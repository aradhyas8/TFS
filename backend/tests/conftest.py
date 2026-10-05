import socket

import pytest


@pytest.fixture(autouse=True)
def no_live_services(monkeypatch):
    original_connect = socket.socket.connect

    def blocked(sock, address):
        # Windows asyncio implements socketpair over loopback TCP.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("Automated tests must not make live network connections.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-backend-only-never-browser")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("BOC_FX_ENABLED", "false")
    monkeypatch.delenv("FINANCIAL_REFERENCE_FILE", raising=False)

    monkeypatch.delenv("RESEARCH_REFERENCE_FILE", raising=False)
