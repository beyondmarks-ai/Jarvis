"""Browser capture regression using simulated audio; requires a running local hub and Playwright."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    token = json.loads(Path('.tmp_phone_access.json').read_text())['JARVIS_MIC_TOKEN']
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True, args=[
            '--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
        # Test-only instrumentation injects synthetic failures; production CSP stays unchanged.
        context = await browser.new_context(permissions=['microphone'], bypass_csp=True)
        await context.add_init_script('''
          window.audioContexts = []; window.audioNodes = []; window.audioTracks = [];
          const OriginalContext = window.AudioContext;
          window.AudioContext = class extends OriginalContext {
            constructor(...args) { super(...args); window.audioContexts.push(this); }
          };
          const OriginalNode = window.AudioWorkletNode;
          window.AudioWorkletNode = class extends OriginalNode {
            constructor(...args) { super(...args); window.audioNodes.push(this); }
          };
          const getMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia = async (...args) => {
            const stream = await getMedia(...args); window.audioTracks.push(...stream.getAudioTracks()); return stream;
          };
        ''')
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:8765/#key=' + token)
        await page.locator('#name').fill('Capture regression check')
        await page.locator('#connectButton').click()
        await page.locator('#connection').filter(has_text='Connected').wait_for()
        await page.wait_for_function("parseInt(document.querySelector('#audioStatus').textContent.match(/frames: (\\d+)/)[1]) > 10")
        await page.wait_for_function("parseFloat(document.querySelector('#level').style.width) > 0", timeout=10000)
        print('Real AudioWorklet delivers frames and moves meter (simulated mic)', flush=True)
        await page.evaluate('audioContexts.at(-1).suspend()')
        await page.locator('#statusDetail').filter(has_text='audio is paused').wait_for()
        await page.locator('#restartButton').click()
        await page.wait_for_function("audioNodes.length === 2 && audioContexts[1].state === 'running'")
        assert await page.evaluate("audioContexts[0].state === 'closed' && audioTracks[0].readyState === 'ended'")
        print('Paused audio identified; restart releases old mic and restores capture', flush=True)
        await page.evaluate("audioNodes.at(-1).port.onmessage = () => {}")
        await page.locator('#statusDetail').filter(has_text='No audio frames received').wait_for(timeout=6000)
        await page.locator('#restartButton').click()
        await page.wait_for_function("audioNodes.length === 3")
        print('Stalled worklet detected and restarted', flush=True)
        await page.evaluate('''() => {
            const node = audioNodes.at(-1), handler = node.port.onmessage;
            node.port.onmessage = event => handler({data:new ArrayBuffer(event.data.byteLength)});
        }''')
        await page.locator('#statusDetail').filter(has_text='Only silence received').wait_for(timeout=10000)
        print('Digital-silence input identified instead of claiming to listen', flush=True)
        await page.locator('#restartButton').click()
        await page.wait_for_function("audioNodes.length === 4")
        await page.evaluate("Object.defineProperty(audioTracks.at(-1), 'muted', {value:true,configurable:true})")
        await page.locator('#statusDetail').filter(has_text='Android muted').wait_for()
        print('Muted track identified', flush=True)
        await page.locator('#stopButton').click()
        assert await page.evaluate("audioTracks.every(t=>t.readyState === 'ended')")
        # Leaving during a pending permission/capture request must not reconnect
        # or leave a microphone running once that request eventually resolves.
        await page.evaluate('''() => {
          const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          const gate = new Promise(resolve => window.releaseCapture = resolve);
          navigator.mediaDevices.getUserMedia = async (...args) => {
            const stream = await original(...args); await gate; return stream;
          };
        }''')
        await page.locator('#connectButton').click()
        await page.wait_for_function("audioTracks.length === 5")
        await page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide')); releaseCapture()")
        await page.wait_for_function("audioTracks.every(t=>t.readyState === 'ended')")
        assert await page.locator('#connection').inner_text() == 'Offline'
        print('Pending-capture cancellation leaves no microphone or connection behind', flush=True)
        assert not errors, errors
        print('All capture recovery checks passed; no page errors or leaked mic tracks', flush=True)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(),100))
