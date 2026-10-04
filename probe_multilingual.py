"""Synthetic-language integration tests; credentials stay in memory."""
import argparse
import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import sys
import time
import httpx
from pathlib import Path

import websockets
import yaml

from probe_pipeline import Device
from probe_provider import load_credentials

QUESTIONS = {
    "en-IN": "What is the capital city of India?",
    "hi-IN": "भारत की राजधानी का नाम क्या है?",
    "ta-IN": "இந்தியாவின் தலைநகரம் எது என்று சொல்லுங்கள்?",
    # Use language-distinctive words: the short capital-city answer contains
    # mostly shared Sanskrit/place names, making a second auto-LID ambiguous.
    "te-IN": "మీరు నాకు ఎలా సహాయం చేయగలరో చెప్పండి.",
    "bn-IN": "ভারতের রাজধানীর নাম কী বলুন?",
    "gu-IN": "ભારતની રાજધાનીનું નામ શું છે?",
    "kn-IN": "ಭಾರತದ ರಾಜಧಾನಿಯ ಹೆಸರು ಏನು ಹೇಳಿ?",
    "ml-IN": "ഇന്ത്യയുടെ തലസ്ഥാനത്തിന്റെ പേര് എന്താണ്?",
    "mr-IN": "भारताच्या राजधानीचे नाव काय आहे?",
    "pa-IN": "ਭਾਰਤ ਦੀ ਰਾਜਧਾਨੀ ਦਾ ਨਾਮ ਕੀ ਹੈ?",
    "od-IN": "ଭାରତର ରାଜଧାନୀର ନାମ କଣ?",
}


async def question_audio(language, text):
    parts = []
    async with websockets.connect(
        "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true",
        additional_headers={"api-subscription-key": os.environ["SARVAM_API_KEY"]},
        max_size=2**20,
    ) as ws:
        await ws.send(json.dumps({"type": "config", "data": {
            "model": "bulbul:v3", "target_language_code": language,
            "language_code": language,
            "speaker": "shubh", "pace": 1.0, "speech_sample_rate": "24000",
        }}))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if message["type"] == "audio":
                parts.append(base64.b64decode(message["data"]["audio"]))
            elif message["type"] == "event" and message["data"].get("event_type") == "final":
                break
            elif message["type"] == "error":
                raise RuntimeError(message["data"])
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "mp3", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        input=b"".join(parts), capture_output=True, check=True,
    )
    return decoded.stdout


class TestDevice(Device):
    def __init__(self, audio):
        super().__init__(audio)
        self.events = []
        self.reply_audio = bytearray()

    async def send_json(self, event):
        self.events.append(event)
        await super().send_json(event)

    async def send_bytes(self, data):
        self.reply_audio.extend(data)
        await super().send_bytes(data)


async def remote(device):
    with open(Path(__file__).resolve().parent / "secrets.yaml", encoding="utf-8") as stream:
        settings = yaml.safe_load(stream)
    async with websockets.connect(
        "wss://" + settings["jarvis_realtime_host"] + "/ws",
        additional_headers={"X-Jarvis-Key": settings["jarvis_backend_key"]},
    ) as ws:
        await ws.send(json.dumps(await device.receive_json()))
        while True:
            message = await device.receive()
            await ws.send(message.get("bytes") or message["text"])
            if "text" in message:
                break
        async for message in ws:
            if isinstance(message, bytes):
                await device.send_bytes(message)
            else:
                event = json.loads(message)
                await device.send_json(event)
                if event["type"] in ("done", "error"):
                    break


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployed", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--verify-spoken", action="store_true")
    parser.add_argument("--languages", nargs="+", default=["en-IN", "hi-IN", "ta-IN", "te-IN"])
    args = parser.parse_args()
    load_credentials()
    spec = importlib.util.spec_from_file_location("relay", Path(__file__).resolve().parent / "realtime_relay" / "app.py")
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    for language in args.languages:
        print("Testing", language, flush=True)
        audio = await question_audio(language, QUESTIONS[language])
        if args.audit:
            async with httpx.AsyncClient(timeout=30) as client:
                result = await client.post("https://api.sarvam.ai/speech-to-text",
                    headers={"api-subscription-key": os.environ["SARVAM_API_KEY"]},
                    data={"model": "saaras:v3", "mode": "transcribe", "language_code": "unknown", "input_audio_codec": "pcm_s16le"},
                    files={"file": ("question.pcm", audio, "application/octet-stream")})
                print("Independent STT:", result.status_code, result.text, flush=True)
        device = TestDevice(audio)
        await asyncio.wait_for(remote(device) if args.deployed else
                               relay.voice_ws(device, os.environ["JARVIS_DEVICE_KEY"]), 45)
        answers = [event for event in device.events if event["type"] == "answer"]
        assert device.success and device.audio_received > 0, device.events
        assert answers and answers[0]["language"] == language, device.events
        if args.verify_spoken:
            pcm = subprocess.run(["ffmpeg", "-v", "error", "-f", "mulaw", "-ar", "12000", "-ac", "1", "-i", "pipe:0",
                                  "-f", "s16le", "-ar", "16000", "pipe:1"],
                                 input=bytes(device.reply_audio), capture_output=True, check=True).stdout
            spoken_text, spoken_code = await relay.transcribe_question(pcm)
            print("Spoken reply:", spoken_code, spoken_text, flush=True)
            assert relay.spoken_language(spoken_code) == language, (spoken_code, spoken_text)
        print("PASS", language, device.audio_received, "audio bytes", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
