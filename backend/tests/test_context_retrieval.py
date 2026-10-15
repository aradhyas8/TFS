"""Ticket 04 behavior at the FastAPI request/result and fake transport seams."""

import copy
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.orchestrator import Orchestrator
from analyst.portfolio import PortfolioStore
from analyst.providers import ModelTurn, ToolCall, latest_price_position
from analyst.schemas import SavedDecision, SavedPortfolio, ToolOutcome


def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio.model_validate({
        "snapshot": {"as_of": "2025-01-01", "reporting_currency": "CAD",
                     "accounts": [{"id": "acc-1", "name": "TFSA"}],
                     "positions": [{"id": "pos-1", "account_id": "acc-1", "kind": "stock",
                                    "currency": "CAD", "ticker": "TD", "listing": "XTSE",
                                    "shares": "100"}]},
        "average_costs": {"pos-1": "80.00"},
    })


class CapturingTransport:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = iter(turns)
        self.requests: list[dict[str, Any]] = []

    async def respond(self, messages: Any, **kwargs: Any) -> ModelTurn:
        self.requests.append(copy.deepcopy({"messages": messages, **kwargs}))
        turn = next(self.turns)
        position = latest_price_position(messages) if isinstance(messages, list) else None
        if isinstance(turn, ModelTurn) and isinstance(turn.answer, dict) and "price_position" not in turn.answer and position:
            # A scripted stock answer reads where the price sits from the tool output, as a correct model does.
            turn = dataclasses.replace(turn, answer={**turn.answer, "price_position": position})
        return turn


def client_for(store: PortfolioStore, model: CapturingTransport) -> TestClient:
    settings = Settings(api_key="sk-test", model="test-model", request_timeout=5)
    return TestClient(create_app(
        settings=settings, portfolio_store=store,
        orchestrator=Orchestrator(store.db, model, settings),
    ))


def submit(client: TestClient, thread: str, text: str, request_id: str = "req-1", *,
           workflow_input: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"/api/threads/{thread}/messages", json={
        "text": text, "client_request_id": request_id,
        **({"workflow_input": workflow_input} if workflow_input is not None else {}),
    })
    assert response.status_code == 202
    run = response.json()
    for _ in range(100):
        result = client.get(f"/api/threads/{thread}/runs/{run['id']}").json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("Run did not finish")


