import asyncio
import struct
import unittest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from phone_mics.app import create_app
from phone_mics.hub import Hub, Microphone, Speaker


class Detector:
    epoch = -1
    def score(self, pcm, epoch):
        return 1. if pcm[0] else 0.


class Socket:
    def __init__(self, hub=None):
        self.events = []
        self.chunks = []
        self.hub = hub
    async def send_json(self, event):
        self.events.append(event)
        if self.hub and event["type"] in ("begin", "end"):
            await self.hub.speaker_event({"type": "beep_done" if event["type"] == "begin" else "played", "turn": event["turn"]})
    async def send_bytes(self, data):
        self.chunks.append(data)


def packet(epoch, voiced=False, wake=False):
    pcm = struct.pack("<h", 1501 if (voiced or wake) else 0)*1280
    return epoch.to_bytes(4, "little") + pcm


async def until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(.01)
    raise AssertionError("Timed out waiting for state")


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.answers = []
        async def reply(audio, output):
            self.answers.append(audio)
            await output(b"\xff"*1200)
        self.hub = Hub(reply)
        self.speaker = Socket(self.hub)
        self.hub.speaker = Speaker(self.speaker)
        self.peers = [Microphone(Socket(), Detector(), f"Phone {i}") for i in range(3)]
        for peer in self.peers:
            await self.hub.add(peer)

    async def asyncTearDown(self):
        await self.hub.close()

    async def finish_question(self, peer):
        await until(lambda: self.hub.phase == "recording")
        for i in range(14):
            peer.next_frame = 0  # Unit-test clock; production rate limiting tested separately.
            await self.hub.packet(peer, packet(self.hub.epoch, voiced=i<4))
        await until(lambda: self.hub.phase == "idle")

    async def test_three_simultaneous_wakes_produce_one_beep_and_one_reply(self):
        epoch = self.hub.epoch
        await asyncio.gather(*(self.hub.packet(p, packet(epoch, wake=True)) for p in self.peers))
        await until(lambda: self.hub.phase == "recording")
        winner = self.hub.mics[self.hub.owner]
        loser = next(p for p in self.peers if p is not winner)
        await self.hub.packet(loser, packet(self.hub.epoch, voiced=True))
        self.assertEqual(len(self.hub.audio), 0)
        await self.finish_question(winner)
        self.assertEqual(len(self.answers), 1)
        self.assertEqual(len(self.answers[0]), 14*2560)
        self.assertEqual(sum(e["type"] == "begin" for e in self.speaker.events), 1)
        self.assertEqual(len(self.speaker.chunks), 1)
        self.assertIsNone(self.hub.owner)
        # It really listens again after the reply, not just after a reboot.
        await self.hub.packet(loser, packet(self.hub.epoch, wake=True))
        await self.finish_question(loser)
        self.assertEqual(len(self.answers), 2)

    async def test_stale_audio_does_not_trigger_new_turn(self):
        await self.hub.packet(self.peers[0], packet(self.hub.epoch-1 & 0xffffffff, wake=True))
        self.assertEqual(self.hub.phase, "idle")

    async def test_no_speaker_means_no_wake(self):
        self.hub.speaker = None
        await self.hub.packet(self.peers[0], packet(self.hub.epoch, wake=True))
        self.assertEqual(self.hub.phase, "idle")

    async def test_disconnect_releases_conversation_lock(self):
        peer = self.peers[0]
        await self.hub.packet(peer, packet(self.hub.epoch, wake=True))
        await until(lambda: self.hub.phase == "recording")
        await self.hub.remove(peer)
        self.assertEqual(self.hub.phase, "idle")
        self.assertIsNone(self.hub.owner)
        self.assertEqual(len(self.answers), 0)

    async def test_bad_frame_and_rate_limit(self):
        with self.assertRaises(ValueError):
            await self.hub.packet(self.peers[0], b"bad")
        self.hub.speaker = None
        with self.assertRaises(ValueError):
            for _ in range(30):
                await self.hub.packet(self.peers[0], packet(self.hub.epoch))

    async def test_fourth_phone_rejected(self):
        with self.assertRaises(ValueError):
            await self.hub.add(Microphone(Socket(), Detector(), "Fourth"))

    async def test_wrong_turn_ack_is_ignored(self):
        await self.hub.speaker_event({"type":"beep_done", "turn":"wrong"})
        self.assertFalse(self.hub.beep_done.is_set())

    async def test_cancel_before_conversation_task_starts(self):
        # Synchronous ownership followed immediately by a disconnect, before task body runs.
        self.hub.phase = "beeping"
        self.hub.owner = self.peers[0].id
        self.hub.turn = "a"*32
        self.hub.task = asyncio.create_task(self.hub.conversation(self.peers[0], self.hub.speaker, self.hub.turn))
        await self.hub.remove(self.peers[0])
        self.assertEqual(self.hub.phase, "idle")
        self.assertIsNone(self.hub.owner)

    async def test_silence_and_provider_failure_both_resume_listening(self):
        peer = self.peers[0]
        await self.hub.packet(peer, packet(self.hub.epoch, wake=True))
        await until(lambda: self.hub.phase == "recording")
        for _ in range(50):
            peer.next_frame = 0
            await self.hub.packet(peer, packet(self.hub.epoch))
        await until(lambda: self.hub.phase == "idle")
        self.assertEqual(self.answers, [])
        self.hub.reply = AsyncMock(side_effect=RuntimeError("private-provider-details"))
        peer.next_frame = 0
        await self.hub.packet(peer, packet(self.hub.epoch, wake=True))
        await self.finish_question(peer)
        notices = [e.get("message", "") for e in peer.socket.events if e["type"] == "notice"]
        self.assertTrue(notices)
        self.assertNotIn("private-provider-details", " ".join(notices))


class AppTests(unittest.TestCase):
    def client(self):
        return TestClient(create_app(detector_factory=Detector, reply=AsyncMock(),
                                    mic_token="test-mic", speaker_token="test-speaker"))

    def test_static_app_and_security_headers(self):
        with self.client() as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Your phone.", page.text)
            self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
            self.assertEqual(client.get("/static/audio.js").status_code, 200)
            self.assertEqual(client.get("/health").json()["status"], "ok")

    def test_bad_key_and_cross_origin_denied(self):
        with self.client() as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws/mic") as ws:
                    ws.send_json({"token":"bad", "name":"Phone"})
                    ws.receive_json()
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws/mic", headers={"origin":"https://evil.example"}):
                    pass
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws/speaker", headers={"x-jarvis-speaker-key":"test-mic"}):
                    pass

    def test_microphone_auth_and_disconnect(self):
        with self.client() as client:
            with client.websocket_connect("/ws/mic") as ws:
                ws.send_json({"token":"test-mic", "name":"Bedroom"})
                self.assertEqual(ws.receive_json()["type"], "welcome")
                self.assertEqual(ws.receive_json()["type"], "state")
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_text("ping")
                self.assertEqual(ws.receive_json()["type"], "pong")


if __name__ == "__main__":
    unittest.main()
