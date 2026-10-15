import base64
import hashlib
import json
import sys
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

@pytest.mark.parametrize("missing", ["OPENAI_API_KEY", "OPENAI_MODEL"])
def test_api_key_provider_without_key_is_refused_and_not_saved(monkeypatch, tmp_path, missing):
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "credentials"))
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt_plan")
    monkeypatch.setenv(missing, "")
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openai"})
        assert response.status_code == 422
        assert "backend/.env" in response.json()["detail"]
        assert client.get("/api/ai/connection").json()["provider"] == "chatgpt_plan"
    assert not (tmp_path / "credentials" / "selection.json").exists()

def test_configured_api_key_provider_is_reported_as_the_ready_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "credentials"))
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/ai/connection").json()
    assert status["provider"] == "openai" and status["state"] == "connected"
    assert status["message"] == "API-key provider ready. AI questions use test-model."
    assert status["chatgpt_state"] in {"disconnected", "unavailable"}
    assert "sk-test" not in json.dumps(status)

def test_configured_openrouter_provider_reports_its_provider_and_model(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "credentials"))
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/model:free")
    monkeypatch.setattr("analyst.api.openrouter_problem", lambda model, reasoning: None)
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/ai/connection").json()
        assert client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter"}).status_code == 200
    assert (status["provider"], status["api_provider"], status["model"], status["state"]) == (
        "openrouter", "openrouter", "vendor/model:free", "connected")
    assert "sk-or-secret" not in json.dumps(status)

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
        if url == f"{plan.ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, request=httpx.Request("GET", url), json={"revocation_endpoint": f"{plan.ISSUER}/test/revoke"})
        if url == plan.JWKS:
            return httpx.Response(200, json={"keys": [{"kid": "test", "kty": "RSA", "n": key['n'].replace('+', '-').replace('/', '_').rstrip('='), "e": "AQAB"}]})
        if url == f"{plan.RESOURCE}/models":
            assert kwargs["headers"]["Authorization"] == "Bearer dummy-access"
            if state.get("catalog_status"):
                return httpx.Response(state["catalog_status"], request=httpx.Request("GET", url), text="dummy-access dummy-refresh")
            return httpx.Response(200, request=httpx.Request("GET", url), json={"models": [
                {"slug": "supported-test-model", "display_name": "Supported model", "visibility": "list"},
                {"slug": "hidden-model", "visibility": "hide"}]})
        return original_get(url, **kwargs)

    def post(url, **kwargs):
        if url == f"{plan.ISSUER}/test/revoke":
            state.setdefault("revocations", []).append(kwargs["data"])
            return httpx.Response(state.get("revocation_status", 200), request=httpx.Request("POST", url))
        assert url == plan.TOKEN
        fields = kwargs["data"]
        if state["token_error"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if fields["grant_type"] == "authorization_code":
            assert fields["code"] == "dummy-code"
            assert plan._b64url(hashlib.sha256(fields["code_verifier"].encode()).digest()) == state["params"]["code_challenge"][0]
        claims = {"iss": plan.ISSUER, "aud": "test-client", "sub": state.get("subject", "test-subject"), "email": "investor@example.test", "nonce": state["params"]["nonce"][0], "exp": time.time() + 3600}
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
        assert client.post('/api/ai/logout', headers=LOCAL).status_code == 409
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
        if sys.platform != "win32":  # earlier callbacks leave TIME_WAIT on 1455; Windows REUSEADDR would let TFS steal the port
            owner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
        assert client.post('/api/ai/logout', headers={'Origin': 'https://attacker.example', 'X-TFS-Local': '1'}).status_code == 403
        assert client.get('/api/ai/connection', headers={'Host': 'attacker.example'}).status_code == 403
        assert client.get('/api/ai/connection', headers={'X-Forwarded-For': '203.0.113.1'}).status_code == 403


def test_new_install_defaults_to_plan_but_honors_explicit_api_provider(monkeypatch, tmp_path):
    # Set empty (not deleted) so a local backend/.env cannot fill them in.
    monkeypatch.setenv('LLM_PROVIDER', '')
    monkeypatch.setenv('OPENAI_API_KEY', '')
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


def test_catalog_upstream_failure_keeps_login_and_explains_safe_retry(oauth):
    oauth['catalog_status'] = 520
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        status = wait_login(client)
        assert status['state'] == 'unavailable'
        assert status['account'] == 'investor@example.test'
        assert 'HTTP 520' in status['message']
        assert 'Refresh connection' in status['message']
        assert 'dummy-access' not in json.dumps(status)
        oauth['catalog_status'] = 0
        recovered = client.post('/api/ai/refresh', headers=LOCAL).json()
        assert recovered['state'] == 'connected'
        assert recovered['models'][0]['slug'] == 'supported-test-model'


def test_logout_revokes_session_preserves_registration_and_allows_login_again(oauth):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        assert wait_login(client)['state'] == 'connected'
        host_id = oauth['params']['ext_agent_host_id'][0]
        response = client.post('/api/ai/logout', headers=LOCAL)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['state'] == 'disconnected' and result['account'] is None
        assert result['models'] == [] and result['model'] == ''
        assert oauth['revocations'] == [{"token": "dummy-refresh", "token_type_hint": "refresh_token", "client_id": "test-client"}]
        for secret in ('dummy-access', 'dummy-refresh', 'id_token', 'code_verifier'):
            assert secret not in response.text
        assert client.post('/api/ai/logout', headers=LOCAL).status_code == 200
        assert len(oauth['revocations']) == 1
        assert client.post('/api/ai/login', headers=LOCAL).status_code == 200
        assert wait_login(client)['state'] == 'connected'
        assert oauth['params']['client_id'] == ['test-client']
        assert oauth['params']['ext_agent_host_id'] == [host_id]
        assert 'id_token_hint' not in oauth['params']


def test_logout_clears_local_login_even_when_remote_revocation_fails(oauth):
    oauth['catalog_status'] = 520
    oauth['revocation_status'] = 520
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        assert wait_login(client)['state'] == 'unavailable'
        response = client.post('/api/ai/logout', headers=LOCAL)
        assert response.status_code == 200, response.text
        assert response.json()['state'] == 'disconnected'
        assert 'revocation was not confirmed' in response.json()['message']
        assert response.json()['account'] is None
        assert len(oauth['revocations']) == 2
        assert client.get('/api/ai/connection').json()['account'] is None


def test_logout_cannot_lose_account_binding_when_storage_write_fails(oauth, monkeypatch):
    import os
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        wait_login(client)
        replace = os.replace
        def fail_host_update(source, destination):
            if Path(destination).name == 'host.json':
                raise OSError('simulated disk failure')
            return replace(source, destination)
        monkeypatch.setattr(os, 'replace', fail_host_update)
        assert client.post('/api/ai/logout', headers=LOCAL).status_code == 503
        assert client.get('/api/ai/connection').json()['account'] == 'investor@example.test'


def test_login_after_logout_cannot_replace_registration_with_another_identity(oauth):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        client.post('/api/ai/login', headers=LOCAL)
        wait_login(client)
        assert client.post('/api/ai/logout', headers=LOCAL).json()['state'] == 'disconnected'
        oauth['subject'] = 'another-account'
        client.post('/api/ai/login', headers=LOCAL)
        status = wait_login(client)
        assert status['state'] == 'failed' and status['account'] is None


DEEPSEEK = "deepseek/deepseek-v4.1-flash"
NEMOTRON = "nvidia/nemotron-3-super-120b-a12b:free"


@pytest.fixture
def openrouter(monkeypatch, tmp_path):
    """ChatGPT is the startup provider; OpenRouter is configured alongside it. The public catalog is mocked."""
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "credentials"))
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt_plan")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", NEMOTRON)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "")  # set, so backend/.env cannot supply one
    catalog = {"data": [{"id": DEEPSEEK, "supported_parameters": ["tools", "tool_choice", "structured_outputs"]},
                        {"id": NEMOTRON, "supported_parameters": ["tools", "tool_choice"]}], "fail": False}

    def get(url, **kwargs):
        assert url == "https://openrouter.ai/api/v1/models" and "Authorization" not in kwargs.get("headers", {})
        if catalog["fail"]:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": catalog["data"]})

    monkeypatch.setattr("analyst.connection.httpx.get", get)
    return catalog


