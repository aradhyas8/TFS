from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.config import Settings
from analyst.decisions import DecisionStore
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import (
    AnalysisResult,
    DecisionAlternative,
    DecisionConclusion,
    DecisionReasoning,
    EvidenceReference,
    SavedDecision,
)
from tests.analysis_harness import analyze_with
from tests.test_analysis import recommendation, snapshot


def make_sample_result() -> AnalysisResult:
    sn = snapshot()
    client = TestClient(
        create_app(
            model=ScriptedModel([
                ModelTurn(calls=[ToolCall("test", "review_portfolio", "{}")]),
                ModelTurn(answer=recommendation()),
            ]),
            data=FakeDataProvider(),
            settings=Settings("sk-test-super-secret-key", "test-model"),
        )
    )
    response = analyze_with(client, {"question": "What is my direct concentration?", "portfolio": sn})
    assert response.status_code == 200
    return AnalysisResult.model_validate(response.json())


def test_plain_file_storage_roundtrip(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    decision = SavedDecision(
        id="dec-test-01",
        saved_at=now,
        as_of=date(2026, 9, 30),
        question="What is my direct concentration?",
        evidence_references=[
            EvidenceReference(
                id="doc-1",
                title="Annual Report 2025",
                source="SEC EDGAR",
                as_of=date(2026, 3, 15),
                url="https://www.sec.gov/edgar/doc1",
                excerpt="Revenues grew by 15%",
            )
        ],
        reasoning=DecisionReasoning(
            reason="Direct exposure is concentrated in Acme.",
            assumptions=["Supplied marks represent the dated snapshot."],
            uncertainty=["Personal guardrails remain unset."],
            what_could_change=["Updated marks or baseline targets."],
            downside="Issuer-specific drawdowns.",
            alternatives=[
                DecisionAlternative(action="clarify_inputs", reason="Supply a baseline.")
            ],
        ),
        conclusion=DecisionConclusion(preferred_action="review_only"),
        confirmed_action=None,
    )

    saved = store.save(decision)
    assert saved.id == "dec-test-01"

    # Verify a plain JSON file exists on disk
    file_path = tmp_path / "dec-test-01.json"
    assert file_path.exists()
    assert file_path.is_file()

    # Reopen and compare
    reopened = store.get("dec-test-01")
    assert reopened is not None
    assert reopened.id == "dec-test-01"
    assert reopened.question == "What is my direct concentration?"
    assert reopened.as_of == date(2026, 9, 30)
    assert len(reopened.evidence_references) == 1
    assert reopened.evidence_references[0].id == "doc-1"
    assert reopened.evidence_references[0].title == "Annual Report 2025"
    assert reopened.evidence_references[0].source == "SEC EDGAR"
    assert reopened.evidence_references[0].as_of == date(2026, 3, 15)
    assert reopened.reasoning.reason == "Direct exposure is concentrated in Acme."
    assert reopened.conclusion.preferred_action == "review_only"
    assert reopened.confirmed_action is None


def test_user_confirmed_action_preservation(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    decision = SavedDecision(
        id="dec-test-02",
        saved_at=now,
        as_of=date(2026, 9, 30),
        question="What is my exposure?",
        reasoning=DecisionReasoning(
            reason="Hold current position.",
            downside="Drawdowns possible.",
        ),
        conclusion=DecisionConclusion(preferred_action="hold"),
    )
    store.save(decision)

    # Absent action stays unconfirmed
    initial = store.get("dec-test-02")
    assert initial is not None
    assert initial.confirmed_action is None

    # Explicit user confirmation is preserved without inferring execution details
    confirm_time = datetime(2026, 10, 5, 14, 30, 0, tzinfo=timezone.utc)
    confirmed = store.confirm_action(
        "dec-test-02",
        action="hold",
        notes="Decided to hold after review; no trade submitted.",
        confirmed_at=confirm_time,
    )
    assert confirmed.confirmed_action is not None
    assert confirmed.confirmed_action.action == "hold"
    assert confirmed.confirmed_action.confirmed_at == confirm_time
    assert confirmed.confirmed_action.notes == "Decided to hold after review; no trade submitted."

    # Reload from disk and check persistence
    reloaded = store.get("dec-test-02")
    assert reloaded is not None
    assert reloaded.confirmed_action is not None
    assert reloaded.confirmed_action.action == "hold"
    assert reloaded.confirmed_action.notes == "Decided to hold after review; no trade submitted."


def test_api_save_and_reopen_decision_roundtrip(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    app = create_app(
        model=ScriptedModel([
            ModelTurn(calls=[ToolCall("test", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]),
        data=FakeDataProvider(),
        settings=Settings("sk-secret-test-key", "test-model"),
        store=store,
    )
    client = TestClient(app)

    # 1. Exercise analysis pipeline
    analysis_resp = analyze_with(client, {"question": "Review my holdings dated September 2026", "portfolio": snapshot()})
    assert analysis_resp.status_code == 200
    result_data = analysis_resp.json()

    # 2. Save decision via API
    save_resp = client.post(
        "/api/decisions",
        json={"result": result_data},
    )
    assert save_resp.status_code == 200
    saved = save_resp.json()
    decision_id = saved["id"]
    assert decision_id.startswith("dec-")
    assert saved["question"] == "Review my holdings dated September 2026"
    assert saved["as_of"] == "2026-09-30"
    assert saved["conclusion"]["preferred_action"] == "review_only"
    assert saved["confirmed_action"] is None
    assert len(saved["evidence_references"]) > 0

    # 3. List decisions
    list_resp = client.get("/api/decisions")
    assert list_resp.status_code == 200
    decisions_list = list_resp.json()
    assert any(d["id"] == decision_id for d in decisions_list)

    # 4. Reopen decision
    reopen_resp = client.get(f"/api/decisions/{decision_id}")
    assert reopen_resp.status_code == 200
    reopened = reopen_resp.json()
    assert reopened["id"] == decision_id
    assert reopened["question"] == "Review my holdings dated September 2026"
    assert reopened["as_of"] == "2026-09-30"
    assert reopened["reasoning"]["reason"] == result_data["recommendation"]["reason"]
    assert reopened["reasoning"]["assumptions"] == result_data["recommendation"]["assumptions"]
    assert reopened["reasoning"]["uncertainty"] == result_data["recommendation"]["uncertainty"]
    assert reopened["conclusion"]["preferred_action"] == result_data["recommendation"]["preferred_action"]
    assert reopened["confirmed_action"] is None

    # 5. Confirm user action via API
    confirm_resp = client.post(
        f"/api/decisions/{decision_id}/confirm",
        json={"action": "no_action", "notes": "I reviewed and decided on no action."},
    )
    assert confirm_resp.status_code == 200
    confirmed = confirm_resp.json()
    assert confirmed["confirmed_action"] is not None
    assert confirmed["confirmed_action"]["action"] == "no_action"
    assert confirmed["confirmed_action"]["notes"] == "I reviewed and decided on no action."

    # 6. Verify plain local file on disk has no secrets
    stored_file = tmp_path / f"{decision_id}.json"
    assert stored_file.exists()
    content = stored_file.read_text(encoding="utf-8")
    assert "sk-secret-test-key" not in content


def test_api_keys_and_secrets_never_persisted(tmp_path: Path) -> None:
    secret = "sk-super-confidential-openai-secret-token-xyz"
    store = DecisionStore(tmp_path)
    app = create_app(
        model=ScriptedModel([
            ModelTurn(calls=[ToolCall("test", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]),
        data=FakeDataProvider(),
        settings=Settings(secret, "test-model"),
        store=store,
    )
    client = TestClient(app)

    analysis_resp = analyze_with(client, {"question": "Analyze concentration", "portfolio": snapshot()})
    assert secret not in analysis_resp.text

    save_resp = client.post(
        "/api/decisions",
        json={"result": analysis_resp.json()},
    )
    assert secret not in save_resp.text
    saved_id = save_resp.json()["id"]

    file_text = (tmp_path / f"{saved_id}.json").read_text(encoding="utf-8")
    assert secret not in file_text


def test_analysis_journey_works_without_saving() -> None:
    # Verify the existing analysis journey works without saving
    client = TestClient(
        create_app(
            model=ScriptedModel([
                ModelTurn(calls=[ToolCall("test", "review_portfolio", "{}")]),
                ModelTurn(answer=recommendation()),
            ]),
            data=FakeDataProvider(),
            settings=Settings("sk-test-key", "test-model"),
        )
    )
    response = analyze_with(client, {"question": "Review portfolio standalone", "portfolio": snapshot()})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_decision_not_found_and_validation(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    app = create_app(store=store)
    client = TestClient(app)

    # 404 for unknown decision
    get_resp = client.get("/api/decisions/dec-doesnotexist")
    assert get_resp.status_code == 404
    assert get_resp.json()["detail"] == "Decision not found."

    # 404 for confirming unknown decision
    confirm_resp = client.post(
        "/api/decisions/dec-doesnotexist/confirm",
        json={"action": "add", "notes": "notes"},
    )
    assert confirm_resp.status_code == 404
    assert confirm_resp.json()["detail"] == "Decision not found."

    # 422 for empty save payload
    save_resp = client.post("/api/decisions", json={})
    assert save_resp.status_code == 422


def test_canadian_stock_evidence_preserved_in_saved_decision(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    decision = SavedDecision(
        id="dec-canadian-01",
        saved_at=now,
        as_of=date(2026, 9, 30),
        question="Analyze Acme on TSX",
        evidence_references=[
            EvidenceReference(
                id="sedar_filing",
                title="Acme Corp Annual Information Form 2025",
                source="sedar_plus",
                as_of=date(2026, 3, 20),
                url="https://www.sedarplus.ca/csa-party/records/document.html?id=123",
                excerpt="SEDAR+ Canadian issuer verified disclosure",
            )
        ],
        reasoning=DecisionReasoning(
            reason="Valuation indicates modest upside in base case.",
            downside="Canadian market regulatory risk.",
        ),
        conclusion=DecisionConclusion(preferred_action="hold"),
    )
    store.save(decision)

    reopened = store.get("dec-canadian-01")
    assert reopened is not None
    assert reopened.evidence_references[0].source == "sedar_plus"
    assert reopened.evidence_references[0].url == "https://www.sedarplus.ca/csa-party/records/document.html?id=123"
    assert reopened.as_of == date(2026, 9, 30)



def test_decisions_can_be_deleted_one_at_a_time_or_all(tmp_path):
    store = DecisionStore(tmp_path)
    api = TestClient(create_app(data=FakeDataProvider(), store=store))
    body = {"result": make_sample_result().model_dump(mode="json")}
    first, second = (SavedDecision.model_validate(api.post("/api/decisions", json=body).json()) for _ in range(2))
    assert api.delete(f"/api/decisions/{first.id}").status_code == 204
    assert api.delete(f"/api/decisions/{first.id}").status_code == 404
    assert [row["id"] for row in api.get("/api/decisions").json()] == [second.id]
    assert api.delete("/api/decisions").json() == {"deleted": 1}
    assert api.get("/api/decisions").json() == []


def test_decisions_workflow_and_subject_roundtrip(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    api = TestClient(create_app(data=FakeDataProvider(), store=store))
    res = make_sample_result().model_dump(mode="json")

    # 1. Stock decision
    stock_payload = {
        "result": res,
        "workflow": "stock",
        "subject": {
            "label": "NVDA",
            "ticker": "NVDA",
            "listing": "NASDAQ",
        },
    }
    stock_saved = api.post("/api/decisions", json=stock_payload).json()
    assert stock_saved["workflow"] == "stock"
    assert stock_saved["subject"]["label"] == "NVDA"
    assert stock_saved["subject"]["ticker"] == "NVDA"
    assert stock_saved["subject"]["listing"] == "NASDAQ"

    # 2. New cash decision
    new_cash_payload = {
        "result": res,
        "workflow": "new_cash",
        "subject": {
            "label": "C$2,000 → TFSA",
            "new_cash": {
                "amount": "2000",
                "account_id": "tfsa",
                "currency": "CAD",
            },
        },
    }
    cash_saved = api.post("/api/decisions", json=new_cash_payload).json()
    assert cash_saved["workflow"] == "new_cash"
    assert cash_saved["subject"]["label"] == "C$2,000 → TFSA"
    assert str(cash_saved["subject"]["new_cash"]["amount"]).startswith("2000")
    assert cash_saved["subject"]["new_cash"]["account_id"] == "tfsa"

    # 3. Theme decision
    theme_payload = {
        "result": res,
        "workflow": "theme",
        "subject": {
            "label": "AI infrastructure",
            "theme": {
                "name": "AI infrastructure",
                "mechanism": "Semiconductor manufacturing",
                "shortlist": ["p1", "p2"],
            },
        },
    }
    theme_saved = api.post("/api/decisions", json=theme_payload).json()
    assert theme_saved["workflow"] == "theme"
    assert theme_saved["subject"]["label"] == "AI infrastructure"
    assert theme_saved["subject"]["theme"]["name"] == "AI infrastructure"
    assert theme_saved["subject"]["theme"]["mechanism"] == "Semiconductor manufacturing"
    assert theme_saved["subject"]["theme"]["shortlist"] == ["p1", "p2"]

    # Verify reload from disk via store.get
    loaded_stock = store.get(stock_saved["id"])
    assert loaded_stock is not None
    assert loaded_stock.workflow == "stock"
    assert loaded_stock.subject is not None
    assert loaded_stock.subject.label == "NVDA"
    assert loaded_stock.subject.ticker == "NVDA"

    loaded_cash = store.get(cash_saved["id"])
    assert loaded_cash is not None
    assert loaded_cash.workflow == "new_cash"
    assert loaded_cash.subject is not None
    assert loaded_cash.subject.new_cash is not None
    assert loaded_cash.subject.new_cash.account_id == "tfsa"

    loaded_theme = store.get(theme_saved["id"])
    assert loaded_theme is not None
    assert loaded_theme.workflow == "theme"
    assert loaded_theme.subject is not None
    assert loaded_theme.subject.theme is not None
    assert loaded_theme.subject.theme.shortlist == ["p1", "p2"]


def test_decisions_backwards_compatibility_legacy_file(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    # Write a legacy JSON file with NO workflow or subject fields
    legacy_json = """{
      "id": "dec-legacy-123",
      "saved_at": "2026-09-15T12:00:00Z",
      "as_of": "2026-09-15",
      "question": "What is my direct exposure?",
      "evidence_references": [],
      "reasoning": {
        "reason": "Direct concentration is moderate.",
        "assumptions": [],
        "uncertainty": [],
        "what_could_change": [],
        "downside": "Drawdowns possible.",
        "alternatives": []
      },
      "conclusion": {
        "preferred_action": "review_only",
        "amount": null
      },
      "confirmed_action": null
    }"""
    (tmp_path / "dec-legacy-123.json").write_text(legacy_json, encoding="utf-8")

    # store.get loads cleanly
    loaded = store.get("dec-legacy-123")
    assert loaded is not None
    assert loaded.id == "dec-legacy-123"
    assert loaded.workflow is None
    assert loaded.subject is None

    # store.list_decisions loads cleanly
    all_decisions = store.list_decisions()
    assert len(all_decisions) == 1
    assert all_decisions[0].id == "dec-legacy-123"
    assert all_decisions[0].workflow is None
    assert all_decisions[0].subject is None

    # API returns it cleanly
    api = TestClient(create_app(data=FakeDataProvider(), store=store))
    res = api.get("/api/decisions/dec-legacy-123")
    assert res.status_code == 200
    data = res.json()
    assert data["workflow"] is None
    assert data["subject"] is None


def test_saving_decision_with_artifact_id_is_idempotent_and_prevents_duplicates(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    api = TestClient(create_app(data=FakeDataProvider(), store=store))
    result = make_sample_result()

    # First save with artifact_id
    payload1 = {
        "result": result.model_dump(mode="json"),
        "workflow": "review",
        "artifact_id": "art-workflow-test-123",
    }
    res1 = api.post("/api/decisions", json=payload1)
    assert res1.status_code == 200
    dec1 = res1.json()
    assert dec1["artifact_id"] == "art-workflow-test-123"

    # Second save with same artifact_id returns the existing decision without duplicating
    res2 = api.post("/api/decisions", json=payload1)
    assert res2.status_code == 200
    dec2 = res2.json()
    assert dec2["id"] == dec1["id"]
    assert dec2["artifact_id"] == "art-workflow-test-123"

    # Verify only 1 decision exists in the store
    assert len(store.list_decisions()) == 1


def test_concurrent_saving_with_same_artifact_id_is_atomic_and_never_duplicates(tmp_path: Path) -> None:
    import concurrent.futures

    store = DecisionStore(tmp_path)
    api = TestClient(create_app(data=FakeDataProvider(), store=store))
    result = make_sample_result()

    payload = {
        "result": result.model_dump(mode="json"),
        "workflow": "review",
        "artifact_id": "art-concurrent-race-1",
    }

    def save_once() -> dict[str, Any]:
        resp = api.post("/api/decisions", json=payload)
        assert resp.status_code == 200
        return resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(save_once) for _ in range(10)]
        results = [f.result() for f in futures]

    decision_ids = {r["id"] for r in results}
    assert len(decision_ids) == 1, f"Expected 1 unique decision ID, got {decision_ids}"

    all_stored = store.list_decisions()
    assert len(all_stored) == 1, f"Expected 1 stored decision, got {len(all_stored)}"
    assert all_stored[0].id == list(decision_ids)[0]
    assert all_stored[0].artifact_id == "art-concurrent-race-1"


def test_repeated_concurrent_and_restart_after_save_requests_resolve_to_one_decision(
    tmp_path: Path,
) -> None:
    import concurrent.futures

    # --- Phase 1: Repeated sequential saves ---
    store1 = DecisionStore(tmp_path)
    client1 = TestClient(create_app(data=FakeDataProvider(), store=store1))
    result = make_sample_result()

    payload = {
        "result": result.model_dump(mode="json"),
        "workflow": "review",
        "artifact_id": "art-repeat-restart-test-1",
    }

    res_seq1 = client1.post("/api/decisions", json=payload)
    assert res_seq1.status_code == 200
    dec_seq1 = res_seq1.json()

    res_seq2 = client1.post("/api/decisions", json=payload)
    assert res_seq2.status_code == 200
    dec_seq2 = res_seq2.json()

    assert dec_seq1["id"] == dec_seq2["id"]
    assert len(store1.list_decisions()) == 1

    # --- Phase 2: Concurrent saves on top of existing decision ---
    def save_call() -> dict[str, Any]:
        resp = client1.post("/api/decisions", json=payload)
        assert resp.status_code == 200
        return resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(save_call) for _ in range(8)]
        results = [f.result() for f in futures]

    assert all(r["id"] == dec_seq1["id"] for r in results)
    assert len(store1.list_decisions()) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1

    # --- Phase 3: Restart-after-save ---
    # Create completely fresh store and client pointing at the same directory
    store2 = DecisionStore(tmp_path)
    client2 = TestClient(create_app(data=FakeDataProvider(), store=store2))

    # A save after restart returns the same logical decision without creating new files
    res_after_restart = client2.post("/api/decisions", json=payload)
    assert res_after_restart.status_code == 200
    dec_after_restart = res_after_restart.json()

    assert dec_after_restart["id"] == dec_seq1["id"]
    assert dec_after_restart["artifact_id"] == "art-repeat-restart-test-1"

    # Listing decisions shows exactly one decision
    all_decisions = store2.list_decisions()
    assert len(all_decisions) == 1
    assert all_decisions[0].id == dec_seq1["id"]

    # Exactly one json file on disk
    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    assert json_files[0].name == f"{dec_seq1['id']}.json"


