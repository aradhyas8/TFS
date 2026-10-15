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


# Models offered in the selector. The configured OPENROUTER_MODEL is listed first when it is not one of these.
OPENROUTER_MODELS = {"deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash", "nvidia/nemotron-3-super-120b-a12b:free": "Nemotron 3 Super Free"}
OPENROUTER_CATALOG = "https://openrouter.ai/api/v1/models"


def openrouter_problem(model: str, reasoning: bool) -> str | None:
    """Why OpenRouter cannot run this model for TFS, or None. Reads the public catalog; no credential is sent."""
    response = httpx.get(OPENROUTER_CATALOG, timeout=15)
    response.raise_for_status()
    row = next((item for item in response.json()["data"] if item["id"] == model), None)
    if row is None:
        return f"OpenRouter does not offer {model} now. Choose another model."
    missing = {"tools", "tool_choice", "structured_outputs", *(["reasoning"] if reasoning else [])} - set(row.get("supported_parameters") or [])
    if missing:
        return f"{model} does not support {', '.join(sorted(missing))} on OpenRouter, which TFS analysis needs. Choose another model."
    return None


STORAGE_ERRORS = {
    "unsupported_storage": "Use Windows or macOS for ChatGPT sign-in, or choose the API-key provider.",
    "keychain_locked": "Unlock the macOS login Keychain and allow TFS to use it, then retry.",
    "keychain_error": "The macOS Keychain refused the ChatGPT credentials. Check Keychain Access, then reconnect.",
}


class Connection:
    def __init__(self, settings: Settings, *, managed: bool = True) -> None:
        self.initial = settings
        self.managed = managed
        self.api_settings = settings if settings.provider != "chatgpt_plan" else replace(
            settings, provider="openai", api_key=os.environ.get("OPENAI_API_KEY", ""), model=os.environ.get("OPENAI_MODEL", ""))
        self.openrouter = settings if settings.openrouter else replace(
            settings, provider="openrouter", base_url="https://openrouter.ai/api/v1",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""), model=os.environ.get("OPENROUTER_MODEL", ""))
        self.provider = settings.provider
        self.model = settings.model
        self.task: asyncio.Task[None] | None = None
        self.cancel = threading.Event()
        self.error: str | None = None
        self.error_state = "failed"
        self.catalog: list[dict[str, Any]] = []
        try:
            # The choice saved from the UI outlives restarts; .env only sets the first-run default.
            saved = json.loads((plan.home() / "selection.json").read_text())
            if saved["provider"] in {"chatgpt_plan", "openrouter", self.api_settings.provider}:
                self.provider = saved["provider"]
                self.model = saved.get("model") or (self.openrouter.model if self.provider == "openrouter" else self.api_settings.model
                                                   if self.provider != "chatgpt_plan" else "")
        except (OSError, ValueError, KeyError):
            pass

    def status(self) -> dict[str, Any]:
        state, message, account = "disconnected", "Connect your ChatGPT plan for AI questions.", None
        if sys.platform not in plan.SUPPORTED_PLATFORMS:
            state, message = "unavailable", "ChatGPT sign-in needs Windows DPAPI or the macOS Keychain. Use the API-key provider here. WSL/Linux storage is not supported."
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
                    state, message = "failed", STORAGE_ERRORS.get(
                        exc.code, "Saved ChatGPT connection could not be read. Reconnect on the computer account that created it.")
            except (OSError, ValueError, KeyError):
                state, message = "failed", "Saved connection is unavailable. Reconnect to recover."
        if self.task and not self.task.done():
            state, message = "connecting", "Complete sign-in in the browser window."
        elif self.error:
            state, message = self.error_state, self.error
        # state/message describe the provider that answers AI questions; chatgpt_state drives the sign-in controls.
        chatgpt_state, active_state = state, state
        if self.provider != "chatgpt_plan":
            ready = bool(self.openrouter.api_key and self.model) if self.provider == "openrouter" else self.api_ready
            active_state = "connected" if ready else "failed"
            label = "OpenRouter" if self.provider == "openrouter" else "API-key provider"
            api_message = (f"{label} ready. AI questions use {self.model}." if ready
                           else "The API-key provider is not configured. Follow API-key setup in README.md, then restart TFS.")
            message = f"{api_message} ChatGPT: {message}" if chatgpt_state == "connecting" or self.error else api_message
        return {"provider": self.provider, "model": self.model, "state": active_state, "chatgpt_state": chatgpt_state,
                "message": message, "account": account, "models": self.catalog,
                "api_provider": self.api_settings.provider, "api_key_available": self.api_ready,
                "openrouter_models": self.openrouter_models(), "openrouter_available": bool(self.openrouter.api_key)}

    def openrouter_models(self) -> list[dict[str, str]]:
        configured = self.openrouter.model
        models = ({configured: configured} if configured and configured not in OPENROUTER_MODELS else {}) | OPENROUTER_MODELS
        return [{"slug": slug, "display_name": name} for slug, name in models.items()]

    @property
    def api_ready(self) -> bool:
        return bool(self.api_settings.api_key and self.api_settings.model)

    def resolve(self) -> Settings:
        if not self.managed:
            return self.initial
        if self.provider == "openrouter":
            if not self.openrouter.api_key:
                raise HTTPException(503, "Add OPENROUTER_API_KEY to backend/.env to use OpenRouter, or select ChatGPT.")
            return replace(self.openrouter, model=self.model)
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
                **STORAGE_ERRORS,
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
            raise HTTPException(409 if exc.code == "login_busy" else 503, STORAGE_ERRORS.get(
                exc.code, "ChatGPT sign-out is unavailable. Finish any other sign-in and retry.")) from None
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
                "browser_unavailable": "The sign-in browser could not open. Run TFS in an interactive desktop session and retry.",
                **STORAGE_ERRORS,
            }.get(exc.code, "Sign-in failed. Continue with ChatGPT to try again.")
        except Exception:
            self.error = "Sign-in failed. Continue with ChatGPT to try again."
