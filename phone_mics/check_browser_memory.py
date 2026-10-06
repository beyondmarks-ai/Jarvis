"""Memory-panel browser regression with synthetic HTTP fixtures, no cloud writes."""
import asyncio
import os
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width':390,'height':844})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        facts = []
        async def api(route):
            request = route.request
            assert request.headers.get('authorization') == 'Bearer browser-test-key'
            if request.method == 'POST':
                assert request.post_data_json['text'] == 'Remember a test fact'
                facts.append({'id':'a'*64, 'text':'<img src=x onerror=alert(1)> is plain saved text',
                              'updated_at':'2026-10-05T12:00:00Z', '_etag':'test-etag'})
                await route.fulfill(json={'answer':'Saved the test fact.', 'action':'remember'})
            elif request.method == 'DELETE':
                assert request.headers.get('if-match') == 'test-etag'
                facts.clear()
                await route.fulfill(json={'deleted':True})
            else:
                await route.fulfill(json={'scope':'shared household','facts':facts})
        await page.route('**/api/memory**', api)
        await page.goto(os.getenv('JARVIS_TEST_URL','http://127.0.0.1:8765'))
        await page.locator('#memoryRefresh').click()
        await page.locator('#memoryAnswer').filter(has_text='access key above first').wait_for()
        await page.locator('#token').fill('browser-test-key')
        await page.locator('#memoryText').fill('Remember a test fact')
        await page.locator('#memorySend').click()
        await page.locator('#memoryAnswer').filter(has_text='Saved the test fact').wait_for()
        await page.locator('#memoryFacts button').wait_for()
        assert await page.locator('#memoryFacts img').count() == 0
        assert '<img' in await page.locator('#memoryFacts').inner_text()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path='.tmp_phone_memory_mobile.png', full_page=True)
        async def dismiss(dialog):
            await dialog.dismiss()
        page.once('dialog', dismiss)
        await page.locator('#memoryFacts button').click()
        assert len(facts) == 1
        async def accept(dialog):
            await dialog.accept()
        page.once('dialog', accept)
        await page.locator('#memoryFacts button').click()
        await page.locator('#memoryFacts').filter(has_text='No saved facts').wait_for()
        assert not facts and not errors, errors
        print('PASS memory form, authentication, safe text rendering, mobile layout, delete cancellation/confirmation')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
