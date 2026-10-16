"""Tests for immutable hypothetical draft analysis, artifact storage, and validation boundaries."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import FakeFinancialProvider
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import (
    Account,
    BenchmarkSetting,
    FinancialEvidence,
    PortfolioSettings,
    Position,
    SavedPortfolio,
    Snapshot,
)
from tests.analysis_harness import analyze_with


def recommendation():
    return {
        "preferred_action": "review_only",
        "amount": None,
        "reason": "Review direct issuer concentration across both accounts.",
        "alternatives": [
            {"action": "clarify_inputs", "reason": "Supply a baseline before considering changes."}
        ],
        "downside": "Direct company exposure can amplify company-specific losses.",
        "assumptions": ["Supplied marks describe the dated snapshot."],
        "uncertainty": ["Risk preferences and indirect fund overlap are unknown."],
        "what_could_change": [
            "A verified snapshot or supplied risk context could change the review."
        ],
    }


def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio(
        snapshot=Snapshot(
            as_of=date(2026, 10, 1),
            reporting_currency="CAD",
            accounts=[Account(id="tfsa", name="TFSA")],
            positions=[
                Position(
                    id="tfsa-xeqt-to",
                    account_id="tfsa",
                    kind="etf",
                    currency="CAD",
                    ticker="XEQT",
                    listing="XTSE",
                    shares=Decimal("100"),
                )
            ],
            fx=[],
        ),
        average_costs={"tfsa-xeqt-to": Decimal("30.00")},
        settings=PortfolioSettings(single_company_cap=Decimal("0.15")),
        benchmark=BenchmarkSetting(ticker="SPY", listing="XNYS", currency="USD", name="SPDR S&P 500"),
        unresolved=[],
    )


def default_model():
    return ScriptedModel(
        [
            ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
            ModelTurn(answer=recommendation()),
        ]
    )


def client(tmp_path, store=None, reference=None, model=None):
    ps = store or PortfolioStore(tmp_path)
    return TestClient(
        create_app(
            model=model or default_model(),
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(reference or FinancialEvidence()),
            portfolio_store=ps,
        )
    ), ps


def test_create_and_retrieve_hypothetical_artifact(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # revision 1

    draft = initial.model_copy(deep=True)
    draft.snapshot.positions.append(
        Position(
            id="tfsa-shop-to",
            account_id="tfsa",
            kind="stock",
            currency="CAD",
            ticker="SHOP",
            listing="XTSE",
            shares=Decimal("50"),
        )
    )
    draft.average_costs["tfsa-shop-to"] = Decimal("120.00")

    # Store hypothetical artifact via POST /api/portfolio/artifacts
    res = api.post(
        "/api/portfolio/artifacts",
        json={
            "portfolio": draft.model_dump(mode="json"),
            "label": "Hypothetical Draft with SHOP",
        },
    )
    assert res.status_code == 200, res.text
    ref = res.json()
    assert ref["id"].startswith("art-hypo-")
    assert ref["kind"] == "portfolio_snapshot"
    assert ref["label"] == "Hypothetical Draft with SHOP"
    assert ref["basis"] == "hypothetical"
    assert ref["revision"] is None
    assert ref["source_artifact_id"] is not None

    # Retrieve artifact reference
    ref_res = api.get(f"/api/portfolio/artifacts/{ref['id']}")
    assert ref_res.status_code == 200
    assert ref_res.json()["id"] == ref["id"]

    # Retrieve human payload (includes average costs)
    human_payload_res = api.get(f"/api/portfolio/artifacts/{ref['id']}/payload?projection=human")
    assert human_payload_res.status_code == 200
    human_data = human_payload_res.json()
    assert "tfsa-shop-to" in human_data["average_costs"]
    assert human_data["average_costs"]["tfsa-shop-to"] == "120.00"

    # Retrieve model payload (excludes average costs)
    model_payload_res = api.get(f"/api/portfolio/artifacts/{ref['id']}/payload?projection=model")
    assert model_payload_res.status_code == 200
    model_data = model_payload_res.json()
    assert model_data["average_costs"] == {}

    # Retrieve generic artifact envelope (human projection default)
    env_res = api.get(f"/api/artifacts/{ref['id']}")
    assert env_res.status_code == 200
    env_data = env_res.json()
    assert env_data["reference"]["id"] == ref["id"]
    assert env_data["reference"]["kind"] == "portfolio_snapshot"
    assert env_data["reference"]["basis"] == "hypothetical"
    assert env_data["payload"]["average_costs"]["tfsa-shop-to"] == "120.00"

    # Retrieve generic artifact envelope (explicit model projection)
    env_model_res = api.get(f"/api/artifacts/{ref['id']}?projection=model")
    assert env_model_res.status_code == 200
    env_model_data = env_model_res.json()
    assert env_model_data["reference"]["id"] == ref["id"]
    assert env_model_data["payload"]["average_costs"] == {}

    # Non-existent artifact returns 404
    assert api.get("/api/artifacts/art-nonexistent").status_code == 404

    # Verify committed portfolio revision and content unchanged
    committed = api.get("/api/portfolio").json()
    assert committed["revision"] == 1
    assert len(committed["portfolio"]["snapshot"]["positions"]) == 1


def test_hypothetical_analysis_api_boundary_and_unchanged_revision(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # revision 1

    draft = initial.model_copy(deep=True)
    draft.snapshot.positions.append(
        Position(
            id="tfsa-shop-to",
            account_id="tfsa",
            kind="stock",
            currency="CAD",
            ticker="SHOP",
            listing="XTSE",
            shares=Decimal("50"),
        )
    )

    art_res = api.post(
        "/api/portfolio/artifacts",
        json={
            "portfolio": draft.model_dump(mode="json"),
            "label": "Hypothetical Draft Portfolio",
        },
    )
    assert art_res.status_code == 200
    artifact_ref = art_res.json()

    # Analyze the draft snapshot, settings, and artifact_reference
    # Average costs stay outside the analysis request
    analysis_res = analyze_with(api, {
            "question": "Review exposure, concentration and fund overlap",
            "portfolio": draft.snapshot.model_dump(mode="json"),
            "settings": draft.settings.model_dump(mode="json"),
            "artifact_reference": artifact_ref,
        })
    assert analysis_res.status_code == 200, analysis_res.text
    result = analysis_res.json()
    assert result["status"] == "completed"
    # Result preserves artifact reference with hypothetical basis
    assert result["artifact_reference"] is not None
    assert result["artifact_reference"]["id"] == artifact_ref["id"]
    assert result["artifact_reference"]["basis"] == "hypothetical"

    # Verify that analysis analyzed the draft positions
    analyzed_pos_ids = [p["supplied"]["id"] for p in result["portfolio"]["positions"]]
    assert "tfsa-shop-to" in analyzed_pos_ids

    # Verify no proposal was created, applied, or revision advanced
    committed = api.get("/api/portfolio").json()
    assert committed["revision"] == 1
    proposals = api.get("/api/portfolio/proposals").json()
    assert len(proposals) == 0


def test_ordinary_analysis_uses_committed_state_while_draft_exists(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # revision 1

    # Store a hypothetical artifact
    draft = initial.model_copy(deep=True)
    draft.snapshot.positions.append(
        Position(
            id="tfsa-shop-to",
            account_id="tfsa",
            kind="stock",
            currency="CAD",
            ticker="SHOP",
            listing="XTSE",
            shares=Decimal("50"),
        )
    )
    api.post("/api/portfolio/artifacts", json={"portfolio": draft.model_dump(mode="json")})

    # Ordinary analysis sends committed snapshot without artifact_reference
    analysis_res = analyze_with(api, {
            "question": "Review exposure, concentration and fund overlap",
            "portfolio": initial.snapshot.model_dump(mode="json"),
            "settings": initial.settings.model_dump(mode="json"),
        })
    assert analysis_res.status_code == 200
    result = analysis_res.json()
    analyzed_pos_ids = [p["supplied"]["id"] for p in result["portfolio"]["positions"]]
    assert "tfsa-shop-to" not in analyzed_pos_ids
    assert "tfsa-xeqt-to" in analyzed_pos_ids


def test_hypothetical_analysis_rejects_invalid_inputs(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)

    # Invalid snapshot: negative shares or missing required fields
    invalid_snapshot = initial.snapshot.model_dump(mode="json")
    invalid_snapshot["reporting_currency"] = "INVALID"

    res = analyze_with(api, {
            "question": "Review invalid portfolio",
            "portfolio": invalid_snapshot,
        })
    assert res.status_code == 422
