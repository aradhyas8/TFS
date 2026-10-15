"""Local connection state. OAuth secrets never form part of the HTTP contract."""
import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import replace
from typing import Any

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from . import chatgpt_plan as plan
from .config import Settings


class ProviderChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str = ""


def local_request(request: Request) -> None:
    # Forwarded headers cannot establish local trust. The launcher binds both servers to loopback.
    from urllib.parse import urlsplit
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "AI connections are available only on this computer.")
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and any(part.strip() not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"} for part in forwarded.split(",")):
        raise HTTPException(403, "AI connections are available only on this computer.")
    host = urlsplit(f"http://{request.headers.get('host', '')}").hostname
    if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
        raise HTTPException(403, "Use the local TFS address.")
    origin = request.headers.get("origin")
    allowed = {"http://localhost:3000", "http://127.0.0.1:3000", "http://127.0.0.1:8000"}
    if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Open AI connections from the local TFS page.")
    if request.method != "GET" and (origin not in allowed or request.headers.get("x-tfs-local") != "1"):
        raise HTTPException(403, "Use the local TFS connection controls.")


class Connection:
    def __init__(self, settings: Settings, *, managed: bool = True) -> None:
        self.initial = settings
        self.managed = managed
        self.api_settings = settings if settings.provider != "chatgpt_plan" else replace(
            settings, provider="openai", api_key=os.environ.get("OPENAI_API_KEY", ""), model=os.environ.get("OPENAI_MODEL", ""))
        self.provider = settings.provider
        self.model = settings.model
        self.task: asyncio.Task[None] | None = None
        self.cancel = threading.Event()
        self.error: str | None = None
        self.error_state = "failed"
        self.catalog: list[dict[str, Any]] = []
        try:
            saved = json.loads((plan.home() / "selection.json").read_text())
            if not os.environ.get("LLM_PROVIDER", "").strip():
                self.provider = saved["provider"] if saved["provider"] in {"chatgpt_plan", self.api_settings.provider} else self.provider
            if self.provider == "chatgpt_plan" and saved.get("provider") == self.provider:
                self.model = saved.get("model", self.model)
        except (OSError, ValueError, KeyError):
            pass

    def status(self) -> dict[str, Any]:
        state, message, account = "disconnected", "Connect your ChatGPT plan for AI questions.", None
        if sys.platform != "win32":
            state, message = "unavailable", "ChatGPT sign-in needs native Windows and DPAPI. Run TFS in Windows, or use the API-key provider here. WSL/Linux storage is not supported."
        else:
            try:
                record = plan.load()
                account = record.get("email") or "Saved ChatGPT account"
                if plan.PLAN_SCOPE not in record.get("scopes", []):
                    state, message = "failed", "ChatGPT plan permission is missing. Reconnect and grant plan usage."
                elif record["saved_at"] + record["expires_in"] <= time.time():
                    state, message = "expired", "The access token expired. Reconnect, or refresh the connection to renew the session."
                else:
                    state, message = "connected", "ChatGPT connected. Select a supported model for AI questions."
            except plan.PlanError as exc:
                if exc.code != "not_signed_in":
                    state, message = "failed", "Saved ChatGPT connection could not be read. Reconnect on the Windows account that created it."
            except (OSError, ValueError, KeyError):
                state, message = "failed", "Saved connection is unavailable. Reconnect to recover."
        if self.task and not self.task.done():
            state, message = "connecting", "Complete sign-in in the browser window."
        elif self.error:
            state, message = self.error_state, self.error
        return {"provider": self.provider, "model": self.model, "state": state, "message": message,
                "account": account, "models": self.catalog,
                "api_provider": self.api_settings.provider, "api_key_available": bool(self.api_settings.api_key and self.api_settings.model)}

    def resolve(self) -> Settings:
        if not self.managed:
            return self.initial
        if self.provider != "chatgpt_plan":
            return self.api_settings
        try:
            token = plan.access_token()
            if not self.model or self.model not in {row["slug"] for row in self.catalog}:
                self.catalog = plan.models()
            if not self.model or self.model not in {row["slug"] for row in self.catalog}:
                raise HTTPException(503, "Connect ChatGPT and select an available model in AI connection.")
            self.error = None
            return replace(self.initial, provider="chatgpt_plan", api_key=token, model=self.model, base_url=plan.RESOURCE)
        except (plan.PlanError, httpx.HTTPError, OSError, ValueError, KeyError):
            raise HTTPException(503, "ChatGPT connection unavailable. Reconnect in AI connection, or select the API-key provider.") from None

    async def refresh(self) -> dict[str, Any]:
        self.catalog = []
        self.error_state = "failed"
        try:
            self.catalog = await asyncio.to_thread(plan.models)
            self.error = None
        except plan.PlanError as exc:
            self.error = {
                "not_signed_in": "Continue with ChatGPT to connect your account.",
                "plan_scope_missing": "Plan permission is missing. Reconnect and grant ChatGPT plan usage.",
                "invalid_grant": "Session expired or revoked. Reconnect with ChatGPT.",
                "unsupported_storage": "Use native Windows for ChatGPT sign-in, or choose the API-key provider.",
            }.get(exc.code, "ChatGPT connection failed. Reconnect to recover.")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            self.error_state = "unavailable" if code >= 500 else "expired" if code == 401 else "failed"
            self.error = (f"Your login is saved, but OpenAI's model catalog is unavailable (HTTP {code}). Use Refresh connection to retry; you do not need to sign in again."
                          if code >= 500 else f"OpenAI rejected the model catalog request (HTTP {code}). Reconnect ChatGPT and check plan permissions.")
        except httpx.RequestError:
            self.error_state = "unavailable"
            self.error = "Your login is saved, but the backend could not reach OpenAI's model catalog. Check the connection and use Refresh connection to retry."
        except (OSError, ValueError, KeyError):
            self.error = "The saved connection or model catalog could not be read. Use Refresh connection to retry."
        return self.status()

    async def sign_out(self) -> dict[str, Any]:
        try:
            revoked = await asyncio.to_thread(plan.logout)
        except plan.PlanError as exc:
            raise HTTPException(409 if exc.code == "login_busy" else 503,
                                "ChatGPT sign-out is unavailable. Finish any other sign-in and retry on native Windows.") from None
        self.catalog = []
        if self.provider == "chatgpt_plan":
            self.model = ""
        self.managed = True
        if self.initial.provider == "chatgpt_plan":
            self.initial = replace(self.initial, api_key="")
        self.error_state = "disconnected"
        self.error = (None if revoked else "Signed out locally. Remote revocation was not confirmed. Disconnect Greedy/TFS in ChatGPT Settings if needed.")
        await asyncio.to_thread(plan._write, plan.home() / "selection.json",
                                json.dumps({"provider": self.provider, "model": self.model}).encode())
        return self.status()

    async def sign_in(self) -> None:
        try:
            await asyncio.to_thread(plan.login, self.cancel)
            await self.refresh()
        except plan.PlanError as exc:
            self.error = {
                "callback_port_busy": "Sign-in callback port 1455 is occupied. Close the other login and retry.",
                "login_busy": "Another TFS sign-in is in progress. Finish or cancel it first.",
                "cancelled": "Sign-in cancelled. Continue with ChatGPT to try again.",
                "timeout": "Sign-in timed out. Continue with ChatGPT to try again.",
                "plan_scope_missing": "Plan permission was not granted. Reconnect and allow ChatGPT plan usage.",
                "browser_unavailable": "The sign-in browser could not open. Run TFS in an interactive Windows session and retry.",
            }.get(exc.code, "Sign-in failed. Continue with ChatGPT to try again.")
        except Exception:
            self.error = "Sign-in failed. Continue with ChatGPT to try again."