def test_followup_gets_compact_bound_context_without_full_holdings(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    model = CapturingTransport([ModelTurn(text="An ETF is a fund."), ModelTurn(text="Yes.")])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        assert submit(client, thread, "What is an ETF?")["status"] == "completed"
        assert submit(client, thread, "Does that diversify?", "req-2")["status"] == "completed"
    request = model.requests[-1]
    context = json.loads(request["messages"][1]["content"])
    assert context["revision"] == 1
    assert context["holdings_date"] == "2025-01-01"
    assert "settings" in context and "benchmark" in context
    assert "An ETF is a fund." in json.dumps(request["messages"])
    assert "positions" not in context and "snapshot" not in context
    assert "80.00" not in json.dumps(request)
    assert {tool["name"] for tool in request["tools"]} == {
        "get_holdings", "get_artifact", "get_proposal", "get_messages", "web_search",
        "review_portfolio", "get_financial_data", "resolve_candidate", "refresh_quotes", "check_proposed_changes",
        "get_sec_filings", "get_sedar_filings", "get_issuer_material", "calculate_company_cases",
        "calculate_comparison", "scan_opportunities", "get_company_research",
        "run_exposure_review", "run_stock_analysis", "run_new_cash_allocation",
        "run_portfolio_reunderwriting", "run_theme_exploration",
        "prepare_portfolio_proposal",
    }


def test_requested_holdings_use_acceptance_revision_after_later_commit(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())

    class CommitDuringRequest(CapturingTransport):
        async def respond(self, messages: Any, **kwargs: Any) -> ModelTurn:
            if not self.requests:
                changed = sample_portfolio().model_dump(mode="json")
                changed["snapshot"]["positions"][0]["shares"] = "999"
                changed["snapshot"]["as_of"] = "2026-01-01"
                store.save(SavedPortfolio.model_validate(changed))
            return await super().respond(messages, **kwargs)

    model = CommitDuringRequest([
        ModelTurn(calls=[ToolCall("read-1", "get_holdings", '{"offset":0,"limit":20}')],
                  continuation=[{"type": "reasoning", "encrypted_content": "current-private"},
                                {"type": "function_call", "call_id": "read-1", "name": "get_holdings",
                                 "arguments": '{"offset":0,"limit":20}'}]),
        ModelTurn(text="You held 100 shares on the bound date."),
        ModelTurn(text="The completed read remains a reference."),
    ])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        result = submit(client, thread, "How many shares?")
        assert result["status"] == "completed"
        assert result["bound_revision"] == 1
        public = client.get(f"/api/threads/{thread}").text
        assert "current-private" not in public
        assert '"tool_outcomes":[{' in public
        assert submit(client, thread, "And now?", "req-2")["bound_revision"] == 2
    items = model.requests[1]["messages"]
    output = json.loads(next(m["output"] for m in items if m.get("type") == "function_call_output"))
    assert output["revision"] == 1
    assert output["holdings_date"] == "2025-01-01"
    assert output["positions"][0]["shares"] == "100"
    assert "80.00" not in json.dumps(items)
    assert any(m.get("encrypted_content") == "current-private" for m in items)
    followup = model.requests[2]["messages"]
    assert "current-private" not in json.dumps(followup)
    assert not any(m.get("type") in {"function_call", "function_call_output"} for m in followup)
    assert "read-1" in json.dumps(followup)


def test_bounded_history_retrieves_older_messages_and_safe_artifacts(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        artifact = client.post("/api/portfolio/artifacts", json={"portfolio": sample_portfolio().model_dump(mode="json")}).json()
        artifact_id = artifact["id"]
        old = store.db.add_message(thread, "assistant", json.dumps({
            "nested": [{"average_cost": "SECRET-COST", "private_continuation": "SECRET-STATE", "fact": "older fact"}],
        }), artifact_ids=[artifact_id])
        cursor = store.db.add_message(thread, "user", "retrieve before this").id
        for i in range(25):
            store.db.add_message(thread, "user", f"message-{i} " + "x" * 2000)
        # The known reference is available without the old payload being injected.
        store.db.add_message(thread, "assistant", "Reference", artifact_ids=[artifact_id])
        model.turns = iter([
            ModelTurn(calls=[ToolCall("history", "get_messages", json.dumps({"before_id": cursor, "limit": 20}))]),
            ModelTurn(calls=[ToolCall("artifact", "get_artifact", json.dumps({"artifact_id": artifact_id}))]),
            ModelTurn(text="Retrieved the older fact."),
        ])
        assert submit(client, thread, "Retrieve that fact")["status"] == "completed"
        assert len(client.get(f"/api/threads/{thread}").json()["messages"]) == 32
    initial = model.requests[0]["messages"][2:]
    assert len(initial) <= 20
    assert sum(len(m["content"]) for m in initial) <= 32000
    assert "message-24" in json.dumps(initial) and "message-0 " not in json.dumps(initial)
    assert "older fact" not in json.dumps(initial)
    last = json.dumps(model.requests[-1])
    assert "older fact" in last and old.id in last
    assert "SECRET-COST" not in last and "SECRET-STATE" not in last
    assert "80.00" not in last
    calls = [m["call_id"] for m in model.requests[-1]["messages"] if m.get("type") == "function_call"]
    outputs = [m["call_id"] for m in model.requests[-1]["messages"] if m.get("type") == "function_call_output"]
    assert calls == outputs == ["history", "artifact"]


def test_proposal_details_and_historical_results_recursively_hide_costs(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    saved = sample_portfolio().model_dump(mode="json")
    saved["unresolved"] = [{"account_id": "acc-1", "ticker": "ABC", "shares": "1", "average_cost": "934.12"}]
    store.save(SavedPortfolio.model_validate(saved))
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        saved["average_costs"]["pos-1"] = "846.23"
        saved["unresolved"][0]["average_cost"] = "745.34"
        proposal = client.put("/api/portfolio", json={"base_revision": 1, "portfolio": saved}).json()["proposal"]
        # Typed historical fixture deliberately includes nested old portfolio/proposal shapes.
        ref = store.db.get_artifact_reference("art-rev-1").model_copy(update={"id": "historical", "kind": "analysis_result"})
        store.db.store_artifact(ref, {"nested": [{"portfolio": saved, "proposal": proposal}],
                                     "private_continuation": "NEVER-REPLAY"})
        store.db.add_message(thread, "assistant", "Old analysis", artifact_ids=["historical"])
        model.turns = iter([
            ModelTurn(calls=[ToolCall("proposal", "get_proposal", json.dumps({"proposal_id": proposal["id"]}))]),
            ModelTurn(calls=[ToolCall("result", "get_artifact", '{"artifact_id":"historical"}')]),
            ModelTurn(text="The proposal remains pending."),
        ])
        assert submit(client, thread, "Explain the pending changes")["status"] == "completed"
        human = client.get(f"/api/portfolio/proposals/{proposal['id']}").text
        assert "846.23" in human and "745.34" in human
        assert client.get("/api/portfolio").json()["revision"] == 1
    initial = json.loads(model.requests[0]["messages"][1]["content"])
    assert initial["pending_proposals"] == [{"id": proposal["id"], "base_revision": 1,
                                              "status": "pending", "label": "Pending portfolio proposal"}]
    serialized = json.dumps(model.requests)
    for private in ["934.12", "846.23", "745.34", "80.00", "NEVER-REPLAY"]:
        assert private not in serialized
    outputs = [json.loads(m["output"]) for m in model.requests[-1]["messages"] if m.get("type") == "function_call_output"]
    assert outputs[0]["id"] == proposal["id"]
    assert outputs[0]["status"] == "pending"
    assert outputs[1]["reference"]["id"] == "historical"


@pytest.mark.parametrize("turn", [
    ModelTurn(calls=[ToolCall("unknown", "confirm_portfolio", "{}")]),
    ModelTurn(calls=[ToolCall("bad-json", "get_holdings", "{secret-sensitive-arguments")]),
    ModelTurn(calls=[ToolCall("extra", "get_holdings", '{"offset":0,"limit":20,"revision":2}')]),
    ModelTurn(calls=[ToolCall("bad-type", "get_holdings", '{"offset":"0","limit":20}')]),
    ModelTurn(calls=[ToolCall("bad-limit", "get_messages", '{"before_id":null,"limit":21}')]),
    ModelTurn(calls=[ToolCall("bad-offset", "get_holdings", '{"offset":-1,"limit":20}')]),
    ModelTurn(calls=[ToolCall("", "get_holdings", '{"offset":0,"limit":20}')]),
    ModelTurn(calls=[ToolCall("bad id", "get_holdings", '{"offset":0,"limit":20}')]),
    ModelTurn(calls=[ToolCall("a" * 121, "get_holdings", '{"offset":0,"limit":20}')]),
    ModelTurn(text="Already answered", calls=[ToolCall("mixed", "get_holdings", '{"offset":0,"limit":20}')]),
    ModelTurn(answer={"answer": "final"}, calls=[ToolCall("mixed-json", "get_holdings", '{"offset":0,"limit":20}')]),
    ModelTurn(calls=[ToolCall("one", "get_holdings", '{"offset":0,"limit":20}'),
                     ToolCall("two", "get_holdings", '{"offset":0,"limit":20}')]),
    ModelTurn(calls=[ToolCall("hidden", "get_holdings", '{"offset":0,"limit":20}')],
              continuation=[{"type": "message", "content": [{"type": "output_text", "text": "hidden final"}]}]),
])
def test_invalid_application_calls_fail_without_execution_or_sensitive_errors(tmp_path: Path, turn: ModelTurn) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([turn])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        result = submit(client, thread, "Invalid call")
        assert result["status"] == "failed"
        assert "secret-sensitive-arguments" not in json.dumps(result)
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert not any(m["tool_outcomes"] for m in messages)
    assert len(model.requests) == 1


def test_repeated_call_id_is_rejected_before_second_read(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("same", "get_holdings", '{"offset":0,"limit":20}')]),
        ModelTurn(calls=[ToolCall("same", "get_holdings", '{"offset":0,"limit":20}')]),
    ])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        assert submit(client, thread, "Read twice")["status"] == "failed"
        messages = client.get(f"/api/threads/{thread}").json()["messages"]
        assert sum(len(m["tool_outcomes"]) for m in messages) == 1


