"""Exercise the edited relay with real providers and synthetic microphone audio."""
import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

from probe_provider import load_credentials


def synthetic_audio():
    script = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$m = New-Object System.IO.MemoryStream
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo -ArgumentList 16000,([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen),([System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToAudioStream($m,$f)
$s.Speak('What is two plus two?')
[Convert]::ToBase64String($m.ToArray())
$s.Dispose()
$m.Dispose()
'''
    return base64.b64decode(subprocess.run(['powershell', '-NoProfile', '-Command', script],
        check=True, capture_output=True, text=True).stdout.strip())


class Device:
    def __init__(self, audio):
        self.audio = audio
        self.offset = 0
        self.ended = None
        self.audio_received = 0
        self.first_audio_ms = None
        self.success = False

    async def accept(self):
        pass

    async def close(self, **kwargs):
        pass

    async def receive_json(self):
        return {'type': 'start', 'language': 'en-IN'}

    async def receive(self):
        if self.offset < len(self.audio):
            data = self.audio[self.offset:self.offset + 3200]
            self.offset += len(data)
            await asyncio.sleep(len(data) / 32000)
            return {'type': 'websocket.receive', 'bytes': data}
        self.ended = time.monotonic()
        return {'type': 'websocket.receive', 'text': '{"type":"end"}'}

    async def send_json(self, event):
        if event['type'] != 'partial':
            print(event, flush=True)
        if event['type'] == 'done':
            self.success = True

    async def send_bytes(self, data):
        if self.first_audio_ms is None:
            self.first_audio_ms = round((time.monotonic() - self.ended) * 1000)
            print('First audio after question:', self.first_audio_ms, 'ms', flush=True)
        self.audio_received += len(data)


async def main():
    load_credentials()
    spec = importlib.util.spec_from_file_location('relay', Path(__file__).resolve().parent / 'realtime_relay' / 'app.py')
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    device = Device(synthetic_audio())
    await asyncio.wait_for(relay.voice_ws(device, os.environ['JARVIS_DEVICE_KEY']), 45)
    print('Received', device.audio_received, 'bytes of audio', flush=True)
    assert device.success and device.audio_received > 0


if __name__ == '__main__':
    asyncio.run(main())
