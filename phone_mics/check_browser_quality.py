"""Silent distance-test UI regression; requires local hub, Chrome and Playwright."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    token=json.loads(Path('.tmp_phone_access.json').read_text())['JARVIS_MIC_TOKEN']
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True,args=[
            '--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream',
            '--disable-background-timer-throttling','--disable-renderer-backgrounding'])
        pages=[]; errors=[]
        for name in ('Quality test','Other room'):
            context=await browser.new_context(permissions=['microphone'],viewport={'width':390,'height':844})
            page=await context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto(os.getenv('JARVIS_TEST_URL','http://127.0.0.1:8765').rstrip('/')+'/#key='+token)
            await page.locator('#name').fill(name)
            await page.locator('#connectButton').click()
            await page.locator('#connection').filter(has_text='Connected').wait_for()
            pages.append(page)
        owner,other=pages
        await owner.wait_for_function("() => !document.querySelector('#testStart').disabled")
        await owner.locator('#testStart').click()
        await owner.locator('#testPrompt').filter(has_text='Room-noise check').wait_for()
        await other.locator('#testPrompt').filter(has_text='Another phone is testing').wait_for()
        print('Silent test started; other phone paused',flush=True)
        await owner.locator('#testPrompt').filter(has_text='Attempt 1').wait_for(timeout=8000)
        await owner.locator('#testResult').filter(has_text='attempts detected at 5 m').wait_for(timeout=22000)
        result=await owner.locator('#testResult').inner_text()
        print('Completed 20-second check:',result,flush=True)
        assert 'Incomplete audio' not in result
        await owner.locator('summary').click()
        assert 'Hub received' in await owner.locator('#hubStatus').inner_text()
        assert not await owner.locator('#copyTest').is_hidden()
        await owner.locator('#testStart').click()
        await owner.locator('#testCancel').click()
        await owner.locator('#testResult').filter(has_text='Test cancelled').wait_for()
        await other.locator('#testStart').wait_for(state='visible')
        assert await owner.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await owner.screenshot(path='.tmp_phone_v4_quality.png',full_page=True)
        for page in pages:
            await page.locator('#stopButton').click()
        assert not errors,errors
        print('Distance completion/cancellation, diagnostics, mobile layout and cleanup passed',flush=True)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(),100))
