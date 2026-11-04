import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from src.app import build_app
from src.config import Settings
from tests.fakes import FakeEngine

TOKEN = {"X-Internal-Token": "tok"}
ANN = {"requester": {"username": "ann", "email": "a@x"}}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def port(tmp_path):
    port = free_port()
    settings = Settings(host="127.0.0.1", port=port, data_dir=tmp_path, internal_token="tok", voice="v1")
    app = build_app(settings, engine=FakeEngine())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    yield port
    server.should_exit = True
    thread.join(5)


async def with_session(port: int, headers: dict, work):
    async with create_mcp_http_client(headers=headers, timeout=httpx.Timeout(30)) as http:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await work(session)


async def test_speak_over_mcp_then_fetch_the_file_over_http(port):
    async def work(session):
        names = {t.name for t in (await session.list_tools()).tools}
        result = await session.call_tool(
            "tool_bard_speak", {"text": "Hello there."}, meta={"requester": {"username": "ann", "email": "a@x"}}
        )
        return names, result

    names, result = await with_session(port, TOKEN, work)
    assert names == {"tool_bard_speak", "tool_bard_listVoices"}
    assert not result.isError
    body = json.loads(result.content[0].text)
    assert body["voice"] == "v1" and body["file_id"] and "Speech ready" in body["message"]
    async with httpx.AsyncClient() as http:
        got = await http.get(
            f"http://127.0.0.1:{port}/api/files/{body['file_id']}",
            headers={**TOKEN, "X-Requester-Username": "ann"},
        )
    assert got.status_code == 200 and got.content == b"mp3:Hello there."


async def test_speak_without_a_requester_is_a_tool_error(port):
    async def work(session):
        return await session.call_tool("tool_bard_speak", {"text": "Hello there."})

    result = await with_session(port, TOKEN, work)
    assert result.isError
    assert "signed-in user" in result.content[0].text


async def test_mcp_rejects_a_missing_token(port):
    async with httpx.AsyncClient() as http:
        response = await http.post(f"http://127.0.0.1:{port}/mcp", json={})
    assert response.status_code == 401
