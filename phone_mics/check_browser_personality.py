"""Two-phone mode UI check using synthetic HTTP; no production setting writes."""
import asyncio
import os
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browsers = []
        state = {'mode':'normal', 'fail':False}
        errors = []
        pages = []
        async def api(route):
            assert route.request.headers.get('authorization') == 'Bearer test-mode-key'
            if route.request.method == 'PUT':
                if state['fail']:
                    await route.fulfill(status=503, json={'detail':'Mode save failed'})
                    return
                value = route.request.post_data_json['mode']
                assert value in ('normal', 'genz')
                state['mode'] = value
            await route.fulfill(json={'mode':state['mode'], 'available':['normal','genz'], 'scope':'shared household'})
        for _ in range(2):
            browser = await p.chromium.launch(channel='chrome', headless=True)
            browsers.append(browser)
            page = await browser.new_page(viewport={'width':390,'height':844})
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/mode', api)
            await page.goto(os.getenv('JARVIS_TEST_URL','http://127.0.0.1:8765'))
            await page.locator('#token').fill('test-mode-key')
            await page.locator('#token').dispatch_event('change')
            await page.locator('#modeCurrent').filter(has_text='Normal').wait_for()
            pages.append(page)
        first, second = pages
        assert await first.locator('.mode-buttons button').count() == 2
        await first.locator('#modeGenz').click()
        await first.locator('#modeCurrent').filter(has_text='Gen Z').wait_for()
        await second.locator('#modeCurrent').filter(has_text='Gen Z').wait_for(timeout=20000)
        assert await first.locator('#modeGenz').get_attribute('aria-pressed') == 'true'
        assert await first.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await first.locator('#modeTitle').scroll_into_view_if_needed()
        await first.screenshot(path='.tmp_phone_modes.png')
        state['fail'] = True
        await first.locator('#modeNormal').click()
        await first.locator('#modeStatus').filter(has_text='Mode save failed').wait_for()
        assert state['mode'] == 'genz'
        assert await first.locator('#modeNormal').get_attribute('aria-pressed') == 'false'
        state['fail'] = False
        await first.locator('#token').dispatch_event('change')
        await first.locator('#modeCurrent').filter(has_text='Gen Z').wait_for()
        await first.locator('#modeNormal').click()
        await first.locator('#modeCurrent').filter(has_text='Normal').wait_for()
        assert state['mode'] == 'normal' and not errors, errors
        print('PASS exactly two modes, authenticated switching, cross-phone refresh, failure honesty, mobile layout')
        for browser in browsers:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
