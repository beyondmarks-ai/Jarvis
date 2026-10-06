"""Voice-selector UI regression with synthetic HTTP; never changes live preferences."""
import asyncio
import os
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width':390, 'height':844})
        state = {'voice':'male', 'fail':False}
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        async def voice_api(route):
            assert route.request.headers.get('authorization') == 'Bearer voice-test-key'
            if route.request.method == 'PUT':
                if state['fail']:
                    await route.fulfill(status=503, json={'detail':'Voice save failed'})
                    return
                state['voice'] = route.request.post_data_json['voice']
                assert state['voice'] in ('male', 'female')
            await route.fulfill(json={'voice':state['voice'], 'available':['male','female']})
        await page.route('**/api/voice', voice_api)
        await page.route('**/api/mode', lambda route: route.fulfill(json={'mode':'normal', 'available':['normal','genz']}))
        await page.goto(os.getenv('JARVIS_TEST_URL','http://127.0.0.1:8765'))
        await page.locator('#token').fill('voice-test-key')
        await page.locator('#token').dispatch_event('change')
        await page.locator('#voiceCurrent').filter(has_text='Male').wait_for()
        assert await page.locator('.voice-buttons button').count() == 2
        await page.locator('#voiceFemale').click()
        await page.locator('#voiceCurrent').filter(has_text='Female').wait_for()
        assert await page.locator('#voiceFemale').get_attribute('aria-pressed') == 'true'
        assert await page.locator('#modeCurrent').inner_text() == 'Normal'
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.locator('#voiceTitle').scroll_into_view_if_needed()
        await page.screenshot(path='.tmp_phone_voices.png')
        await page.reload()
        await page.locator('#token').fill('voice-test-key')
        await page.locator('#token').dispatch_event('change')
        await page.locator('#voiceCurrent').filter(has_text='Female').wait_for()
        state['fail'] = True
        await page.locator('#voiceMale').click()
        await page.locator('#voiceStatus').filter(has_text='Voice save failed').wait_for()
        assert state['voice'] == 'female'
        state['fail'] = False
        await page.locator('#token').dispatch_event('change')
        await page.locator('#voiceCurrent').filter(has_text='Female').wait_for()
        await page.locator('#voiceMale').click()
        await page.locator('#voiceCurrent').filter(has_text='Male').wait_for()
        assert state['voice'] == 'male' and not errors, errors
        print('PASS voice selector, reload, failed-save honesty, unchanged mode, mobile layout')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
