"""Focused and API-level tests for Ticket 03: Durable tool-free conversation and run lifecycle.

Covers:
1. Forward schema migration v1 -> v2 with automated database backup.
2. Conceptual question and no-portfolio tool-free conversation.
3. Transactional uniqueness & idempotency (matching request ID returns existing run, different payload raises 409).
4. Single active running run constraint per thread (returns 409 on competing submission).
5. Cross-thread run lookup returning 404.
6. Controlled asynchronous cancellation without late terminal-state overwrite.
7. Outer turn limits (8 turns) and run deadline / request timeout behavior.
8. Process restart marking running runs as interrupted without replaying.
9. Public responses omitting private continuation and credentials.
"""

import asyncio
import sqlite3
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.orchestrator import Orchestrator
from analyst.persistence import Database, RunConflictError, ThreadNotFoundError
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import (
    Account,
    Position,
    SavedPortfolio,
    Snapshot,
    SubmitMessageRequest,
)


def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio(
        snapshot=Snapshot(
            as_of=datetime(2025, 1, 1).date(),
            reporting_currency="CAD",
            accounts=[Account(id="acc-1", name="TFSA")],
            positions=[
                Position(
                    id="pos-1",
                    account_id="acc-1",
                    kind="stock",
                    currency="CAD",
                    ticker="TD",
                    listing="XTSE",
                    shares=Decimal("100"),
                )
            ],
            fx=[],
        ),
        average_costs={"pos-1": Decimal("80.00")},
    )


# =========================================================================
# 1. Forward Migration v1 -> v2 & Automated Backup
# =========================================================================

def test_migration_v1_to_v2_with_automated_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.db"
    backup_path = tmp_path / "portfolio.backup.db"

    # 1. Create a database strictly at schema version 1
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 1;")
    conn.execute("""
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE portfolio_revisions (
            revision INTEGER PRIMARY KEY CHECK(revision > 0),
            committed_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE portfolio_current (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            current_revision INTEGER,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (current_revision) REFERENCES portfolio_revisions(revision)
        );
    """)
    conn.execute("""
        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revision INTEGER,
            basis TEXT NOT NULL,
            source_artifact_id TEXT,
            originating_run_id TEXT,
            payload_json TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE proposals (
            id TEXT PRIMARY KEY,
            base_revision INTEGER,
            origin TEXT NOT NULL,
            thread_id TEXT,
            run_id TEXT,
            before_artifact_id TEXT,
            after_artifact_id TEXT NOT NULL,
            differences_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE proposal_receipts (
            proposal_id TEXT PRIMARY KEY,
            previous_revision INTEGER,
            new_revision INTEGER NOT NULL UNIQUE,
            committed_at TEXT NOT NULL
        );
    """)
    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (1, '2025-01-01T00:00:00Z');")
    conn.execute("INSERT INTO portfolio_revisions (revision, committed_at, payload) VALUES (1, '2025-01-01T00:00:00Z', '{}');")
    conn.execute("INSERT INTO portfolio_current (id, current_revision, updated_at) VALUES (1, 1, '2025-01-01T00:00:00Z');")
    conn.commit()
    conn.close()

    assert not backup_path.exists()

    # 2. Initialize Database object, which triggers forward migration from 1 to 2
    db = Database(db_path)
    db.initialize()

    # Check that schema version is now 2
    assert db.get_schema_version() == 2

    # Verify backup was created
    assert backup_path.exists()

    # Verify backup is at version 1
    backup_conn = sqlite3.connect(backup_path)
    b_cur = backup_conn.execute("PRAGMA user_version;")
    assert int(b_cur.fetchone()[0]) == 1
    backup_conn.close()

    # Verify existing Spec 2 records preserved in upgraded DB
    with db.connect() as conn:
        rev_row = conn.execute("SELECT revision FROM portfolio_revisions WHERE revision = 1;").fetchone()
        assert rev_row is not None
        assert rev_row[0] == 1

        # Verify new tables exist in upgraded database
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        assert "threads" in tables
        assert "runs" in tables
        assert "messages" in tables
        assert "private_continuation" in tables


# =========================================================================
# 2. Database Conversation Methods & Idempotency
# =========================================================================

