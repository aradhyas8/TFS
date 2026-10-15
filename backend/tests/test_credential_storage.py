"""ChatGPT credential storage on the real OS store: Windows DPAPI files or the macOS login Keychain."""
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from analyst import chatgpt_plan as plan

native = pytest.mark.skipif(sys.platform not in plan.SUPPORTED_PLATFORMS, reason="needs Windows DPAPI or the macOS Keychain")
RECORD = {"client_id": "client-storage-test", "subject": "user-1", "email": "investor@example.test",
          "access_token": "dummy-access-secret", "refresh_token": "dummy-refresh-secret", "id_token": "dummy-id",
          "scopes": [plan.PLAN_SCOPE], "expires_in": 3600, "saved_at": time.time()}


@native
def test_tokens_round_trip_through_the_os_store_and_never_reach_a_plaintext_file():
    plan._save_host({"ext_agent_host_id": "urn:uuid:test", "client_id": RECORD["client_id"]})
    try:
        plan.save(RECORD)
        assert plan.load() == RECORD
        rotated = {**RECORD, "refresh_token": "dummy-rotated-secret"}
        plan.save(rotated)
        assert plan.load()["refresh_token"] == "dummy-rotated-secret"
        for path in plan.home().rglob("*"):
            if path.is_file():
                assert b"dummy-" not in path.read_bytes(), path
    finally:
        plan._delete_record(RECORD["client_id"])
    with pytest.raises(plan.PlanError) as missing:
        plan.load()
    assert missing.value.code == "not_signed_in"


@native
def test_a_second_process_cannot_start_a_login_while_one_is_running():
    script = ("import sys\nfrom analyst import chatgpt_plan as plan\n"
              "try:\n    plan.login_lock().__enter__()\nexcept plan.PlanError as exc:\n    sys.exit(0 if exc.code == 'login_busy' else 2)\n"
              "sys.exit(1)")
    with plan.login_lock():
        other = subprocess.run([sys.executable, "-c", script], timeout=30)
    assert other.returncode == 0
    with plan.login_lock():  # released
        pass


@native
def test_refresh_rotation_waits_for_the_other_process():
    script = ("import time\nfrom analyst import chatgpt_plan as plan\n"
              "with plan.credential_lock():\n    print('locked', flush=True)\n    time.sleep(1)\n")
    holder = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with plan.credential_lock():
            waited = time.monotonic() - started
    finally:
        holder.wait(timeout=30)
    assert waited > 0.5


def test_a_credential_write_survives_a_concurrent_reader():
    path = plan.home() / "host.json"
    plan._write(path, b"{}")
    reader = open(path, "rb")  # Windows refuses os.replace while this handle is open
    threading.Timer(0.2, reader.close).start()
    plan._write(path, b'{"client_id": null}')
    assert path.read_bytes() == b'{"client_id": null}'


@pytest.mark.parametrize(("error", "code"), [("KeyringLocked", "keychain_locked"), ("KeyringError", "keychain_error")])
def test_keychain_failures_are_reported_without_secrets(monkeypatch, error, code):
    """Mocked Security API: real Keychain behaviour is covered by the native tests on macOS."""
    class KeyringError(Exception):
        pass

    class KeyringLocked(KeyringError):
        pass

    raised = {"KeyringError": KeyringError, "KeyringLocked": KeyringLocked}[error]

    class Keyring:
        def get_password(self, service, account):
            raise raised("status -25308 dummy-access-secret")

    backends, errors = ModuleType("keyring.backends.macOS"), ModuleType("keyring.errors")
    backends.Keyring, errors.KeyringError, errors.KeyringLocked = Keyring, KeyringError, KeyringLocked
    monkeypatch.setitem(sys.modules, "keyring.backends.macOS", backends)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    monkeypatch.setattr(plan, "sys", SimpleNamespace(platform="darwin"))
    plan._save_host({"ext_agent_host_id": "urn:uuid:test", "client_id": "client-1"})
    with pytest.raises(plan.PlanError) as failure:
        plan.load()
    assert failure.value.code == code
    assert "dummy-access-secret" not in str(failure.value)
    assert "Keychain" in str(failure.value)
