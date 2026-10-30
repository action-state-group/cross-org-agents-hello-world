"""The browser half of hop 3: open the verify pages in headless Chromium, with the AAC
TypeScript verifier loaded from jsDelivr exactly as an attendee's browser loads it.

Needs Playwright and its Chromium (`uv run --group browser playwright install chromium`);
skipped otherwise, unless HELLO_REQUIRE_BROWSER=1 (set in CI), where a missing browser fails."""

import os

import pytest
from test_hops import book, links, services

REQUIRED = os.environ.get("HELLO_REQUIRE_BROWSER") == "1"
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # Chromium not installed
            if REQUIRED:
                raise
            pytest.skip(f"no Chromium for Playwright: {e}")
        yield b
        b.close()


def verdict(page) -> str:
    page.wait_for_function("!document.getElementById('verdict').textContent.startsWith('Loading')", timeout=30000)
    return page.locator("#verdict").inner_text()


def test_hop3_verifies_and_one_flipped_byte_fails(tmp_path, browser):
    with services(tmp_path) as (env, a, b):
        receipt, confirmation = links(book(a))
        page = browser.new_page()

        page.goto(receipt)
        assert verdict(page).startswith("✓ Verified")
        assert "One-sided" in page.locator("#note").inner_text()

        page.goto(confirmation)
        assert verdict(page).startswith("✓ Verified")
        assert "both sides report the same payment (settled)" in page.locator("#settlement").inner_text()
        assert page.locator("#addhalf").is_hidden()

        page.click("#tamper")
        page.wait_for_function("document.getElementById('verdict').textContent.startsWith('✗')")
        assert "capsule does not match its ID" in page.locator("#rows").inner_text()
        page.click("#tamper")  # a second click still leaves the record changed
        page.wait_for_timeout(500)
        assert verdict(page).startswith("✗")
        page.click("#reset")
        page.wait_for_function("document.getElementById('verdict').textContent.startsWith('✓')")


def test_fallback_and_missing_set(tmp_path, browser):
    with services(tmp_path, payment="none") as (env, a, b):
        page = browser.new_page()
        page.goto(links(book(a))[1])
        assert verdict(page).startswith("✓ Verified")
        assert "no payment is recorded" in page.locator("#settlement").inner_text()
        page.goto(f"{b}/verify#set=nope")
        page.reload()
        assert "Could not check this record" in verdict(page)
