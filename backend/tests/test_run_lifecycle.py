"""Ticket 12 cancellation, revision basis and hypothetical runs at the FastAPI request/result seam."""

import asyncio
import json
import sqlite3
import threading
import time
from decimal import Decimal

from analyst.financial_data import FakeFinancialProvider
from analyst.persistence import SCHEMA_VERSION, Database
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall
from analyst.schemas import SubmitMessageRequest
from tests.test_context_retrieval import CapturingTransport
from tests.test_financial_tools import api, evidence, portfolio

HOLDINGS = ("get_holdings", {"offset": 0, "limit": 100})
EXPOSURE = ("review_portfolio", {})
SET_25 = ("prepare_portfolio_proposal",
          {"edits": [{"action": "set_quantity", "position_id": "stock", "quantity": "25"}]})


def call(tool, id="call"):
    name, arguments = tool
    return ModelTurn(calls=[ToolCall(id, name, json.dumps(arguments))])


class Gated(CapturingTransport):
    """Provider work that ends only when the test releases it. A turn may be a callable run as the answer arrives."""

    def __init__(self, turns, released=True):
        super().__init__(turns)
        self.release = threading.Event()
        if released:
            self.release.set()

    async def respond(self, messages, **kwargs):
        self.requests.append({"messages": json.loads(json.dumps(messages)), **kwargs})
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        turn = next(self.turns)
        return turn() if callable(turn) else turn


def desk(tmp_path, turns, released=True):
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(portfolio())
    model = Gated(turns, released)
    return api(store, model, FakeFinancialProvider(evidence())), model, store


def until(condition):
    for _ in range(400):
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("Condition was not reached")


def accept(client, thread, text, request_id="req-1", **extra):
    response = client.post(f"/api/threads/{thread}/messages",
                           json={"text": text, "client_request_id": request_id, **extra})
    assert response.status_code == 202, response.text
    return response.json()


def ask(client, thread, text, request_id="req-1", **extra):
    """Sends one message and waits for its run to end."""
    run = accept(client, thread, text, request_id, **extra)
    url = f"/api/threads/{thread}/runs/{run['id']}"
    until(lambda: client.get(url).json()["status"] != "running")
    return client.get(url).json()


def texts(client, thread):
    return [m["text"] for m in client.get(f"/api/threads/{thread}").json()["messages"]]


def tool_output(model, request):
    return json.loads(next(m["output"] for m in reversed(model.requests[request]["messages"])
                           if m.get("type") == "function_call_output"))


def shares(rows, position_id="stock"):
    return Decimal(str(next(row for row in rows if row["id"] == position_id)["shares"]))


def draft(**changes):
    """An edited copy of the saved portfolio, as the holdings editor would capture it."""
    edited = portfolio()
    edited.snapshot.positions[0].shares = Decimal(changes.get("shares", "99"))
    edited.snapshot.positions[1].cash = Decimal(changes.get("cash", "5"))
    return edited.model_dump(mode="json")


def capture(client, **changes):
    response = client.post("/api/portfolio/artifacts", json={"portfolio": draft(**changes)})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def committed(client):
    saved = client.get("/api/portfolio").json()
    return saved["revision"], shares(saved["portfolio"]["snapshot"]["positions"])


# Cancellation


def test_stop_cancels_the_run_and_nothing_is_answered_for_it(tmp_path):
    client, model, _ = desk(tmp_path, [ModelTurn(text="Answer to the next question.")], released=False)
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = accept(client, thread, "Slow question")
        until(lambda: model.requests)
        stopped = client.post(f"/api/threads/{thread}/runs/{run['id']}/cancel")
        assert (stopped.status_code, stopped.json()["status"]) == (200, "cancelled")
        model.release.set()
        assert ask(client, thread, "Next question", "req-2")["status"] == "completed"
        assert texts(client, thread) == ["Slow question", "Next question", "Answer to the next question."]
        assert [r["status"] for r in client.get(f"/api/threads/{thread}").json()["runs"]] == ["cancelled", "completed"]