def test_oversized_reads_are_bounded_and_completed_exchanges_become_references(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        base = store.db.get_artifact_reference("art-rev-1")
        for name, length in [("large", 40000), ("fits", 18000)]:
            store.db.store_artifact(base.model_copy(update={"id": name, "kind": "analysis_result"}), {"fact": "x" * length})
        store.db.add_message(thread, "assistant", "Results", artifact_ids=["large", "fits"])
        store.db.add_message(thread, "assistant", "y" * 40000)
        model.turns = iter([
            ModelTurn(calls=[ToolCall("too-large", "get_artifact", '{"artifact_id":"large"}')]),
            ModelTurn(calls=[ToolCall("first", "get_artifact", '{"artifact_id":"fits"}')]),
            ModelTurn(calls=[ToolCall("second", "get_artifact", '{"artifact_id":"fits"}')]),
            ModelTurn(text="Finished safely."),
        ])
        assert submit(client, thread, "Read those results")["status"] == "completed"
        public = client.get(f"/api/threads/{thread}").json()
        assert any(o["status"] == "error" for m in public["messages"] for o in m["tool_outcomes"])
    first_output = json.loads(model.requests[1]["messages"][-1]["output"])
    assert "error" in first_output and first_output["artifact_id"] == "large"
    for request in model.requests:
        items = request["messages"][2:]
        assert sum(len(m["content"]) if "content" in m else len(json.dumps(m)) for m in items) <= 32000
        calls = [m["call_id"] for m in items if m.get("type") == "function_call"]
        outputs = [m["call_id"] for m in items if m.get("type") == "function_call_output"]
        assert calls == outputs
    final = model.requests[-1]["messages"]
    assert "first" in json.dumps(final)
    final_calls = [m["call_id"] for m in final if m.get("type") == "function_call"]
    assert "first" not in final_calls and "second" in final_calls


def test_reads_cannot_cross_threads_or_use_unknown_or_newer_artifacts(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        other = client.post("/api/threads").json()["id"]
        foreign = store.db.add_message(other, "user", "FOREIGN-SECRET")
        private = store.db.get_artifact_reference("art-rev-1").model_copy(update={"id": "unknown-artifact"})
        store.db.store_artifact(private, {"private_fact": "UNKNOWN-ARTIFACT-SECRET"})
        model.turns = iter([
            ModelTurn(calls=[ToolCall("foreign", "get_messages", json.dumps({"before_id": foreign.id, "limit": 20}))]),
            ModelTurn(calls=[ToolCall("unknown-art", "get_artifact", '{"artifact_id":"unknown-artifact"}')]),
            ModelTurn(calls=[ToolCall("unknown-proposal", "get_proposal", '{"proposal_id":"missing"}')]),
            ModelTurn(text="Those references are unavailable."),
        ])
        assert submit(client, thread, "Read invalid references")["status"] == "completed"
    outputs = [json.loads(m["output"]) for m in model.requests[-1]["messages"] if m.get("type") == "function_call_output"]
    assert len(outputs) == 3 and all("error" in output for output in outputs)
    assert "FOREIGN-SECRET" not in json.dumps(model.requests)
    assert "UNKNOWN-ARTIFACT-SECRET" not in json.dumps(model.requests)


def test_twenty_recent_messages_and_current_thread_cursor_are_preserved(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        for i in range(25):
            store.db.add_message(thread, "user", f"short-{i:02d}")
        model.turns = iter([ModelTurn(text="Recent history is available.")])
        assert submit(client, thread, "Latest question")["status"] == "completed"
    recent = model.requests[0]["messages"][2:]
    assert len(recent) == 20
    assert "short-06" in recent[0]["content"]
    assert "short-05" not in json.dumps(recent)
    assert "Latest question" in recent[-1]["content"]


def test_large_result_cannot_evict_current_question(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("large", "get_artifact", '{"artifact_id":"large"}')],
                  continuation=[{"type": "reasoning", "encrypted_content": "r" * 400}]),
        ModelTurn(text="Answered the current question."),
    ])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        ref = store.db.get_artifact_reference("art-rev-1").model_copy(update={"id": "large", "kind": "analysis_result"})
        store.db.store_artifact(ref, {"fact": "x" * 30500})
        store.db.add_message(thread, "assistant", "Result", artifact_ids=["large"])
        question = "current-question " + "q" * 980
        assert submit(client, thread, question)["status"] == "completed"
    assert question in json.dumps(model.requests[-1]["messages"])
    assert any(m.get("encrypted_content") == "r" * 400 for m in model.requests[-1]["messages"])


