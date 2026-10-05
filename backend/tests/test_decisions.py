from datetime import date, datetime, timezone
from pathlib import Path

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
    response = client.post(
        "/api/analyze",
        json={"question": "What is my direct concentration?", "portfolio": sn},
    )
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
    analysis_resp = client.post(
        "/api/analyze",
        json={"question": "Review my holdings dated September 2026", "portfolio": snapshot()},
    )
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

    analysis_resp = client.post(
        "/api/analyze",
        json={"question": "Analyze concentration", "portfolio": snapshot()},
    )
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
    response = client.post(
        "/api/analyze",
        json={"question": "Review portfolio standalone", "portfolio": snapshot()},
    )
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