def test_stop_landing_as_the_provider_answers_stores_no_late_answer(tmp_path):
    # The stop is recorded in the instant uninterruptible provider work returns its answer.
    state = {}

    def answer_after_stop():
        for run in state["store"].db.get_thread(state["thread"]).runs:
            state["store"].db.cancel_run(state["thread"], run.id)
        return ModelTurn(text="Late answer.")

    client, model, state["store"] = desk(tmp_path, [answer_after_stop, ModelTurn(text="Second answer.")])
    with client:
        state["thread"] = thread = client.post("/api/threads").json()["id"]
        assert ask(client, thread, "Stopped question")["status"] == "cancelled"
        assert ask(client, thread, "Next question", "req-2")["status"] == "completed"
        assert texts(client, thread) == ["Stopped question", "Next question", "Second answer."]
        first = client.get(f"/api/threads/{thread}").json()["runs"][0]
        assert (first["status"], len(first["message_ids"])) == ("cancelled", 1)


def test_stopped_run_cannot_prepare_a_portfolio_change(tmp_path):
    state = {}

    def tool_call_after_stop():
        for run in state["store"].db.get_thread(state["thread"]).runs:
            state["store"].db.cancel_run(state["thread"], run.id)
        return call(SET_25)

    client, model, state["store"] = desk(tmp_path, [tool_call_after_stop, ModelTurn(text="Second answer.")])
    with client:
        state["thread"] = thread = client.post("/api/threads").json()["id"]
        assert ask(client, thread, "I now hold 25 Acme shares.")["status"] == "cancelled"
        assert ask(client, thread, "Next question", "req-2")["status"] == "completed"
        assert texts(client, thread) == ["I now hold 25 Acme shares.", "Next question", "Second answer."]
        assert client.get("/api/portfolio/proposals").json() == []
        assert committed(client) == (1, 10)


def test_stopping_one_run_leaves_other_runs_and_pending_proposals_alone(tmp_path):
    client, model, _ = desk(tmp_path, [call(SET_25), ModelTurn(text="Preview ready."), ModelTurn(text="Other answer.")])
    with client:
        first = client.post("/api/threads").json()["id"]
        other = client.post("/api/threads").json()["id"]
        prepared = ask(client, first, "I now hold 25 Acme shares.")
        assert prepared["status"] == "completed"
        model.release.clear()
        stopping = accept(client, first, "Slow question", "req-2")
        running = accept(client, other, "Other question")
        until(lambda: len(model.requests) == 4)

        # A run is stopped only through its own conversation: naming it under another thread is refused
        # and must not interrupt its work either.
        assert client.post(f"/api/threads/{first}/runs/{running['id']}/cancel").status_code == 404
        assert client.post(f"/api/threads/{other}/runs/{stopping['id']}/cancel").status_code == 404
        assert client.get(f"/api/threads/{first}/runs/{stopping['id']}").json()["status"] == "running"
        assert client.get(f"/api/threads/{other}/runs/{running['id']}").json()["status"] == "running"

        assert client.post(f"/api/threads/{first}/runs/{stopping['id']}/cancel").json()["status"] == "cancelled"
        assert client.get(f"/api/threads/{other}/runs/{running['id']}").json()["status"] == "running"
        model.release.set()
        until(lambda: client.get(f"/api/threads/{other}/runs/{running['id']}").json()["status"] == "completed")
        # The run that was wrongly named kept working, and its answer is stored exactly once.
        assert texts(client, other) == ["Other question", "Other answer."]
        assert texts(client, first)[-1] == "Slow question"
        assert len(client.get(f"/api/threads/{other}/runs/{running['id']}").json()["message_ids"]) == 2
        assert len(model.requests) == 4

        # Stopping a run that already ended changes nothing, and no proposal is cancelled or applied by a stop.
        for thread, run in ((first, prepared), (other, running)):
            assert client.post(f"/api/threads/{thread}/runs/{run['id']}/cancel").json()["status"] == "completed"
        [proposal] = client.get("/api/portfolio/proposals").json()
        assert (proposal["status"], proposal["run_id"]) == ("pending", prepared["id"])
        assert committed(client) == (1, 10)


def test_restart_shows_the_run_as_interrupted_and_never_replays_it(tmp_path):
    client, model, store = desk(tmp_path, [ModelTurn(text="Never delivered.")], released=False)
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = accept(client, thread, "Question before the restart")
        until(lambda: model.requests)

    restarted_model = Gated([ModelTurn(text="Fresh answer.")])
    with api(store, restarted_model, FakeFinancialProvider(evidence())) as restarted:
        detail = restarted.get(f"/api/threads/{thread}").json()
        assert [(r["id"], r["status"]) for r in detail["runs"]] == [(run["id"], "interrupted")]
        assert texts(restarted, thread) == ["Question before the restart"]
        # The browser's recovery resend of the same request finds the interrupted run; it does not run again.
        again = accept(restarted, thread, "Question before the restart")
        assert (again["id"], again["status"]) == (run["id"], "interrupted")
        assert restarted_model.requests == []
        assert ask(restarted, thread, "Question before the restart", "req-2")["status"] == "completed"
        assert texts(restarted, thread)[-1] == "Fresh answer."


