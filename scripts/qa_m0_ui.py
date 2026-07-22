from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]

# Pure Vite browser mode has no Tauri IPC; treat those as expected.
_EXPECTED_ERROR_FRAGMENTS = (
    "ipc.localhost",
    "__TAURI",
    "Tauri",
    "sidecar_status",
    "sidecar_request",
    "sidecar-event",
)


def assert_no_horizontal_overflow(page) -> None:  # type: ignore[no-untyped-def]
    overflow = page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert not overflow, "page has horizontal overflow"


def is_expected_browser_only_error(message: str) -> bool:
    return any(fragment in message for fragment in _EXPECTED_ERROR_FRAGMENTS)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: console_errors.append(str(error)))

    page.goto("http://127.0.0.1:1420", wait_until="networkidle")
    page.get_by_role("heading", name="工作流核心台").wait_for()
    page.get_by_role("button", name="发送 Ping").wait_for()
    page.get_by_role("button", name="验证崩溃恢复").wait_for()
    assert_no_horizontal_overflow(page)
    page.screenshot(path=ROOT / "artifacts/m0-ui.png", full_page=True)

    page.set_viewport_size({"width": 800, "height": 900})
    page.reload(wait_until="networkidle")
    assert_no_horizontal_overflow(page)
    page.screenshot(path=ROOT / "artifacts/m0-ui-compact.png", full_page=True)

    unexpected = [error for error in console_errors if not is_expected_browser_only_error(error)]
    assert not unexpected, f"browser console errors: {unexpected}"
    browser.close()