def test_openrouter_models_are_listed_and_selection_survives_a_restart(openrouter, tmp_path):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/ai/connection").json()
        assert [row["slug"] for row in status["openrouter_models"]] == [DEEPSEEK, NEMOTRON]
        assert status["openrouter_available"] is True
        selected = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter", "model": DEEPSEEK})
        assert selected.status_code == 200, selected.text
        assert (selected.json()["provider"], selected.json()["model"], selected.json()["state"]) == ("openrouter", DEEPSEEK, "connected")
        assert "sk-or-secret" not in selected.text
    # A new backend process restores the saved choice even though .env names another provider.
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert (client.get("/api/ai/connection").json()["provider"], client.get("/api/ai/connection").json()["model"]) == ("openrouter", DEEPSEEK)


def test_incompatible_or_unchecked_openrouter_models_are_refused_without_switching(openrouter):
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        refused = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter", "model": NEMOTRON})
        assert refused.status_code == 422
        assert "structured_outputs" in refused.json()["detail"]
        unknown = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter", "model": "vendor/other"})
        assert unknown.status_code == 422
        openrouter["fail"] = True
        offline = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter", "model": DEEPSEEK})
        assert offline.status_code == 503 and "not changed" in offline.json()["detail"]
        assert client.get("/api/ai/connection").json()["provider"] == "chatgpt_plan"


def test_openrouter_without_a_backend_key_cannot_be_selected(openrouter, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/ai/connection").json()["openrouter_available"] is False
        refused = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter", "model": DEEPSEEK})
        assert refused.status_code == 422 and "OPENROUTER_API_KEY" in refused.json()["detail"]


def test_selected_openrouter_model_is_the_one_each_request_uses(openrouter):
    from analyst.connection import Connection
    connection = Connection(Settings.from_environment())
    connection.provider, connection.model = "openrouter", DEEPSEEK
    config = connection.resolve()
    assert (config.provider, config.model, config.api_key, config.openrouter) == ("openrouter", DEEPSEEK, "sk-or-secret", True)


def test_a_configured_reasoning_effort_requires_openrouter_reasoning_support(openrouter, monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        refused = client.post("/api/ai/provider", headers=LOCAL, json={"provider": "openrouter", "model": DEEPSEEK})
        assert refused.status_code == 422 and "reasoning" in refused.json()["detail"]


def test_a_signed_out_chatgpt_selection_does_not_hide_the_configured_provider(openrouter, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    (tmp_path / "credentials").mkdir(parents=True, exist_ok=True)
    # Sign-out saves the ChatGPT provider without a model; that is not a choice to restore.
    (tmp_path / "credentials" / "selection.json").write_text(json.dumps({"provider": "chatgpt_plan", "model": ""}))
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/ai/connection").json()
    assert (status["provider"], status["model"], status["state"]) == ("openrouter", NEMOTRON, "connected")
