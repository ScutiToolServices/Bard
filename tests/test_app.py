import pytest
from starlette.testclient import TestClient

from src.app import build_app
from src.config import Settings
from src.store import AudioStore
from tests.fakes import FakeEngine

TOKEN = {"X-Internal-Token": "tok"}
BOB = {**TOKEN, "X-Requester-Username": "bob"}
ANN = {**TOKEN, "X-Requester-Username": "ann"}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, internal_token="tok", voice="v1")


@pytest.fixture
def client(settings):
    return TestClient(build_app(settings, engine=FakeEngine()))


@pytest.fixture
def file_id(settings):
    store = AudioStore(settings.audio_dir, settings.ttl_seconds, settings.quota_bytes)
    fid = AudioStore.file_id("v1", "hello")
    store.put("ann", fid, b"0123456789", "v1", 1.0)
    return fid


def test_health_needs_no_token(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"ok": True}


def test_files_require_the_internal_token(client, file_id):
    response = client.get(f"/api/files/{file_id}", headers={"X-Requester-Username": "ann"})
    assert response.status_code == 401
    assert client.get(f"/api/files/{file_id}", headers={"X-Internal-Token": "wrong", "X-Requester-Username": "ann"}).status_code == 401


def test_files_require_a_requester(client, file_id):
    assert client.get(f"/api/files/{file_id}", headers=TOKEN).status_code == 401


def test_the_owner_gets_the_audio(client, file_id):
    response = client.get(f"/api/files/{file_id}", headers=ANN)
    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_range_requests_are_served(client, file_id):
    response = client.get(f"/api/files/{file_id}", headers={**ANN, "Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_another_owner_a_missing_id_and_a_malformed_id_all_answer_404(client, file_id):
    assert client.get(f"/api/files/{file_id}", headers=BOB).status_code == 404
    assert client.get(f"/api/files/{'a' * 32}", headers=ANN).status_code == 404
    assert client.get("/api/files/not-an-id", headers=ANN).status_code == 404


def test_without_a_configured_token_the_routes_are_open(tmp_path):
    settings = Settings(data_dir=tmp_path, internal_token="", voice="v1")
    store = AudioStore(settings.audio_dir, settings.ttl_seconds, settings.quota_bytes)
    fid = AudioStore.file_id("v1", "hello")
    store.put("ann", fid, b"abc", "v1", 1.0)
    client = TestClient(build_app(settings, engine=FakeEngine()))
    assert client.get(f"/api/files/{fid}", headers={"X-Requester-Username": "ann"}).status_code == 200


@pytest.mark.parametrize("path,method", [("/api/speak", "post"), ("/api/voices", "get")])
def test_speech_routes_require_identity_and_token(client, path, method):
    call = getattr(client, method)
    kwargs = {"json": {"text": "hello"}} if method == "post" else {}
    assert call(path, headers={"X-Requester-Username": "ann"}, **kwargs).status_code == 401
    assert call(path, headers=TOKEN, **kwargs).status_code == 401


def test_speak_cleans_text_uses_selected_voice_and_owns_file(client):
    response = client.post("/api/speak", headers=ANN, json={"text": "**hello**", "voice": "v2"})
    assert response.status_code == 200
    body = response.json()
    assert body == {"file_id": AudioStore.file_id("v2", "hello"), "duration_s": 2.0, "voice": "v2"}
    assert client.get(f"/api/files/{body['file_id']}", headers=ANN).content == b"mp3:hello"
    assert client.get(f"/api/files/{body['file_id']}", headers=BOB).status_code == 404


def test_speak_default_and_validation(client):
    assert client.post("/api/speak", headers=ANN, json={"text": "hello"}).json()["voice"] == "v1"
    for body in [{"text": ""}, {"text": "a" * 5001}, {"text": "hello", "voice": "missing"}, {}, {"text": 123}]:
        assert client.post("/api/speak", headers=ANN, json=body).status_code == 400
    assert client.post("/api/speak", headers=ANN, content="{").status_code == 400


def test_voices_lists_only_installed_without_a_default(client):
    response = client.get("/api/voices", headers=ANN)
    assert response.status_code == 200
    assert response.json() == {"voices": ["v1", "v2"]}


@pytest.fixture
def admin_client(tmp_path):
    settings = Settings(data_dir=tmp_path, internal_token="tok", voice="v1", admin_password="pw")
    return TestClient(build_app(settings, engine=FakeEngine())), tmp_path


def test_admin_is_off_without_a_password(client):
    assert client.get("/admin/settings").status_code in (404, 405)


def test_admin_needs_the_password(admin_client):
    client, _ = admin_client
    assert client.get("/admin/settings").status_code == 401
    assert client.put("/admin/settings", json={"values": {"max_chars": 10}}, headers={"X-Admin-Password": "x"}).status_code == 401


def test_admin_saves_and_the_service_uses_it(admin_client):
    client, _ = admin_client
    auth = {"X-Admin-Password": "pw"}
    rows = {r["key"]: r for r in client.get("/admin/settings", headers=auth).json()["settings"]}
    assert rows["max_chars"]["value"] == 5000 and rows["voice"]["value"] == "v1"
    saved = client.put("/admin/settings", json={"values": {"max_chars": 10}}, headers=auth)
    assert saved.status_code == 200
    spoken = client.post("/api/speak", json={"text": "x" * 50}, headers=BOB)
    assert spoken.status_code == 400  # now over the 10 character limit


def test_admin_refuses_bad_values_and_unknown_voices(admin_client):
    client, _ = admin_client
    auth = {"X-Admin-Password": "pw"}
    assert client.put("/admin/settings", json={"values": {"max_chars": 0}}, headers=auth).status_code == 422
    assert client.put("/admin/settings", json={"values": {"voice": "nope"}}, headers=auth).status_code == 422
    assert client.put("/admin/settings", json={"oops": 1}, headers=auth).status_code == 422


def test_admin_page_is_served_without_the_internal_token(admin_client):
    client, _ = admin_client
    assert client.get("/admin/").status_code == 200


def test_the_admin_page_shows_status_usage_and_the_piper_and_paradee_slots(admin_client):
    client, _ = admin_client
    pw = {"X-Admin-Password": "pw"}
    assert client.get("/admin/status").status_code == 401
    assert client.post("/api/speak", json={"text": "hello there"}, headers=ANN).status_code == 200
    body = client.get("/admin/status", headers=pw).json()
    assert body["service"] == "Bard" and body["usage"]["calls"] == 1 and body["usage"]["users"][0]["user"] == "ann"
    assert {"label": "Piper", "value": "not connected"} in body["items"]
    slots = {s["key"]: s for s in client.get("/admin/backends", headers=pw).json()["backends"]}
    assert set(slots) == {"piper", "paradee"} and slots["piper"]["url"] == "" and slots["piper"]["local"] is False
    client.put("/admin/backends/piper", headers=pw, json={"url": "http://piper:9150", "token": "t"})
    assert {"label": "Piper", "value": "model service"} in client.get("/admin/status", headers=pw).json()["items"]

