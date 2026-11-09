"""Playground browser checks through real public routes and a fake speech engine.

Run with ``uv run pytest tests/test_admin_ui.py`` after installing Playwright and Chromium.
"""

import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")

from src.app import build_app
from src.config import Settings
from tests.fakes import FakeEngine
from tests.seed import seed_token

TOKEN = "playground-service-token"
PASSWORD = "playground-admin-password"


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def base_url(tmp_path, engine):
    app = build_app(seed_token(Settings(data_dir=tmp_path, admin_password=PASSWORD, voice="v1"), TOKEN), engine)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "Fake-engine HTTP server did not start"
        yield f"http://127.0.0.1:{port}/admin/"
    finally:
        server.should_exit = True
        thread.join(5)


@pytest.fixture
def page(base_url):
    with sync_api.sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(base_url)
            page.fill("#password", PASSWORD)
            page.click("#signin-button")
            page.click('[data-page="playground"]')
            sync_api.expect(page.locator("#pg-token")).to_be_visible()
            yield page
        finally:
            browser.close()


def test_playground_speaks_and_downloads_owned_audio(page, engine):
    page.fill("#pg-token", TOKEN)
    page.click("#pg-load")
    sync_api.expect(page.locator("#pg-voice option[value=v1]")).to_have_count(1)
    page.select_option("#pg-voice", "v1")
    page.fill("#pg-text", "Hello browser")
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("Complete")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 200")
    audio = page.locator("#pg-media audio")
    sync_api.expect(audio).to_have_attribute("src", re.compile(r"^blob:"))
    assert page.evaluate("async () => await (await fetch(document.querySelector('#pg-media audio').src)).text()") == "mp3:Hello browser"
    assert engine.calls == [("Hello browser", "v1")]
    assert page.locator("#pg-media a").get_attribute("download") == "playground.mp3"
    curl = page.locator("#pg-curl").text_content()
    assert TOKEN not in curl and PASSWORD not in curl and "<SERVICE_TOKEN>" in curl
    assert "/api/speak" in curl and "/api/files/" in curl
    assert TOKEN not in page.evaluate("JSON.stringify([Object.values(sessionStorage), Object.values(localStorage)])")
    page.click("#signout")
    assert page.locator("#pg-token").input_value() == ""


def test_playground_reports_public_auth_failure(page):
    page.fill("#pg-token", "wrong-token")
    page.fill("#pg-text", "Hello browser")
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 401")
    sync_api.expect(page.locator("#pg-output")).to_contain_text("Invalid or missing token")
    assert page.locator("#panel").is_visible()
