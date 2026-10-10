"""ChatGPT plan usage through OpenAI's official Sign in with ChatGPT flow for open-source local apps.
https://developers.openai.com/siwc/token-sharing-open-source

Greedy registers itself (dynamic_agent_client, PKCE S256), keeps the tokens DPAPI-encrypted for the current Windows
user under %LOCALAPPDATA%\\Greedy\\chatgpt-plan and rotates the refresh token. It never reads Codex or other tools'
credentials. Inference uses the public Responses API with store=false and stream=true; the stream is consumed here
under one deadline per model turn.

    python -m analyst.chatgpt_plan login | status | models
"""
import asyncio
import base64
import collections
import ctypes
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import openai

ISSUER = "https://auth.openai.com"
AUTHORIZE = f"{ISSUER}/api/accounts/authorize"
TOKEN = f"{ISSUER}/api/accounts/oauth/token"
JWKS = f"{ISSUER}/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
APP_NAME = "Greedy"
REDIRECT = "http://127.0.0.1:1455/auth/callback"
REFRESH_MARGIN = 900  # refresh when under 15 minutes remain; an analysis finishes well inside that
USAGE_CODES = {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}


class PlanError(openai.OpenAIError):
    """A plan-route failure: response.failed, an unusable stream, or missing/invalid credentials."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"ChatGPT plan {code}: {message}".strip(": "))
        self.code = code


def home() -> Path:
    return Path(os.environ.get("CHATGPT_PLAN_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Greedy" / "chatgpt-plan")


# --- Responses stream: one deadline for the whole turn ---

async def stream_response(client: openai.AsyncOpenAI, params: dict[str, Any], timeout: float) -> Any:
    """Create a streamed response and read it to its terminal event within `timeout` seconds in total.
    httpx's timeout only bounds each read, so a stream that keeps emitting would otherwise never end. On expiry the
    stream is closed and openai.APITimeoutError raised; nothing is retried and partial events are only logged."""
    seen: collections.Counter[str] = collections.Counter()
    stream = None
    try:
        async with asyncio.timeout(timeout):
            stream = await client.responses.create(**params, stream=True)
            return await _terminal(stream, seen)
    except TimeoutError:
        logging.error("CHATGPT PLAN TIMEOUT after %ss; partial events (diagnostic only): %s", timeout, dict(seen))
        raise openai.APITimeoutError(request=httpx.Request("POST", f"{RESOURCE}/responses")) from None
    finally:
        if stream is not None:
            await stream.close()


async def _terminal(stream: Any, seen: collections.Counter[str]) -> Any:
    # The plan route sends response.completed with output=[]: items arrive only as response.output_item.done.
    items: dict[int, Any] = {}
    async for event in stream:
        seen[event.type] += 1
        if event.type == "response.output_item.done":
            items[event.output_index] = event.item
        elif event.type in {"response.completed", "response.incomplete"}:
            response = event.response
            if not response.output and items:
                response.output = [items[index] for index in sorted(items)]
            return response
        elif event.type == "response.failed":
            error = event.response.error
            code = getattr(error, "code", None) or "response_failed"
            if code in USAGE_CODES:
                logging.error("CHATGPT PLAN USAGE LIMIT code=%s: pause plan requests; see ChatGPT Settings > Usage.", code)
            raise PlanError(code, getattr(error, "message", "") or "")
        elif event.type == "error":
            raise PlanError(getattr(event, "code", None) or "stream_error", getattr(event, "message", "") or "")
    logging.error("CHATGPT PLAN stream ended without a terminal event; partial events (diagnostic only): %s", dict(seen))
    raise PlanError("no_terminal_event", "The stream ended without response.completed.")


# --- Credentials: DPAPI-encrypted record per issued client, atomic writes ---

def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        # ponytail: Windows DPAPI only; add an OS keychain backend when the app runs elsewhere.
        raise PlanError("unsupported_storage", "Encrypted ChatGPT plan credentials need Windows DPAPI.")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    crypt32 = ctypes.windll.crypt32
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not call(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):  # 0x1: no UI
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _host() -> dict[str, Any]:
    path = home() / "host.json"  # not secret: the stable host ID and the issued client ID
    if path.exists():
        return dict(json.loads(path.read_text()))
    host = {"ext_agent_host_id": f"urn:uuid:{uuid.uuid4()}", "client_id": None}
    _write(path, json.dumps(host).encode())
    return host


def _save_host(host: dict[str, Any]) -> None:
    _write(home() / "host.json", json.dumps(host).encode())


def save(record: dict[str, Any]) -> None:
    _write(home() / f"{record['client_id']}.dpapi", _dpapi(json.dumps(record).encode(), True))


def load() -> dict[str, Any]:
    client_id = _host().get("client_id")
    path = home() / f"{client_id}.dpapi"
    if not client_id or not path.exists():
        raise PlanError("not_signed_in", "Run: python -m analyst.chatgpt_plan login")
    return dict(json.loads(_dpapi(path.read_bytes(), False)))


def _post_form(fields: dict[str, str]) -> dict[str, Any]:
    response = httpx.post(TOKEN, data=fields, headers={"Accept": "application/json"}, timeout=30)
    if response.status_code != 200:
        try:
            code = response.json().get("error", "token_error")
        except ValueError:
            code = "token_error"
        raise PlanError(str(code), f"token endpoint HTTP {response.status_code}")
    return dict(response.json())


def access_token() -> str:
    """The current access token, refreshed with refresh-token rotation when near expiry."""
    record = load()
    if record["saved_at"] + record["expires_in"] - time.time() > REFRESH_MARGIN:
        return str(record["access_token"])
    tokens = _post_form({"grant_type": "refresh_token", "client_id": record["client_id"],
                         "refresh_token": record["refresh_token"], "resource": RESOURCE})
    scopes = sorted(tokens.get("scope", " ".join(record["scopes"])).split())
    # Access token, expiry, scopes and the rotated refresh token are replaced together.
    record.update(access_token=tokens["access_token"], refresh_token=tokens.get("refresh_token", record["refresh_token"]),
                  expires_in=tokens["expires_in"], scopes=scopes, saved_at=time.time())
    save(record)
    if PLAN_SCOPE not in scopes:
        raise PlanError("plan_scope_missing", f"The refreshed grant lacks {PLAN_SCOPE}.")
    return str(record["access_token"])


# --- Sign in (system browser + loopback callback) ---

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def rs256_valid(signing_input: bytes, signature: bytes, n: int, e: int) -> bool:
    """RSASSA-PKCS1-v1_5 / SHA-256: rebuild the whole expected encoding and compare it, never parse the decrypted block."""
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    decrypted = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing_input).digest()
    return hmac.compare_digest(decrypted, b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info)


def verify_id_token(id_token: str, client_id: str, nonce: str) -> dict[str, Any]:
    header_b64, payload_b64, signature_b64 = id_token.split(".")
    header, claims = json.loads(_unb64url(header_b64)), json.loads(_unb64url(payload_b64))
    if header.get("alg") != "RS256":
        raise PlanError("id_token_invalid", f"unsupported alg {header.get('alg')}")
    keys = httpx.get(JWKS, timeout=30).json()["keys"]
    key = next((row for row in keys if row.get("kid") == header.get("kid") and row.get("kty") == "RSA"), None)
    if key is None:
        raise PlanError("id_token_invalid", "kid not in OpenAI JWKS")
    n, e = (int.from_bytes(_unb64url(key[name]), "big") for name in ("n", "e"))
    if not rs256_valid(f"{header_b64}.{payload_b64}".encode(), _unb64url(signature_b64), n, e):
        raise PlanError("id_token_invalid", "signature")
    audience = claims.get("aud")
    failed = [name for name, ok in {
        "iss": claims.get("iss") == ISSUER,
        "aud": client_id == audience or (isinstance(audience, list) and client_id in audience),
        "exp": isinstance(claims.get("exp"), (int, float)) and claims["exp"] > time.time() - 5,
        "nonce": hmac.compare_digest(str(claims.get("nonce", "")), nonce),
        "sub": isinstance(claims.get("sub"), str) and bool(claims["sub"]),
    }.items() if not ok]
    if failed:
        raise PlanError("id_token_invalid", str(failed))
    return dict(claims)


def login() -> None:
    host = _host()
    saved = host.get("client_id")
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    params = {"client_id": saved or "dynamic_agent_client", "ext_agent_host_id": host["ext_agent_host_id"], "response_type": "code",
              "redirect_uri": REDIRECT, "scope": SCOPES, "resource": RESOURCE, "state": state, "nonce": nonce,
              "code_challenge_method": "S256", "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest())}
    previous = None
    if saved:
        try:
            previous = load()
            params.update(id_token_hint=previous["id_token"], login_hint=previous.get("email") or "")
        except PlanError:
            pass
    else:
        params["agent_name_hint"] = APP_NAME
    received: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return
            received.update({key: value[0] for key, value in urllib.parse.parse_qs(parsed.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Greedy: sign-in received. You can close this tab.")

        def log_message(self, *args: Any) -> None:  # the callback query carries the code: never log it
            pass

    server = HTTPServer(("127.0.0.1", 1455), Callback)  # listening before the browser opens
    server.timeout = 600
    url = f"{AUTHORIZE}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
    print("Opening the browser for Sign in with ChatGPT." + ("" if previous else f" If it does not open, visit:\n{url}"), flush=True)
    webbrowser.open(url)
    deadline = time.time() + 600
    while not received and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if not received or not hmac.compare_digest(received.get("state", ""), state):
        raise SystemExit("No valid callback (missing or mismatched state).")
    if "error" in received:
        raise SystemExit(f"Authorization error: {received['error']}")
    issued = received.get("client_id")
    client_id = saved
    if not saved:
        if not issued or issued == "dynamic_agent_client":
            raise SystemExit("Registration incomplete: no issued client_id.")
        client_id = issued
        _save_host({**host, "client_id": client_id})  # kept even if the exchange below fails
    elif issued and issued != saved:
        raise SystemExit("Callback client_id differs from the saved registration.")
    assert client_id
    tokens = _post_form({"grant_type": "authorization_code", "client_id": client_id, "code": received["code"],
                         "code_verifier": verifier, "redirect_uri": REDIRECT, "resource": RESOURCE})
    claims = verify_id_token(tokens["id_token"], client_id, nonce)
    if previous and previous.get("subject") != claims["sub"]:
        raise SystemExit("Signed-in identity differs from the saved account; credentials not replaced.")
    scopes = sorted(tokens.get("scope", "").split())
    if PLAN_SCOPE not in scopes or "refresh_token" not in tokens:
        raise SystemExit(f"ChatGPT plan usage was not granted: scopes={scopes}")
    save({"email": claims.get("email"), "issuer": ISSUER, "subject": claims["sub"], "client_id": client_id,
          "ext_agent_host_id": host["ext_agent_host_id"], "id_token": tokens["id_token"], "access_token": tokens["access_token"],
          "refresh_token": tokens["refresh_token"], "token_type": tokens.get("token_type"), "expires_in": tokens["expires_in"],
          "scopes": scopes, "saved_at": time.time()})
    print(f"Signed in. client_id={client_id} scopes={scopes}")


def models() -> list[dict[str, Any]]:
    response = httpx.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {access_token()}"}, timeout=30)
    response.raise_for_status()
    return [{"slug": row.get("slug"), "display_name": row.get("display_name")}
            for row in response.json().get("models", []) if row.get("visibility") == "list"]


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "login":
        login()
    elif command == "models":
        print(json.dumps(models(), indent=1))
    else:
        record = load()
        print(json.dumps({"client_id": record["client_id"], "email": record.get("email"), "scopes": record["scopes"],
                          "plan_scope_granted": PLAN_SCOPE in record["scopes"],
                          "expires_in_s": int(record["saved_at"] + record["expires_in"] - time.time())}, indent=1))
