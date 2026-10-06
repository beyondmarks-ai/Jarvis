"""Authenticated microphone arbitration; all audible output belongs to one speaker."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import math
import logging
import time
import uuid
import numpy as np
from .diagnostics import AudioHealth, DistanceTest

FRAME_BYTES = 2560  # 80 ms, signed little-endian 16 kHz mono PCM
log = logging.getLogger('uvicorn.error')


@dataclass
class Microphone:
    socket: object
    detector: object
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_packet: float = 0.
    next_frame: float = 0.
    health: AudioHealth = field(default_factory=AudioHealth)
    health_epoch: int = -1

    async def send(self, event):
        async with self.write_lock:
            await asyncio.wait_for(self.socket.send_json(event), 2)


class Speaker:
    def __init__(self, socket):
        self.socket = socket
        self.lock = asyncio.Lock()

    async def send(self, event):
        async with self.lock:
            await asyncio.wait_for(self.socket.send_json(event), 3)

    async def audio(self, data):
        async with self.lock:
            for offset in range(0, len(data), 4096):
                await asyncio.wait_for(self.socket.send_bytes(data[offset:offset+4096]), 5)


class Hub:
    def __init__(self, reply, threshold=.5, max_mics=3, speech_rms=500):
        if not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("Wake threshold must be greater than 0 and at most 1")
        if not 1 <= max_mics <= 8 or not 1 <= speech_rms <= 32767:
            raise ValueError("Invalid microphone limit or speech level")
        self.reply = reply
        self.threshold = threshold
        self.max_mics = max_mics
        self.speech_rms = speech_rms
        self.mics = {}
        self.speaker = None
        self.phase = "idle"
        self.epoch = 0
        self.owner = None
        self.turn = None
        self.audio = bytearray()
        self.heard_speech = False
        self.silent_frames = 0
        self.recording_frames = 0
        self.task = None
        self.beep_done = asyncio.Event()
        self.played = asyncio.Event()
        self.question_done = asyncio.Event()
        self.error = None
        self.last_activity = time.monotonic()
        self.distance_test = None

    def snapshot(self):
        return {"type": "state", "phase": self.phase, "epoch": self.epoch,
                "owner": self.owner, "turn": self.turn,
                "speaker_connected": self.speaker is not None,
                "microphones": [{"id": p.id, "name": p.name} for p in self.mics.values()]}

    def metrics(self, peer):
        result = peer.health.snapshot(self.threshold)
        if self.distance_test and self.owner == peer.id:
            result["test_elapsed"] = round(time.monotonic()-self.distance_test.started, 1)
        return result

    async def start_test(self, peer, distance):
        if distance not in (1, 3, 5) or self.phase != "idle":
            await self.safe_send(peer, {"type":"notice", "message":"Wait until Jarvis is idle before starting a distance test."})
            return
        # Reserve before any await: another microphone cannot start a reply mid-test.
        self.distance_test = DistanceTest(distance, self.threshold)
        self.phase, self.owner = "testing", peer.id
        self.epoch += 1
        self.task = asyncio.create_task(self.run_test(peer, self.distance_test))

    async def run_test(self, peer, test):
        try:
            await self.broadcast()
            await asyncio.sleep(20)
            await self.safe_send(peer, test.report())
        except asyncio.CancelledError:
            await self.safe_send(peer, {"type":"test_result", "cancelled":True})
        finally:
            if self.distance_test is test:
                self.distance_test = None
                self.owner = self.turn = None
                await self.transition("idle")

    async def broadcast(self):
        event = self.snapshot()
        await asyncio.gather(*(self.safe_send(p, event) for p in list(self.mics.values())))

    async def safe_send(self, peer, event):
        with suppress(Exception):
            await peer.send(event)

    async def transition(self, phase):
        self.phase = phase
        self.epoch += 1
        self.last_activity = time.monotonic()
        await self.broadcast()

    async def add(self, peer):
        if len(self.mics) >= self.max_mics:
            raise ValueError("All microphone slots are occupied")
        self.mics[peer.id] = peer
        await peer.send({"type": "welcome", "id": peer.id})
        await self.broadcast()

    async def remove(self, peer):
        self.mics.pop(peer.id, None)
        if self.owner == peer.id and self.task and not self.task.done():
            await self.close()
        await self.broadcast()

    async def packet(self, peer, packet):
        if len(packet) != FRAME_BYTES + 4:
            raise ValueError("Expected epoch + exactly 80 ms of 16 kHz mono PCM")
        epoch = int.from_bytes(packet[:4], "little")
        now = time.monotonic()
        # Permit a small network burst; reject prerecorded/flooded streams.
        peer.next_frame = max(peer.next_frame, now) + .08
        if peer.next_frame - now > 1.6:
            raise ValueError("Microphone is sending faster than real time")
        if now - peer.last_packet > .8:
            peer.detector.epoch = -1
        peer.last_packet = now
        if epoch != self.epoch:
            return
        pcm = packet[4:]
        if peer.health_epoch != epoch:
            peer.health.last_at = 0.  # A planned turn pause is not a network dropout.
            peer.health_epoch = epoch
        peer.health.audio(pcm, now)
        testing = self.phase == "testing" and self.owner == peer.id
        if self.phase == "idle" or testing:
            if not self.speaker and not testing:
                return
            started = time.monotonic()
            score = await asyncio.to_thread(peer.detector.score, pcm, epoch)
            peer.health.prediction(score, time.monotonic()-started)
            if epoch != self.epoch:
                return
            if testing and self.phase == "testing" and self.distance_test:
                self.distance_test.observe(peer.health, time.monotonic())
                return
            if self.phase != "idle":
                return
            if math.isfinite(score) and score >= self.threshold:
                # No await before claiming ownership: simultaneous phones cannot both win.
                self.owner, self.turn = peer.id, uuid.uuid4().hex
                self.phase = "beeping"
                self.epoch += 1
                self.error = None
                self.audio.clear()
                self.beep_done.clear()
                self.played.clear()
                self.question_done.clear()
                self.task = asyncio.create_task(self.conversation(peer, self.speaker, self.turn))
        elif self.phase == "recording" and peer.id == self.owner:
            self.audio.extend(pcm)
            self.recording_frames += 1
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
            voiced = float(np.sqrt(np.mean(samples*samples))) >= self.speech_rms
            self.heard_speech |= voiced
            self.silent_frames = 0 if voiced else self.silent_frames + 1
            if (self.recording_frames >= 150 or
                (self.heard_speech and self.recording_frames >= 10 and self.silent_frames >= 10) or
                (not self.heard_speech and self.recording_frames >= 50)):
                self.question_done.set()
                await self.transition("thinking")

    async def speaker_event(self, event):
        if event.get("turn") != self.turn or self.turn is None:
            return
        kind = event.get("type")
        if kind == "beep_done" and self.phase == "beeping":
            self.beep_done.set()
        elif kind == "played" and self.phase == "speaking":
            self.played.set()
        elif kind == "error":
            self.error = "IoT speaker reported an error"
            await self.close()

    async def conversation(self, peer, speaker, turn):
        try:
            await self.broadcast()
            await speaker.send({"type": "begin", "turn": turn})
            await asyncio.wait_for(self.beep_done.wait(), 3)
            self.heard_speech = False
            self.silent_frames = self.recording_frames = 0
            await self.transition("recording")
            await asyncio.wait_for(self.question_done.wait(), 15)
            if not self.heard_speech:
                raise ValueError("No question heard; say Hey Jarvis again")
            audio = bytes(self.audio)
            self.audio.clear()
            sent = 0
            delivery_started = last_chunk_at = 0.
            max_source_gap = max_send_wait = 0.
            first_chunk = chunks = 0

            async def output(chunk):
                nonlocal sent, delivery_started, last_chunk_at, max_source_gap, max_send_wait, first_chunk, chunks
                now = time.monotonic()
                if not delivery_started:
                    delivery_started = now
                    first_chunk = len(chunk)
                if last_chunk_at:
                    max_source_gap = max(max_source_gap, now-last_chunk_at)
                last_chunk_at = now
                chunks += 1
                if self.speaker is not speaker:
                    raise ConnectionError("IoT disconnected")
                if self.phase != "speaking":
                    await self.transition("speaking")
                sent += len(chunk)
                if sent > 1024*1024:
                    raise ValueError("Reply audio limit reached")
                await speaker.audio(chunk)
                max_send_wait = max(max_send_wait, time.monotonic()-now)

            await asyncio.wait_for(self.reply(audio, output), 60)
            if not sent:
                raise ValueError("Answer service returned no audio")
            # Aggregate transport timings only. Never log speech, facts, audio, or keys.
            log.info('Speaker delivery: bytes=%d audio_ms=%.0f stream_ms=%.0f chunks=%d first_bytes=%d source_gap_ms=%.0f send_wait_ms=%.0f',
                     sent, sent/12, (last_chunk_at-delivery_started)*1000, chunks, first_chunk,
                     max_source_gap*1000, max_send_wait*1000)
            await speaker.send({"type": "end", "turn": turn})
            await asyncio.wait_for(self.played.wait(), 15)
        except asyncio.CancelledError:
            self.error = self.error or "Conversation cancelled or a device disconnected"
        except asyncio.TimeoutError:
            self.error = "A device or answer service timed out; try again"
        except Exception:
            # Do not leak provider credentials, URLs or raw exceptions to clients.
            self.error = "Could not finish this question; check the hub configuration and connections"
        finally:
            with suppress(Exception):
                await speaker.send({"type": "cancel", "turn": turn})
            if self.error:
                await self.safe_send(peer, {"type": "notice", "message": self.error})
            self.audio.clear()
            self.owner = self.turn = None
            await self.transition("cooldown")
            await asyncio.sleep(.8)
            await self.transition("idle")

    async def close(self):
        task = self.task
        if task and not task.done():
            if not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # A task cancelled before its first instruction never enters its finally block.
        # Do not leave the hub stuck after a wake/disconnect race, or reset a newer turn.
        if self.task is task and self.phase != "idle":
            self.distance_test = None
            self.audio.clear()
            self.owner = self.turn = None
            await self.transition("idle")
