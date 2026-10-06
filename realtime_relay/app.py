"""Secure WebSocket relay for the Jarvis ESP32.

Protocol (all device audio is raw signed 16-bit, mono, 16 kHz PCM):
  device -> JSON  {"type":"start","language":"unknown"}
  device -> binary PCM chunks while the user speaks
  device -> JSON  {"type":"end"}
  relay  -> JSON  {"type":"transcript","text":"..."}
  relay  -> binary 12 kHz G.711 mu-law chunks for playback
  relay  -> JSON  {"type":"done"}

Sarvam credentials never leave this service. The ESP32 authenticates the
upgrade using X-Jarvis-Key.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import time
from contextlib import suppress

import httpx
import websockets
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status

app = FastAPI()
log = logging.getLogger("uvicorn.error")
STT_FRAME_BYTES = 3200  # 100 ms PCM; below the provider's 32,000-byte cap.
MAX_INPUT_BYTES = 16000 * 2 * 15
SPOKEN_LANGUAGES = {
    "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali",
    "ta-IN": "Tamil", "te-IN": "Telugu", "gu-IN": "Gujarati",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi",
    "pa-IN": "Punjabi", "od-IN": "Odia",
}


def spoken_language(detected: str | None) -> str | None:
    # Realtime STT uses or-IN for Odia; Bulbul uses od-IN.
    base = (detected or "").strip().lower().replace("_", "-").split("-")[0]
    code = ("od" if base == "or" else base) + "-IN"
    return code if code in SPOKEN_LANGUAGES else None

SARVAM_BASE = "https://api.sarvam.ai"
TTS_WS_URL = "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true"
SYSTEM_PROMPT = (
    "You are Jarvis, a fast multilingual voice assistant. Answer in the same "
    "language and script as the user. Give one direct spoken sentence of at "
    "most twelve words. No preamble, markdown, or lists."
)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing {name}")
    return value


def linear_to_ulaw(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), 32635) + 0x84
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not sample & mask:
        exponent -= 1
        mask >>= 1
    return (~(sign | (exponent << 4) | ((sample >> (exponent + 3)) & 0x0F))) & 0xFF


def pcm24k_to_ulaw12k(pcm: bytes) -> bytes:
    # Downsample by two with averaging and compact to G.711 mu-law.
    samples = memoryview(pcm).cast("h")
    return bytes(linear_to_ulaw((samples[i] + samples[i + 1]) // 2)
                 for i in range(0, len(samples) - 1, 2))


async def ask_sarvam(transcript: str, language: str | None = None) -> str:
    prompt = SYSTEM_PROMPT
    if language in SPOKEN_LANGUAGES:
        prompt += (f" The detected spoken language is {SPOKEN_LANGUAGES[language]}."
                   " Reply in that language using its native script, not an English translation."
                   " For mixed-language questions, use that language as the main reply language.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{SARVAM_BASE}/v1/chat/completions",
            headers={"api-subscription-key": require_env("SARVAM_API_KEY")},
            json={
                "model": "sarvam-105b-conversations",
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ],
                "max_tokens": 384,
                "reasoning_effort": "low",
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        answer = (choice["message"].get("content") or "").strip()
        if not answer:
            raise RuntimeError(f"Answer model returned no text (finish_reason={choice.get('finish_reason')})")
        return answer


def stt_frames(audio: bytes):
    if len(audio) % 2:
        raise ValueError("Microphone audio must contain complete 16-bit samples")
    for offset in range(0, len(audio), STT_FRAME_BYTES):
        yield audio[offset:offset + STT_FRAME_BYTES]


async def transcribe_question(audio: bytes) -> tuple[str, str | None]:
    # Realtime auto detection returned English for known Hindi audio in live
    # v3/v4 tests. REST unknown correctly preserves native text and language.
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{SARVAM_BASE}/speech-to-text",
            headers={"api-subscription-key": require_env("SARVAM_API_KEY")},
            data={"model": "saaras:v3", "mode": "transcribe",
                  "language_code": "unknown", "input_audio_codec": "pcm_s16le"},
            files={"file": ("question.pcm", audio, "application/octet-stream")},
        )
        response.raise_for_status()
        result = response.json()
        transcript = (result.get("transcript") or "").strip()
        if not transcript:
            raise ValueError("No speech recognized; please ask again")
        return transcript, result.get("language_code")


async def receive_question(websocket: WebSocket) -> bytes:
    audio = bytearray()
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect()
        if message.get("bytes") is not None:
            chunk = message["bytes"]
            if len(audio) + len(chunk) > MAX_INPUT_BYTES:
                raise ValueError("Question exceeded the 15-second audio limit")
            for frame in stt_frames(chunk):
                audio.extend(frame)
        elif message.get("text"):
            if json.loads(message["text"]).get("type") == "end":
                if not audio:
                    raise ValueError("No microphone audio received")
                return bytes(audio)


async def stream_tts_to_device(device: WebSocket, text: str, language: str, *, speaker: str = "shubh") -> None:
    """Receive Sarvam MP3 WebSocket chunks, decode continuously, send mu-law."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for streaming TTS")
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-probesize", "32",
        "-analyzeduration", "0", "-f", "mp3", "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def forward_pcm() -> None:
        # A 4 KB read is about 85 ms at 24 kHz. Start playback only after the
        # ESP32 has a small jitter buffer, handled on-device.
        carry = b""
        total = 0
        while data := await process.stdout.read(4096):
            data = carry + data
            whole = len(data) // 4 * 4  # Preserve sample pairs across pipe reads.
            carry = data[whole:]
            encoded = pcm24k_to_ulaw12k(data[:whole])
            if encoded:
                await device.send_bytes(encoded)
                total += len(encoded)
        return total

    pcm_task = asyncio.create_task(forward_pcm())
    try:
        async with websockets.connect(
            TTS_WS_URL,
            additional_headers={"Api-Subscription-Key": require_env("SARVAM_API_KEY")},
            max_size=2**20,
        ) as tts:
            await tts.send(json.dumps({
                "type": "config",
                "data": {
                    "model": "bulbul:v3",
                    "target_language_code": language if language else "en-IN",
                    "language_code": language if language else "en-IN",
                    "speaker": speaker,
                    "pace": 1.0,
                    "speech_sample_rate": "24000",
                },
            }))
            await tts.send(json.dumps({"type": "text", "data": {"text": text}}))
            await tts.send(json.dumps({"type": "flush"}))
            got_audio = False
            while True:
                try:
                    raw = await asyncio.wait_for(tts.recv(), timeout=3 if got_audio else 15)
                except asyncio.TimeoutError:
                    if got_audio:
                        break  # Some provider sessions omit the final event.
                    raise RuntimeError("Speech synthesis timed out before producing audio")
                message = json.loads(raw)
                if message["type"] == "audio":
                    got_audio = True
                    process.stdin.write(base64.b64decode(message["data"]["audio"]))
                    await process.stdin.drain()
                elif message["type"] == "event" and message["data"].get("event_type") == "final":
                    break
                elif message["type"] == "error":
                    raise RuntimeError(message["data"].get("message", "Sarvam TTS error"))
        if process.stdin:
            process.stdin.close()
        await asyncio.wait_for(process.wait(), timeout=5)
        total = await asyncio.wait_for(pcm_task, timeout=5)
        if process.returncode != 0 or total == 0:
            raise RuntimeError("Speech decoder produced no playable audio")
    finally:
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            process.kill()
            await process.wait()
        if not pcm_task.done():
            pcm_task.cancel()
        await asyncio.gather(pcm_task, return_exceptions=True)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "realtime-20261004f"}