@pytest.mark.parametrize("transition", ["cancel", "confirm"])
def test_known_historical_proposal_remains_retrievable(tmp_path: Path, transition: str) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        edited = sample_portfolio().model_dump(mode="json")
        edited["average_costs"]["pos-1"] = "856.78"
        proposal = client.put("/api/portfolio", json={"base_revision": 1, "portfolio": edited}).json()["proposal"]
        assert client.post(f"/api/portfolio/proposals/{proposal['id']}/{transition}").status_code == 200
        store.db.add_message(thread, "assistant", "Earlier proposal", proposal_ids=[proposal["id"]])
        cursor = store.db.add_message(thread, "user", "Cursor").id
        for _ in range(20):
            store.db.add_message(thread, "user", "Recent conversation")
        model.turns = iter([
            ModelTurn(calls=[ToolCall("older", "get_messages", json.dumps({"before_id": cursor, "limit": 20}))]),
            ModelTurn(calls=[ToolCall("proposal", "get_proposal", json.dumps({"proposal_id": proposal["id"]}))]),
            ModelTurn(text="Historical proposal explained."),
        ])
        assert submit(client, thread, "Read the earlier proposal")["status"] == "completed"
    detail = json.loads(model.requests[-1]["messages"][-1]["output"])
    assert detail["id"] == proposal["id"]
    assert detail["status"] == ("cancelled" if transition == "cancel" else "applied")
    assert detail["base_revision"] == 1
    assert "856.78" not in json.dumps(model.requests)


