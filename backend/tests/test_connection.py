import base64
import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from analyst import chatgpt_plan as plan
from analyst.api import create_app
from analyst.config import Settings
from tests.test_chatgpt_plan import completed_event, sse

LOCAL = {"Origin": "http://localhost:3000", "X-TFS-Local": "1"}



def test_disconnected_connection_is_local_and_contains_no_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "credentials"))
    app = create_app(settings=Settings("", "", provider="chatgpt_plan"))
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/ai/connection")
        assert response.status_code == 200
        assert response.json()["state"] == "disconnected"
        assert response.json()["provider"] == "chatgpt_plan"
        assert not any(key in response.text for key in ("access_token", "refresh_token", "id_token", "code_verifier"))
        assert client.post("/api/ai/login", headers={"Origin": "https://evil.example"}).status_code == 403
    with TestClient(app, client=("192.168.1.2", 50000)) as client:
        assert client.get("/api/ai/connection").status_code == 403

@pytest.fixture
def oauth(monkeypatch, tmp_path):
    """Mock OpenAI HTTP and the browser only; real callback, PKCE, JWT checks, DPAPI and SDK."""
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt_plan")
    monkeypatch.delenv("CHATGPT_PLAN_MODEL", raising=False)
    key = json.loads((Path(__file__).parent / "fixtures/dummy_oauth_rsa.json").read_text())
    n, d = [int.from_bytes(base64.b64decode(key[k]), "big") for k in ("n", "d")]
    state = {"scope": plan.SCOPES, "callback": True, "token_error": False, "calls": []}
    original_get = httpx.get

    def get(url, **kwargs):
        if url == plan.JWKS:
            return httpx.Response(200, json={"keys": [{"kid": "test", "kty": "RSA", "n": key['n'].replace('+', '-').replace('/', '_').rstrip('='), "e": "AQAB"}]})
        if url == f"{plan.RESOURCE}/models":
            assert kwargs["headers"]["Authorization"] == "Bearer dummy-access"
            return httpx.Response(200, request=httpx.Request("GET", url), json={"models": [
                {"slug": "supported-test-model", "display_name": "Supported model", "visibility": "list"},
                {"slug": "hidden-model", "visibility": "hide"}]})
        return original_get(url, **kwargs)

    def post(url, **kwargs):
        assert url == plan.TOKEN
        fields = kwargs["data"]
        if state["token_error"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if fields["grant_type"] == "authorization_code":
            assert fields["code"] == "dummy-code"
            assert plan._b64url(hashlib.sha256(fields["code_verifier"].encode()).digest()) == state["params"]["code_challenge"][0]
        claims = {"iss": plan.ISSUER, "aud": "test-client", "sub": "test-subject", "email": "investor@example.test", "nonce": state["params"]["nonce"][0], "exp": time.time() + 3600}
        head = plan._b64url(json.dumps({"alg": "RS256", "kid": "test"}).encode())
        body = plan._b64url(json.dumps(claims).encode())
        payload = f"{head}.{body}"
        digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload.encode()).digest()
        size = (n.bit_length() + 7) // 8
        encoded = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
        signature = pow(int.from_bytes(encoded, 'big'), d, n).to_bytes(size, 'big')
        return httpx.Response(200, json={"access_token": "dummy-access", "refresh_token": "dummy-refresh", "expires_in": 3600,
                                       "scope": state["scope"], "id_token": f"{payload}.{plan._b64url(signature)}"})

    def browser(url):
        state["params"] = parse_qs(urlsplit(url).query)
        if state["callback"]:
            params = state["params"]
            def callback():
                original_get(plan.REDIRECT, params={"state": params["state"][0], "code": "dummy-code", "client_id": "test-client"})
            threading.Thread(target=callback, daemon=True).start()
        return True

    async def inference(request):
        import asyncio
        while state.get("hold"):
            await asyncio.sleep(.01)
        state["calls"].append(json.loads(request.content))
        if state.get("inference_error"):
            return httpx.Response(200, text=sse({"type": "response.failed", "response": {"error": {
                "code": "test_failure", "message": "dummy-access dummy-refresh dummy-id"}}}), headers={"content-type": "text/event-stream"})
        assert request.headers["Authorization"] == "Bearer dummy-access"
        items = [{"type": "message", "id": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": "A local conceptual answer.", "annotations": []}]}]
        return httpx.Response(200, text=sse(*completed_event(items)), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(plan.webbrowser, "open", browser)
    monkeypatch.setattr("analyst.providers.AsyncOpenAI", lambda **kw: AsyncOpenAI(**kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(inference))))
    return state


def wait_login(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get("/api/ai/connection").json()
        if result["state"] != "connecting":
            return result
        time.sleep(.01)
    pytest.fail("Login did not finish")


def test_browser_sign_in_activates_subsequent_ai_requests_without_restart(oauth, caplog):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/ai/connection").json()["state"] == "disconnected"
        assert client.post("/api/ai/login", headers=LOCAL).status_code == 200
        status = wait_login(client)
        assert status["state"] == "connected", status
        assert status["account"] == "investor@example.test"
        assert status["models"] == [{"slug": "supported-test-model", "display_name": "Supported model"}]
        assert client.post("/api/ai/provider", headers=LOCAL, json={"provider": "chatgpt_plan", "model": "hidden-model"}).status_code == 422
        assert client.post("/api/ai/provider", headers=LOCAL, json={"provider": "chatgpt_plan", "model": "supported-test-model"}).status_code == 200
        thread = client.post("/api/threads", json={}).json()["id"]
        run = client.post(f"/api/threads/{thread}/messages", json={"text": "Explain diversification", "client_request_id": "one"})
        assert run.status_code == 202, run.text
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(f"/api/threads/{thread}/runs/{run.json()['id']}").json()
            if result["status"] != "running":
                break
            time.sleep(.01)
        assert result["status"] == "completed", result
        assert oauth["calls"][0]["model"] == "supported-test-model"
        assert oauth["calls"][0]["stream"] and not oauth["calls"][0]["store"]
        for secret in ("dummy-access", "dummy-refresh", "dummy-code", "code_verifier", "id_token"):
            assert secret not in json.dumps(status) + run.text + caplog.text
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as reopened:
        status = reopened.get("/api/ai/connection").json()
        assert status["state"] == "connected" and status["model"] == "supported-test-model"


def test_cancel_and_concurrent_login_are_safe(oauth):
    oauth["callback"] = False
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/ai/login", headers=LOCAL).status_code == 200
        assert client.post("/api/ai/login", headers=LOCAL).status_code == 409
        assert client.post("/api/ai/login/cancel", headers=LOCAL).json()["state"] == "failed"
        assert not list(plan.home().glob('*.dpapi'))


def test_insufficient_plan_scope_is_recoverable(oauth):
    oauth["scope"] = "openid email"
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post("/api/ai/login", headers=LOCAL)
        status = wait_login(client)
        assert status["state"] == "failed" and "permission" in status["message"]
        assert not list(plan.home().glob('*.dpapi'))



def test_provider_switch_cannot_change_an_active_run(oauth):
    oauth['hold'] = True
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        wait_login(client)
        choice = {"provider": "chatgpt_plan", "model": "supported-test-model"}
        client.post('/api/ai/provider', headers=LOCAL, json=choice)
        thread = client.post('/api/threads', json={}).json()['id']
        run = client.post(f'/api/threads/{thread}/messages', json={"text": "Explain diversification", "client_request_id": "active"}).json()
        assert client.post('/api/ai/provider', headers=LOCAL, json={"provider": "openai"}).status_code == 409
        assert client.post('/api/ai/login', headers=LOCAL).status_code == 409
        assert client.post(f'/api/threads/{thread}/runs/{run["id"]}/cancel').json()['status'] == 'cancelled'
        oauth['hold'] = False
        time.sleep(.05)
        assert client.post('/api/ai/provider', headers=LOCAL, json={"provider": "openai"}).status_code == 200
        assert client.get('/api/ai/connection').json()['provider'] == 'openai'


def test_expiration_and_revocation_have_recovery(oauth, monkeypatch):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        assert wait_login(client)['state'] == 'connected'
        now = time.time()
        monkeypatch.setattr(plan.time, 'time', lambda: now + 7200)
        assert client.get('/api/ai/connection').json()['state'] == 'expired'
        oauth['token_error'] = True
        status = client.post('/api/ai/refresh', headers=LOCAL).json()
        assert status['state'] == 'failed' and 'expired or revoked' in status['message']


def test_sign_in_timeout_is_recoverable(oauth, monkeypatch):
    oauth['callback'] = False
    monkeypatch.setattr(plan, 'LOGIN_TIMEOUT', .01)
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        status = wait_login(client)
        assert status['state'] == 'failed' and 'timed out' in status['message']


def test_callback_port_conflict_does_not_touch_its_owner(oauth):
    import socket
    with socket.socket() as owner:
        owner.bind(('127.0.0.1', 1455))
        owner.listen()
        with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
            client.post('/api/ai/login', headers=LOCAL)
            status = wait_login(client)
            assert status['state'] == 'failed' and '1455 is occupied' in status['message']
            assert owner.getsockname()[1] == 1455


def test_unsupported_storage_reports_native_windows_path(oauth, monkeypatch):
    from types import SimpleNamespace

    from analyst import connection
    monkeypatch.setattr(connection, 'sys', SimpleNamespace(platform='linux'))
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        status = client.get('/api/ai/connection').json()
        assert status['state'] == 'unavailable' and 'WSL/Linux' in status['message']
        assert client.post('/api/ai/login', headers=LOCAL).status_code == 503


def test_local_auth_rejects_cross_origin_missing_header_and_remote_proxy(oauth):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert client.post('/api/ai/login', headers={'Origin': 'http://localhost:3000'}).status_code == 403
        assert client.post('/api/ai/login', headers={'X-TFS-Local': '1'}).status_code == 403
        assert client.get('/api/ai/connection', headers={'Host': 'attacker.example'}).status_code == 403
        assert client.get('/api/ai/connection', headers={'X-Forwarded-For': '203.0.113.1'}).status_code == 403


def test_new_install_defaults_to_plan_but_honors_explicit_api_provider(monkeypatch, tmp_path):
    monkeypatch.delenv('LLM_PROVIDER')
    monkeypatch.delenv('OPENAI_API_KEY')
    assert Settings.from_environment().provider == 'chatgpt_plan'
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    assert Settings.from_environment().provider == 'openai'


def test_disconnected_theme_clarification_still_needs_no_ai(oauth):
    from analyst.providers import FakeDataProvider
    from tests.test_theme import theme_request
    with TestClient(create_app(data=FakeDataProvider()), client=("127.0.0.1", 50000)) as client:
        result = client.post('/api/analyze', json=theme_request())
        assert result.status_code == 200, result.text
        assert result.json()['theme']['status'] == 'awaiting_agreement'


def test_plan_service_errors_never_expose_provider_secrets(oauth, caplog):
    oauth['inference_error'] = True
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        wait_login(client)
        client.post('/api/ai/provider', headers=LOCAL, json={"provider": "chatgpt_plan", "model": "supported-test-model"})
        thread = client.post('/api/threads', json={}).json()['id']
        run = client.post(f'/api/threads/{thread}/messages', json={"text": "Explain diversification", "client_request_id": "error"}).json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(f'/api/threads/{thread}/runs/{run["id"]}').json()
            if result['status'] != 'running':
                break
            time.sleep(.01)
        assert result['status'] == 'failed'
        for secret in ['dummy-access', 'dummy-refresh', 'dummy-id']:
            assert secret not in json.dumps(result) + caplog.text