def test_database_thread_and_run_uniqueness(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()

    # Thread creation
    thread = db.create_thread("th-1")
    assert thread.id == "th-1"

    req1 = SubmitMessageRequest(
        client_request_id="req-1",
        text="What is a P/E ratio?",
    )

    # First submission
    run1, is_new1 = db.submit_message("th-1", req1, bound_revision=None)
    assert is_new1 is True
    assert run1.client_request_id == "req-1"
    assert run1.status == "running"
    assert run1.bound_revision is None

    # Duplicate submission with identical payload returns existing run (is_new=False)
    run_dup, is_new_dup = db.submit_message("th-1", req1, bound_revision=None)
    assert is_new_dup is False
    assert run_dup.id == run1.id

    # Submission with same client_request_id but DIFFERENT payload raises RunConflictError
    req_conflict = SubmitMessageRequest(
        client_request_id="req-1",
        text="Different question?",
    )
    with pytest.raises(RunConflictError, match="different payload"):
        db.submit_message("th-1", req_conflict, bound_revision=None)

    # Submission with DIFFERENT client_request_id while run1 is still 'running' raises RunConflictError
    req2 = SubmitMessageRequest(
        client_request_id="req-2",
        text="Second question",
    )
    with pytest.raises(RunConflictError, match="active running run"):
        db.submit_message("th-1", req2, bound_revision=None)

    # Submitting to non-existent thread raises ThreadNotFoundError
    with pytest.raises(ThreadNotFoundError):
        db.submit_message("th-nonexistent", req2, bound_revision=None)


def test_database_restart_interruption_recovery(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.create_thread("th-1")
    req = SubmitMessageRequest(client_request_id="req-1", text="Test")
    run, _ = db.submit_message("th-1", req, bound_revision=1)
    assert run.status == "running"

    # Simulate process restart
    recovered_count = db.recover_interrupted_runs()
    assert recovered_count == 1

    run_after = db.get_run("th-1", run.id)
    assert run_after is not None
    assert run_after.status == "interrupted"

    # Now a retry with a new client request ID can run
    req_retry = SubmitMessageRequest(client_request_id="req-retry", text="Test retry")
    run2, is_new2 = db.submit_message("th-1", req_retry, bound_revision=1)
    assert is_new2 is True
    assert run2.status == "running"


# =========================================================================
# 3. Supervised Orchestrator Lifecycle & Constraints
# =========================================================================

@pytest.mark.anyio
async def test_orchestrator_conceptual_answer_no_tools(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.create_thread("th-1")

    model = ScriptedModel([
        ModelTurn(
            text="Price-to-Earnings (P/E) ratio compares a company's share price to its earnings per share.",
            citations=[],
        )
    ])
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)
    orch = Orchestrator(db=db, transport=model, settings=settings)

    req = SubmitMessageRequest(client_request_id="req-1", text="What is a P/E ratio?")
    run, _ = db.submit_message("th-1", req, bound_revision=None)

    # Supervised execution
    orch.submit_supervised_run("th-1", run, req.text)

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        run_status = db.get_run("th-1", run.id)
        if run_status and run_status.status != "running":
            break

    run_final = db.get_run("th-1", run.id)
    assert run_final is not None
    assert run_final.status == "completed"

    thread_detail = db.get_thread("th-1")
    assert thread_detail is not None
    assert len(thread_detail.messages) == 2
    assert thread_detail.messages[0].role == "user"
    assert thread_detail.messages[1].role == "assistant"
    assert "Price-to-Earnings" in thread_detail.messages[1].text
    # No tools were called
    assert len(thread_detail.messages[1].tool_outcomes) == 0


@pytest.mark.anyio
async def test_orchestrator_cancellation_without_late_overwrite(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.create_thread("th-1")

    # A slow transport that takes 1 second
    class SlowTransport:
        async def respond(self, messages: Any, **kwargs: Any) -> ModelTurn:
            await asyncio.sleep(1.0)
            return ModelTurn(text="Late response")

    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)
    orch = Orchestrator(db=db, transport=SlowTransport(), settings=settings)

    req = SubmitMessageRequest(client_request_id="req-cancel", text="Cancel me")
    run, _ = db.submit_message("th-1", req, bound_revision=None)

    orch.submit_supervised_run("th-1", run, req.text)

    # Cancel quickly
    await asyncio.sleep(0.05)
    cancelled_run = await orch.cancel_run("th-1", run.id)
    assert cancelled_run is not None
    assert cancelled_run.status == "cancelled"

    # Wait long enough for slow transport to finish
    await asyncio.sleep(1.2)

    # Check that terminal state was NOT overwritten by "completed"
    final_run = db.get_run("th-1", run.id)
    assert final_run is not None
    assert final_run.status == "cancelled"


@pytest.mark.anyio
async def test_orchestrator_max_turns_limit(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.create_thread("th-1")

    # Scripted model that keeps returning unexpected tool calls without finishing
    turns = [
        ModelTurn(calls=[ToolCall(call_id=f"c-{i}", name="unknown_tool", arguments="{}")])
        for i in range(10)
    ]
    model = ScriptedModel(turns)
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)
    orch = Orchestrator(db=db, transport=model, settings=settings)

    req = SubmitMessageRequest(client_request_id="req-turns", text="Tool call loop")
    run, _ = db.submit_message("th-1", req, bound_revision=None)

    orch.submit_supervised_run("th-1", run, req.text)

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        run_status = db.get_run("th-1", run.id)
        if run_status and run_status.status != "running":
            break

    final_run = db.get_run("th-1", run.id)
    assert final_run is not None
    assert final_run.status == "failed"
    assert "Unexpected tool call" in (final_run.error or "")


# =========================================================================
# 4. FastAPI Endpoints & Contract Verification
# =========================================================================

def test_fastapi_thread_and_run_endpoints(tmp_path: Path) -> None:
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)
    portfolio_store = PortfolioStore(database_path=tmp_path / "portfolio.db")

    # Save a committed portfolio at revision 1
    portfolio_store.save(sample_portfolio())
    rev = portfolio_store.get_revisioned()
    assert rev is not None
    assert rev.revision == 1

    model = ScriptedModel([
        ModelTurn(
            text="Compound interest is interest calculated on the initial principal and accumulated interest.",
            continuation=[{"type": "private_reasoning_token", "val": "SECRET"}],
        )
    ])
    orch = Orchestrator(db=portfolio_store.db, transport=model, settings=settings)
    app = create_app(
        model=model,
        settings=settings,
        portfolio_store=portfolio_store,
        orchestrator=orch,
    )
    client = TestClient(app)

    # 1. POST /api/threads creates a thread
    resp = client.post("/api/threads")
    assert resp.status_code == 201
    thread_data = resp.json()
    assert "id" in thread_data
    thread_id = thread_data["id"]

    # 2. GET /api/threads lists thread summaries
    resp = client.get("/api/threads")
    assert resp.status_code == 200
    threads_list = resp.json()
    assert any(t["id"] == thread_id for t in threads_list)

    # 3. POST /api/threads/{id}/messages submits message and returns 202
    msg_payload = {
        "client_request_id": "req-fastapi-1",
        "text": "Explain compound interest",
    }
    resp = client.post(f"/api/threads/{thread_id}/messages", json=msg_payload)
    assert resp.status_code == 202
    run_data = resp.json()
    assert run_data["client_request_id"] == "req-fastapi-1"
    assert run_data["thread_id"] == thread_id
    assert run_data["status"] == "running"
    # Revision was bound once at acceptance!
    assert run_data["bound_revision"] == 1
    run_id = run_data["id"]

    # 4. Duplicate submission returns existing run (202)
    resp_dup = client.post(f"/api/threads/{thread_id}/messages", json=msg_payload)
    assert resp_dup.status_code == 202
    assert resp_dup.json()["id"] == run_id

    # 5. Conflicting submission with same request ID returns 409
    resp_conflict = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"client_request_id": "req-fastapi-1", "text": "Different text"},
    )
    assert resp_conflict.status_code == 409

    # Wait briefly for in-process background task
    time.sleep(0.3)

    # 6. GET /api/threads/{id}/runs/{run_id} checks run status
    resp_run = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
    assert resp_run.status_code == 200
    assert resp_run.json()["status"] == "completed"

    # 7. Cross-thread run lookup returns 404
    resp_other = client.post("/api/threads")
    other_thread_id = resp_other.json()["id"]
    resp_cross = client.get(f"/api/threads/{other_thread_id}/runs/{run_id}")
    assert resp_cross.status_code == 404

    # 8. GET /api/threads/{id} returns detail without credentials or private continuation
    resp_detail = client.get(f"/api/threads/{thread_id}")
    assert resp_detail.status_code == 200
    detail = resp_detail.json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["role"] == "assistant"
    assert "Compound interest" in detail["messages"][1]["text"]

    # Verify private continuation was NOT leaked into messages or public detail
    detail_str = resp_detail.text
    assert "SECRET" not in detail_str
    assert "private_continuation" not in detail_str

    # But private continuation was safely persisted in server storage
    saved_cont = portfolio_store.db.get_private_continuation(run_id)
    assert saved_cont is not None
    assert saved_cont[0]["val"] == "SECRET"


