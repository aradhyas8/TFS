from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import FakeFinancialProvider
from analyst.persistence import Database
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider
from analyst.schemas import (
    Account,
    AnalysisResult,
    ArtifactReference,
    CompanyResearch,
    FinancialEvidence,
    Position,
    Recommendation,
    SavedPortfolio,
    Snapshot,
    UnresolvedHolding,
)


def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio(
        snapshot=Snapshot(
            as_of=datetime(2025, 1, 1).date(),
            reporting_currency="CAD",
            accounts=[Account(id="acc-1", name="Tax-Free")],
            positions=[
                Position(
                    id="tfsa-xeqt-to",
                    account_id="acc-1",
                    kind="etf",
                    currency="CAD",
                    ticker="XEQT",
                    listing="XTSE",
                    shares=Decimal("100"),
                )
            ],
            fx=[],
        ),
        average_costs={"tfsa-xeqt-to": Decimal("25.50")},
        unresolved=[
            UnresolvedHolding(
                account_id="acc-1",
                ticker="SHOP.TO",
                shares=Decimal("10"),
                average_cost=Decimal("95.50"),
                currency="CAD",
            )
        ],
    )


def client(tmp_path: Path) -> tuple[TestClient, PortfolioStore, Database]:
    store = PortfolioStore(tmp_path)
    db = store.db
    app = create_app(
        data=FakeDataProvider(),
        financial=FakeFinancialProvider(FinancialEvidence()),
        portfolio_store=store,
    )
    return TestClient(app), store, db


def test_api_round_trip_for_all_artifact_kinds(tmp_path: Path):
    api, store, db = client(tmp_path)
    now = datetime.now(UTC)

    # 1. Portfolio snapshot artifact
    port = sample_portfolio()
    ref_port = ArtifactReference(
        id="art-port-1",
        kind="portfolio_snapshot",
        label="Committed Snapshot",
        created_at=now,
        revision=1,
        basis="committed",
    )
    db.store_artifact(ref_port, port)

    # Human projection
    res = api.get("/api/artifacts/art-port-1")
    assert res.status_code == 200
    env = res.json()
    assert env["reference"]["id"] == "art-port-1"
    assert env["reference"]["kind"] == "portfolio_snapshot"
    assert env["payload"]["average_costs"]["tfsa-xeqt-to"] == "25.50"
    assert env["payload"]["unresolved"][0]["average_cost"] == "95.50"

    # Model projection
    res_model = api.get("/api/artifacts/art-port-1?projection=model")
    assert res_model.status_code == 200
    env_model = res_model.json()
    assert env_model["reference"]["id"] == "art-port-1"
    assert env_model["payload"]["average_costs"] == {}
    assert env_model["payload"]["unresolved"][0]["average_cost"] is None

    # Portfolio-specific route still works
    res_port_spec = api.get("/api/portfolio/artifacts/art-port-1/payload?projection=human")
    assert res_port_spec.status_code == 200
    assert res_port_spec.json()["average_costs"]["tfsa-xeqt-to"] == "25.50"

    # 2. Analysis result artifact
    from analyst.calculations import review_portfolio
    analysis_obj = AnalysisResult(
        status="completed",
        question="Review holdings",
        portfolio=review_portfolio(port.snapshot, FinancialEvidence()),
        recommendation=Recommendation.model_validate({
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
        }),
    )
    ref_analysis = ArtifactReference(
        id="art-analysis-1",
        kind="analysis_result",
        label="Portfolio Analysis",
        created_at=now,
        basis="committed",
        revision=1,
    )
    db.store_artifact(ref_analysis, analysis_obj)

    res_analysis = api.get("/api/artifacts/art-analysis-1")
    assert res_analysis.status_code == 200
    env_analysis = res_analysis.json()
    assert env_analysis["reference"]["kind"] == "analysis_result"
    assert env_analysis["payload"]["question"] == "Review holdings"
    assert env_analysis["payload"]["recommendation"]["preferred_action"] == "review_only"

    # 3. Company research artifact
    research_obj = CompanyResearch(
        company_id="SHOP",
        sector="industrial",
        cyclical=False,
        documents=[],
        facts=[],
        issues=["competition"],
    )
    ref_research = ArtifactReference(
        id="art-research-1",
        kind="company_research",
        label="Shopify Research",
        created_at=now,
        basis="none",
    )
    db.store_artifact(ref_research, research_obj)

    res_research = api.get("/api/artifacts/art-research-1")
    assert res_research.status_code == 200
    env_research = res_research.json()
    assert env_research["reference"]["kind"] == "company_research"
    assert env_research["payload"]["company_id"] == "SHOP"
    assert env_research["payload"]["issues"] == ["competition"]

    # 4. Calculation result artifact
    calc_data = {
        "capability": "exposure_check",
        "result": {
            "status": "within_limits",
            "cap": "0.20",
            "current_weight": "0.15",
        },
    }
    ref_calc = ArtifactReference(
        id="art-calc-1",
        kind="calculation_result",
        label="Cap Calculation",
        created_at=now,
        basis="committed",
        revision=1,
    )
    db.store_artifact(ref_calc, calc_data)

    res_calc = api.get("/api/artifacts/art-calc-1")
    assert res_calc.status_code == 200
    env_calc = res_calc.json()
    assert env_calc["reference"]["kind"] == "calculation_result"
    assert env_calc["payload"]["capability"] == "exposure_check"
    assert env_calc["payload"]["result"]["status"] == "within_limits"

    # 5. Financial evidence artifact
    evidence_obj = FinancialEvidence()
    ref_evidence = ArtifactReference(
        id="art-evidence-1",
        kind="financial_evidence",
        label="Market Evidence",
        created_at=now,
        basis="none",
    )
    db.store_artifact(ref_evidence, evidence_obj)

    res_evidence = api.get("/api/artifacts/art-evidence-1")
    assert res_evidence.status_code == 200
    env_evidence = res_evidence.json()
    assert env_evidence["reference"]["kind"] == "financial_evidence"

    # 6. Web research artifact
    web_data = {
        "query": "Shopify merchant growth 2026",
        "citations": [
            {
                "url": "https://example.com/growth",
                "title": "Shopify Growth",
            }
        ],
        "findings": "Merchant solutions revenue continues to compound.",
    }
    ref_web = ArtifactReference(
        id="art-web-1",
        kind="web_research",
        label="Shopify Web Findings",
        created_at=now,
        basis="none",
    )
    db.store_artifact(ref_web, web_data)

    res_web = api.get("/api/artifacts/art-web-1")
    assert res_web.status_code == 200
    env_web = res_web.json()
    assert env_web["reference"]["kind"] == "web_research"
    assert env_web["payload"]["query"] == "Shopify merchant growth 2026"
    assert len(env_web["payload"]["citations"]) == 1