# Committed revision basis


def test_commit_during_a_run_keeps_its_basis_and_the_next_run_binds_the_new_revision(tmp_path):
    state = {}

    def holdings_after_commit():
        changed = portfolio()
        changed.snapshot.positions[0].shares = Decimal("25")
        state["store"].save(changed)
        return call(HOLDINGS)

    client, model, state["store"] = desk(tmp_path, [
        holdings_after_commit, call(EXPOSURE, "exposure"), ModelTurn(text="You hold 10 shares."),
        call(HOLDINGS), ModelTurn(text="You hold 25 shares."),
    ])
    with client:
        thread = client.post("/api/threads").json()["id"]
        first = ask(client, thread, "What do I hold?")
        assert (first["status"], first["bound_revision"]) == ("completed", 1)
        assert committed(client) == (2, 25)
        held = tool_output(model, 1)
        assert (held["revision"], shares(held["positions"])) == (1, 10)
        [result] = [id for id in first["artifact_ids"] if id != "art-rev-1"]
        stored = client.get(f"/api/artifacts/{result}").json()
        assert (stored["reference"]["revision"], stored["reference"]["basis"]) == (1, "committed")

        second = ask(client, thread, "And now?", "req-2")
        assert (second["status"], second["bound_revision"]) == ("completed", 2)
        held = tool_output(model, 4)
        assert (held["revision"], shares(held["positions"])) == (2, 25)
        # The earlier run and its stored result keep the basis they were computed from.
        runs = client.get(f"/api/threads/{thread}").json()["runs"]
        assert [r["bound_revision"] for r in runs] == [1, 2]
        assert client.get(f"/api/artifacts/{result}").json() == stored


# Hypothetical runs


def test_hypothetical_run_analyzes_the_captured_draft_and_labels_what_it_derives(tmp_path):
    client, model, _ = desk(tmp_path, [call(HOLDINGS), call(EXPOSURE, "exposure"), ModelTurn(text="Hypothetically, 99 shares.")])
    with client:
        selected = capture(client)
        capture(client, shares="500")  # the draft is edited again after the capture
        thread = client.post("/api/threads").json()["id"]
        run = ask(client, thread, "What if I held this?", hypothetical_artifact_id=selected)
        assert (run["status"], run["hypothetical_artifact_id"]) == ("completed", selected)

        held = tool_output(model, 1)
        assert (held["basis"], held["revision"], shares(held["positions"])) == ("hypothetical", None, 99)
        assert held["artifact_ids"] == [selected]
        context = json.loads(model.requests[0]["messages"][1]["content"])
        assert context["hypothetical_portfolio"]["artifact_id"] == selected
        assert "12.34" not in json.dumps(model.requests)

        derived = [client.get(f"/api/artifacts/{id}").json()["reference"] for id in run["artifact_ids"] if id != selected]
        assert derived and all(
            (ref["basis"], ref["revision"], ref["source_artifact_id"], ref["originating_run_id"])
            == ("hypothetical", None, selected, run["id"]) for ref in derived)

        # Reopening the conversation still says which run was hypothetical; nothing was saved or proposed.
        reopened = client.get(f"/api/threads/{thread}").json()
        assert [r["hypothetical_artifact_id"] for r in reopened["runs"]] == [selected]
        assert committed(client) == (1, 10)
        assert client.get("/api/portfolio/proposals").json() == []


def test_hypothetical_run_cannot_change_saved_holdings_without_a_confirmed_proposal(tmp_path):
    client, model, _ = desk(tmp_path, [call(SET_25), ModelTurn(text="A preview is ready for your review.")])
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = ask(client, thread, "Record 25 Acme shares.", hypothetical_artifact_id=capture(client))
        assert run["status"] == "completed"
        [proposal] = client.get("/api/portfolio/proposals").json()
        assert (proposal["status"], proposal["base_revision"]) == ("pending", 1)
        # The preview edits the saved portfolio: none of the draft's values are carried into it.
        after = client.get(f"/api/portfolio/proposals/{proposal['id']}").json()["after_portfolio"]["snapshot"]["positions"]
        assert shares(after) == 25
        assert Decimal(next(row for row in after if row["id"] == "cash")["cash"]) == 800
        assert committed(client) == (1, 10)