def test_oversized_historical_message_reference_still_excludes_private_fields(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([ModelTurn(text="Reference preserved.")])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        store.db.add_message(thread, "assistant", "x" * 40000, tool_outcomes=[ToolOutcome(
            id="old-tool", name="get_holdings", status="error",
            error=json.dumps({"average_cost": "SECRET-COST", "private_continuation": "SECRET-STATE"}),
        )])
        assert submit(client, thread, "Explain the previous read")["status"] == "completed"
    request = json.dumps(model.requests[0])
    assert "old-tool" in request
    assert "SECRET-COST" not in request and "SECRET-STATE" not in request


def test_retrieved_investment_reasoning_survives_private_state_redaction(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    store.save(sample_portfolio())
    rationale = {"reasoning": {"thesis": "Diversification reduces issuer concentration.",
                               "nested": [{"reasoning": "Keep enough cash for withdrawals.",
                                           "average_cost": "PRIVATE-COST"}]}}
    decision = SavedDecision.model_validate({
        "id": "saved-decision", "saved_at": "2025-01-01T00:00:00Z", "as_of": "2025-01-01",
        "question": "Why diversify?",
        "reasoning": {"reason": "Diversification reduces issuer concentration.",
                      "downside": "Fund values can still decline."},
        "conclusion": {"preferred_action": "no_action"},
    }).model_dump(mode="json")
    model = CapturingTransport([
        ModelTurn(calls=[ToolCall("decision", "get_artifact", '{"artifact_id":"decision"}')]),
        ModelTurn(text="The saved rationale remains available."),
    ])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        ref = store.db.get_artifact_reference("art-rev-1").model_copy(update={"id": "decision", "kind": "analysis_result"})
        store.db.store_artifact(ref, {
            "saved_decision": decision, "explanation": rationale,
            "private_continuation": [{"type": "reasoning", "summary": "PRIVATE-CONTINUATION"}],
            "nested": [{"type": "reasoning", "summary": [{"text": "PRIVATE-SUMMARY"}],
                        "encrypted_content": "PRIVATE-TOKEN"}],
            "average_costs": {"pos-1": "PRIVATE-COST"},
        })
        store.db.add_message(thread, "assistant", "Saved decision", artifact_ids=["decision"])
        assert submit(client, thread, "Why was that decision saved?")["status"] == "completed"
        projected = client.get("/api/artifacts/decision?projection=model").json()["payload"]
        human = client.get("/api/artifacts/decision").json()["payload"]
    retrieved = json.loads(model.requests[-1]["messages"][-1]["output"])["payload"]
    for payload in [projected, retrieved]:
        assert payload["saved_decision"] == decision
        assert payload["explanation"]["reasoning"]["thesis"] == "Diversification reduces issuer concentration."
        assert payload["explanation"]["reasoning"]["nested"][0]["reasoning"] == "Keep enough cash for withdrawals."
        assert "PRIVATE-" not in json.dumps(payload)
    assert human["saved_decision"] == decision and human["explanation"] == rationale
    assert "PRIVATE-TOKEN" in json.dumps(human)


def test_historical_user_json_keeps_reasoning_and_removes_private_state(tmp_path: Path) -> None:
    store = PortfolioStore(database_path=tmp_path / "portfolio.db")
    model = CapturingTransport([])
    with client_for(store, model) as client:
        thread = client.post("/api/threads").json()["id"]
        earlier = {"reasoning": "Hold cash for next year's planned withdrawal.",
                   "nested": [{"reasoning": {"reason": "Avoid forced sales.", "average_cost": "PRIVATE-COST"}}],
                   "continuation": [{"type": "reasoning", "summary": "PRIVATE-STATE"}],
                   "model_item": {"type": "reasoning", "summary": [{"text": "PRIVATE-SUMMARY"}]}}
        old = store.db.add_message(thread, "user", json.dumps(earlier))
        cursor = store.db.add_message(thread, "user", "Retrieve before this").id
        model.turns = iter([
            ModelTurn(calls=[ToolCall("older", "get_messages", json.dumps({"before_id": cursor, "limit": 20}))]),
            ModelTurn(text="Your withdrawal rationale remains available."),
        ])
        assert submit(client, thread, "Explain my earlier rationale")["status"] == "completed"
        durable = client.get(f"/api/threads/{thread}").json()["messages"]
        assert next(m["text"] for m in durable if m["id"] == old.id) == json.dumps(earlier)
    automatic = json.loads(model.requests[0]["messages"][2]["content"])
    retrieved = json.loads(model.requests[-1]["messages"][-1]["output"])["messages"][0]
    for message in [automatic, json.loads(retrieved["content"])]:
        projected = json.loads(message["text"])
        assert projected["reasoning"] == "Hold cash for next year's planned withdrawal."
        assert projected["nested"][0]["reasoning"]["reason"] == "Avoid forced sales."
        assert "PRIVATE-" not in json.dumps(projected)