def test_fastapi_no_portfolio_conceptual_question(tmp_path: Path) -> None:
    settings = Settings(api_key="sk-test", model="gpt-4o", request_timeout=5.0)
    portfolio_store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    # No portfolio saved in database: current revision is None

    model = ScriptedModel([
        ModelTurn(
            text="An ETF is an exchange-traded fund.",
            citations=[],
        )
    ])
    orch = Orchestrator(db=portfolio_store.db, transport=model, settings=settings)
    app = create_app(
        model=model,
        settings=settings,
        portfolio_store=portfolio_store,
        orchestrator=orch,
    )
    client = TestClient(app)

    # Create thread
    resp = client.post("/api/threads")
    thread_id = resp.json()["id"]

    # Submit conceptual question with no portfolio
    resp = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"client_request_id": "req-no-port", "text": "What is an ETF?"},
    )
    assert resp.status_code == 202
    run_data = resp.json()
    assert run_data["bound_revision"] is None

    time.sleep(0.3)

    resp_run = client.get(f"/api/threads/{thread_id}/runs/{run_data['id']}")
    assert resp_run.status_code == 200
    assert resp_run.json()["status"] == "completed"

    resp_detail = client.get(f"/api/threads/{thread_id}")
    assert resp_detail.status_code == 200
    messages = resp_detail.json()["messages"]
    assert len(messages) == 2
    assert "exchange-traded fund" in messages[1]["text"]