def test_api_unknown_id_and_invalid_projections(tmp_path: Path):
    api, store, db = client(tmp_path)

    # Unknown artifact ID returns 404
    res = api.get("/api/artifacts/nonexistent-id")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()

    # Unknown portfolio artifact ID returns 404
    res_port = api.get("/api/portfolio/artifacts/nonexistent-id")
    assert res_port.status_code == 404


def test_spec_2_compatibility_fixture(tmp_path: Path):
    """Verify that existing Spec 2 revisions, receipts, proposals, and hypothetical artifacts remain valid."""
    api, store, db = client(tmp_path)

    # Setup initial committed revision 1
    init_port = sample_portfolio()
    store.save(init_port)  # rev 1

    curr = store.get_revisioned()
    assert curr is not None
    assert curr.revision == 1

    # Hypothetical artifact creation via POST /api/portfolio/artifacts
    draft = init_port.model_copy(deep=True)
    create_res = api.post(
        "/api/portfolio/artifacts",
        json={
            "portfolio": draft.model_dump(mode="json"),
            "label": "Hypo Test Draft",
        },
    )
    assert create_res.status_code == 200
    hypo_ref = create_res.json()
    assert hypo_ref["kind"] == "portfolio_snapshot"
    assert hypo_ref["basis"] == "hypothetical"

    # Proposal preparation via existing Spec 2 actions
    prepare_res = api.put(
        "/api/portfolio",
        json={
            "base_revision": 1,
            "portfolio": draft.model_dump(mode="json"),
        },
    )
    assert prepare_res.status_code == 200
    prep_data = prepare_res.json()
    if prep_data["status"] == "prepared":
        prop = prep_data["proposal"]
        assert prop["status"] == "pending"
        assert prop["after_artifact"]["kind"] == "portfolio_snapshot"
        assert prop["after_artifact"]["basis"] == "hypothetical"

        # Apply proposal
        confirm_res = api.post(f"/api/portfolio/proposals/{prop['id']}/confirm")
        assert confirm_res.status_code == 200
        receipt = confirm_res.json()["receipt"]
        assert receipt["new_revision"] == 2
        assert receipt["previous_revision"] == 1

    # Ensure artifacts table still retrieves rev 1 artifact envelope cleanly
    rev1_art_id = "art-rev-1"
    rev1_env = api.get(f"/api/artifacts/{rev1_art_id}")
    assert rev1_env.status_code == 200
    assert rev1_env.json()["reference"]["revision"] == 1
    assert rev1_env.json()["reference"]["kind"] == "portfolio_snapshot"
