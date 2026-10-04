"""Bounded diagnostic calls using the deployed relay's credentials in memory."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import time

import httpx
import websockets


def load_credentials():
    if os.environ.get('SARVAM_API_KEY') and os.environ.get('JARVIS_DEVICE_KEY'):
        return
    app_name = os.environ.get('JARVIS_AZURE_APP')
    resource_group = os.environ.get('JARVIS_AZURE_RESOURCE_GROUP')
    if not app_name or not resource_group:
        raise RuntimeError('Set SARVAM_API_KEY and JARVIS_DEVICE_KEY, or explicitly set JARVIS_AZURE_APP and JARVIS_AZURE_RESOURCE_GROUP for Azure lookup.')
    if not shutil.which('az'):
        raise RuntimeError('Azure CLI is required for Azure credential lookup.')
    result = subprocess.run(
        [shutil.which('az'), 'containerapp', 'show', '-n', app_name,
         '-g', resource_group, '--query', 'properties.template.containers[0].env', '-o', 'json'],
        capture_output=True, text=True, check=True,
    )
    for item in json.loads(result.stdout):
        if item['name'] in ('SARVAM_API_KEY', 'JARVIS_DEVICE_KEY'):
            if item.get('value'):
                os.environ[item['name']] = item['value']
    if not all(os.environ.get(name) for name in ('SARVAM_API_KEY', 'JARVIS_DEVICE_KEY')):
        raise RuntimeError('Cloud values are secret references or unavailable; supply credentials as environment variables.')


async def main():
    load_credentials()
    headers = {'api-subscription-key': os.environ['SARVAM_API_KEY']}
    async with httpx.AsyncClient(timeout=20) as client:
        started = time.monotonic()
        response = await client.post('https://api.sarvam.ai/v1/chat/completions', headers=headers,
            json={'model': 'sarvam-105b-conversations', 'messages': [
                {'role': 'system', 'content': 'Answer in one short sentence, no more than twelve words.'},
                {'role': 'user', 'content': 'What is two plus two?'}],
                'max_tokens': 384, 'reasoning_effort': 'low', 'temperature': 0.2})
        print('LLM', response.status_code, round(time.monotonic() - started, 3), flush=True)
        response.raise_for_status()
        print(response.json()['choices'], flush=True)

    async with websockets.connect('wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true',
                                  additional_headers=headers, max_size=2**20) as ws:
        started = time.monotonic()
        await ws.send(json.dumps({'type': 'config', 'data': {
            'model': 'bulbul:v3', 'target_language_code': 'en-IN',
            'language_code': 'en-IN', 'speaker': 'shubh', 'pace': 1.0, 'speech_sample_rate': '24000'}}))
        await ws.send(json.dumps({'type': 'text', 'data': {'text': 'Two plus two is four.'}}))
        await ws.send(json.dumps({'type': 'flush'}))
        for _ in range(15):
            try:
                raw = await asyncio.wait_for(ws.recv(), 8)
            except asyncio.TimeoutError:
                print('TTS idle', flush=True)
                break
            message = json.loads(raw)
            if message['type'] == 'audio':
                audio = base64.b64decode(message['data']['audio'])
                print('AUDIO', round(time.monotonic()-started,3), len(audio),
                      message['data'].get('content_type'), audio[:12].hex(), flush=True)
            else:
                print(message, flush=True)
                if message['type'] in ('error', 'event'):
                    break


if __name__ == '__main__':
    asyncio.run(main())
