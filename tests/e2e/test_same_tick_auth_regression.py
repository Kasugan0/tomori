"""Verify actual Axios instance captures the invoking session synchronously."""

import pytest
from playwright.async_api import async_playwright


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


@pytest.mark.parametrize("next_token", ["token-B", "token-A"])
async def test_same_tick_request_keeps_original_session(frontend_url: str, next_token: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url, wait_until="networkidle")
            result = await page.evaluate(
                """async nextToken => {
              const {apiClient} = await import('/src/shared/api.ts');
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              let captured;
              let finish;
              const original = apiClient.defaults.adapter;
              apiClient.defaults.adapter = config => {
                captured = {authorization: config.headers.get('Authorization'),
                            generation: config.sessionGeneration};
                return new Promise(resolve => { finish = () => resolve({
                  data: {marker: 'old'}, status: 200, statusText: 'OK',
                  headers: {}, config
                }); });
              };
              try {
                auth.clearSession();
                auth.token = 'token-A';
                const generationA = auth.sessionGeneration;
                const pending = apiClient.post('/tickets/123/cancel', {reason: 'test'});
                auth.clearSession();
                auth.token = nextToken;
                const generationB = auth.sessionGeneration;
                await Promise.resolve();
                finish();
                let rejected = false;
                try { await pending; } catch { rejected = true; }
                return {captured, generationA, generationB, rejected, token: auth.token};
              } finally { apiClient.defaults.adapter = original; }
            }""",
                next_token,
            )
            assert result["captured"]["authorization"] == "Bearer token-A"
            assert result["captured"]["generation"] == result["generationA"]
            assert result["generationA"] != result["generationB"]
            assert result["rejected"] and result["token"] == next_token
        finally:
            await browser.close()