@app.websocket("/ws")
async def voice_ws(websocket: WebSocket, x_jarvis_key: str | None = Header(default=None)) -> None:
    if x_jarvis_key != require_env("JARVIS_DEVICE_KEY"):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    language = "en-IN"
    started = time.monotonic()
    input_ended = started
    total_input = 0

    try:
        start = await websocket.receive_json()
        if start.get("type") != "start":
            raise ValueError("First message must be start")
        # Older installed firmware always announces en-IN. Auto-detect every
        # question here so language switching needs no device firmware update.
        audio = await asyncio.wait_for(receive_question(websocket), timeout=25)
        total_input = len(audio)
        input_ended = time.monotonic()
        log.info("Jarvis input ended: %d bytes in %.0f ms", total_input,
                 (input_ended - started) * 1000)
        final_text, detected_language = await transcribe_question(audio)
        log.info("Jarvis STT final after end: %.0f ms; input: %d bytes",
                 (time.monotonic() - input_ended) * 1000, total_input)
        language = spoken_language(detected_language)
        log.info("Jarvis detected language: %s; reply voice: %s", detected_language, language)
        await websocket.send_json({"type": "transcript", "text": final_text, "language": detected_language})
        if language:
            answer = await ask_sarvam(final_text, language)
        else:
            # Do not silently translate an unsupported language or send an
            # unsupported code to TTS. Speak the limitation in English.
            language = "en-IN"
            answer = ("Sorry, I cannot speak replies in that language yet."
                      if detected_language else
                      "Sorry, I couldn't identify the language. Please ask a longer question.")
        log.info("Jarvis answer after end: %.0f ms", (time.monotonic() - input_ended) * 1000)
        await websocket.send_json({"type": "answer", "text": answer, "language": language})
        await stream_tts_to_device(websocket, answer, language)
        await websocket.send_json({"type": "done"})
        log.info("Jarvis response complete after end: %.0f ms", (time.monotonic() - input_ended) * 1000)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except Exception as exc:
        log.warning("Jarvis conversation failed (%s): %s; input: %d bytes", type(exc).__name__, str(exc)[:160], total_input)
        with suppress(Exception):
            await websocket.send_json({"type": "error", "message": str(exc)[:160]})
    finally:
        with suppress(Exception):
            await websocket.close()