def test_only_a_stored_hypothetical_portfolio_can_be_selected(tmp_path):
    client, model, _ = desk(tmp_path, [call(EXPOSURE), ModelTurn(text="Done.")])
    with client:
        thread = client.post("/api/threads").json()["id"]
        result = next(id for id in ask(client, thread, "Check exposure")["artifact_ids"] if id != "art-rev-1")
        for artifact in ("art-missing", "art-rev-1", result):
            response = client.post(f"/api/threads/{thread}/messages", json={
                "text": "What if?", "client_request_id": f"req-{artifact}", "hypothetical_artifact_id": artifact})
            assert response.status_code == 422
        assert len(client.get(f"/api/threads/{thread}").json()["runs"]) == 1


def test_ordinary_run_ignores_an_open_draft(tmp_path):
    client, model, _ = desk(tmp_path, [call(HOLDINGS), ModelTurn(text="You hold 10 shares.")])
    with client:
        capture(client)
        thread = client.post("/api/threads").json()["id"]
        run = ask(client, thread, "What do I hold?")
        assert (run["status"], run["hypothetical_artifact_id"], run["bound_revision"]) == ("completed", None, 1)
        held = tool_output(model, 1)
        assert (held["revision"], shares(held["positions"])) == (1, 10)
        assert "hypothetical_portfolio" not in model.requests[0]["messages"][1]["content"]
        assert held["artifact_ids"] == ["art-rev-1"] and "basis" not in held


# Storage migration


def test_existing_runs_survive_the_upgrades_that_record_hypothetical_selection_and_model(tmp_path):
    path = tmp_path / "portfolio.db"
    db = Database(path)
    db.initialize()
    db.create_thread("th-1")
    run, _ = db.submit_message("th-1", SubmitMessageRequest(client_request_id="req-1", text="Before upgrade"), 1)
    with sqlite3.connect(path) as conn:  # the database as the previous release left it
        conn.execute("ALTER TABLE runs DROP COLUMN hypothetical_artifact_id;")
        conn.execute("ALTER TABLE runs DROP COLUMN ai_provider;")
        conn.execute("ALTER TABLE runs DROP COLUMN ai_model;")
        conn.execute("PRAGMA user_version = 2;")

    upgraded = Database(path)
    upgraded.initialize()
    upgraded.initialize()  # repeated startup
    assert upgraded.get_schema_version() == SCHEMA_VERSION == 4
    kept = upgraded.get_run("th-1", run.id)
    assert kept is not None and (kept.bound_revision, kept.hypothetical_artifact_id, kept.ai_model) == (1, None, None)
    with sqlite3.connect(tmp_path / "portfolio.backup.db") as backup:
        assert backup.execute("PRAGMA user_version;").fetchone()[0] == 2


def test_later_ordinary_run_sees_earlier_hypothetical_messages_labeled(tmp_path):
    client, model, _ = desk(tmp_path, [call(HOLDINGS), ModelTurn(text="Hypothetically, 99 shares."),
                                       call(HOLDINGS, "again"), ModelTurn(text="You hold 10 shares.")])
    with client:
        thread = client.post("/api/threads").json()["id"]
        ask(client, thread, "What if I held this?", hypothetical_artifact_id=capture(client))
        run = ask(client, thread, "What do I hold?", "req-2")
        assert (run["status"], run["hypothetical_artifact_id"]) == ("completed", None)
        history = [json.loads(m["content"]) for m in model.requests[2]["messages"][2:]]
        # Tool steps are not dialogue; the answer carries its run's outcomes, still labeled hypothetical.
        assert [m.get("basis") for m in history] == ["hypothetical", "hypothetical", None]
        assert history[1]["completed_tools"][0]["name"] == "get_holdings"
        held = tool_output(model, 3)
        assert (held["revision"], shares(held["positions"])) == (1, 10)


def test_hypothetical_run_keeps_saved_quantities_out_of_web_search_queries(tmp_path):
    client, model, _ = desk(tmp_path, [call(("web_search", {"query": "Acme 800 outlook"})), ModelTurn(text="No search.")])
    with client:
        thread = client.post("/api/threads").json()["id"]
        run = ask(client, thread, "Any news?", hypothetical_artifact_id=capture(client))
        assert run["status"] == "completed"
        # 800 is the saved cash balance; the draft holds 5. Neither may leave in a query.
        assert tool_output(model, 1)["payload"]["reason"] == "rejected_query"
        assert len(model.requests) == 2
