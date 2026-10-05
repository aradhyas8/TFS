import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from analyst.api import create_app
from analyst.pipeline import InvalidReview, validate_prose
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import (
    AnalysisRequest,
    CompanyResearch,
    DiscoveryScan,
    FinancialEvidence,
    ResearchDocument,
)
from tests.test_analysis import recommendation, snapshot
from tests.test_stock import company_judgments, stock_comparison_judgments


def canadian_stock_request():
    return {
        "question": "Should I hold Canadian Acme in my portfolio?",
        "portfolio": snapshot(),
        "stock": {"position_id": "p2"},
    }


def canadian_research_fixture():
    documents = [
        {
            "id": "sedar_filing",
            "authority": "sedar_plus",
            "company_id": "acme",
            "url": "https://www.sedarplus.ca/csa-party/records/document.html?id=123",
            "published_on": "2026-02-01",
            "as_of": "2025-12-31",
            "title": "Acme Canadian annual filing (SEDAR+)",
            "excerpt": "Consolidated Canadian revenue and diluted shares are reported in the annual statements.",
            "available": True,
            "qa_available": False,
        },
        {
            "id": "issuer_release",
            "authority": "issuer",
            "company_id": "acme",
            "url": "https://investors.example.com/canadian-annual",
            "published_on": "2026-02-01",
            "as_of": "2025-12-31",
            "title": "Acme Canadian investor release",
            "excerpt": "Canadian market operational commentary and outlook.",
            "available": True,
            "qa_available": False,
        },
    ]
    facts = [
        {
            "id": "revenue",
            "metric": "revenue",
            "value": "1000",
            "unit": "currency",
            "currency": "CAD",
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "definition": "Consolidated annual revenue in CAD, without scaling",
            "document_ids": ["sedar_filing", "issuer_release"],
            "filing_checked": True,
            "notes_checked": True,
            "custom_tags_checked": True,
            "segments_checked": True,
        },
        {
            "id": "shares",
            "metric": "shares",
            "value": "10",
            "unit": "shares",
            "currency": None,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "definition": "Diluted weighted average shares, without scaling",
            "document_ids": ["sedar_filing", "issuer_release"],
            "filing_checked": True,
            "notes_checked": True,
            "custom_tags_checked": True,
            "segments_checked": True,
        },
    ]
    return {
        "company_id": "acme",
        "sector": "industrial",
        "cyclical": True,
        "documents": documents,
        "facts": facts,
        "issues": [],
    }


def canadian_stock_answer():
    answer = recommendation()
    answer.update(
        preferred_action="hold",
        reason="Hold conditionally while Canadian company cases are assessed against cash and existing concentration.",
        alternatives=[
            {"action": "reduce", "reason": "A conditional reduction could lower company exposure."},
            {"action": "no_action", "reason": "Retaining the snapshot avoids an unconfirmed transaction."},
        ],
        evidence_ids=["sedar_filing", "issuer_release"],
    )
    return answer


def run_canadian_stock(*, research=None, paths=None, request=None, answer=None, skip=None):
    fixture = research or canadian_research_fixture()
    source = ReviewedResearchProvider({"acme:XTSE": CompanyResearch.model_validate(fixture)})
    turns = [ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")])]
    for key, name, arguments in [
        ("sedar", "get_sedar_filings", {}),
        ("issuer", "get_issuer_material", {}),
        ("company", "calculate_company_cases", paths or company_judgments()),
    ]:
        if skip != name:
            turns.append(ModelTurn(calls=[ToolCall(key, name, json.dumps(arguments))]))
    req = request or canadian_stock_request()
    bound = AnalysisRequest.model_validate(req).model_dump(mode="json")
    turns.append(ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]))
    turns.append(ModelTurn(answer=answer or canadian_stock_answer()))
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post(
        "/api/analyze", json=req
    )
    return response, model


def test_canadian_stock_assessment_completes_in_shared_pipeline():
    response, model = run_canadian_stock()
    assert response.status_code == 200, response.text
    result = response.json()
    stock = result["stock"]
    assert stock["position_id"] == "p2"
    assert stock["reporting_currency"] == "CAD"
    assert stock["research"]["documents"][0]["authority"] == "sedar_plus"
    assert stock["research"]["documents"][0]["url"] == "https://www.sedarplus.ca/csa-party/records/document.html?id=123"
    assert stock["cases"][0]["terminal_price"] == "50"
    assert stock["cases"][1]["terminal_price"] == "100"
    assert stock["cases"][2]["terminal_price"] == "150"
    assert stock["cases"][1]["known_terminal_value"] == "500"
    assert result["recommendation"]["preferred_action"] == "hold"
    assert result["recommendation"]["amount"] is None
    assert "sedar_filing" in str(model.requests[-1])


def test_canadian_stock_rejects_sec_tool_call():
    source = ReviewedResearchProvider({"acme:XTSE": CompanyResearch.model_validate(canadian_research_fixture())})
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post(
        "/api/analyze", json=canadian_stock_request()
    )
    assert response.status_code == 502, response.text
    assert "The model did not produce a valid portfolio review." in response.json()["detail"]


def test_us_stock_rejects_sedar_tool_call():
    from tests.test_stock import research_fixture, stock_request
    source = ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())})
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sedar", "get_sedar_filings", "{}")]),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post(
        "/api/analyze", json=stock_request()
    )
    assert response.status_code == 502, response.text
    assert "The model did not produce a valid portfolio review." in response.json()["detail"]


def test_sedar_document_url_validation():
    base = {
        "id": "doc1",
        "company_id": "acme",
        "published_on": "2026-02-01",
        "as_of": "2025-12-31",
        "title": "SEDAR+ Annual Filing",
        "excerpt": "Extract",
        "available": True,
        "qa_available": False,
    }
    # Valid SEDAR+ URLs
    doc = ResearchDocument.model_validate({**base, "authority": "sedar_plus", "url": "https://www.sedarplus.ca/csa-party/records/document.html?id=123"})
    assert doc.url == "https://www.sedarplus.ca/csa-party/records/document.html?id=123"

    doc2 = ResearchDocument.model_validate({**base, "authority": "sedar", "url": "https://sedarplus.ca/csa-party/records/document.html"})
    assert doc2.url == "https://sedarplus.ca/csa-party/records/document.html"

    # Non-https
    with pytest.raises(ValidationError, match="Primary evidence needs a public HTTPS reference"):
        ResearchDocument.model_validate({**base, "authority": "sedar_plus", "url": "http://www.sedarplus.ca/csa-party/records/document.html"})

    # Non-sedar domain
    with pytest.raises(ValidationError, match="SEDAR\\+ evidence must reference an exact sedarplus.ca filing verification link"):
        ResearchDocument.model_validate({**base, "authority": "sedar_plus", "url": "https://example.com/filings/acme.pdf"})

    # Root path
    with pytest.raises(ValidationError, match="SEDAR\\+ evidence must reference an exact sedarplus.ca filing verification link"):
        ResearchDocument.model_validate({**base, "authority": "sedar_plus", "url": "https://www.sedarplus.ca/"})


@pytest.mark.parametrize("fault", ["missing_fact", "conflicting_fact", "unavailable_doc", "future_doc", "unchecked_notes"])
def test_missing_or_contradictory_canadian_evidence_leaves_cases_unknown(fault):
    fixture = canadian_research_fixture()
    paths = company_judgments()
    if fault == "missing_fact":
        fixture["facts"][0]["value"] = None
    elif fault == "conflicting_fact":
        fixture["facts"].append({**fixture["facts"][0], "id": "conflict", "value": "2500"})
    elif fault == "unavailable_doc":
        fixture["documents"][0]["available"] = False
    elif fault == "future_doc":
        fixture["documents"][0]["published_on"] = "2026-10-01"
    elif fault == "unchecked_notes":
        fixture["facts"][0]["notes_checked"] = False

    answer = canadian_stock_answer()
    if fault in {"unavailable_doc", "future_doc"}:
        answer["evidence_ids"] = ["issuer_release"]

    response, _ = run_canadian_stock(research=fixture, paths=paths, answer=answer)
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(case["terminal_price"] is None for case in result["stock"]["cases"])
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["recommendation"]["amount"] is None


@pytest.mark.parametrize("forbidden_prose", [
    "We used our automated SEDAR+ scraper to extract the filing data.",
    "The internal SEDAR+ database was queried for company metrics.",
    "This sale is completely tax-free under TFSA capital gains exemption.",
    "Guaranteed 0% tax rate on Canadian dividends in this account.",
])
def test_forbidden_canadian_prose_claims_rejected(forbidden_prose):
    with pytest.raises(InvalidReview):
        validate_prose(forbidden_prose)


def test_canadian_stock_missing_sedar_citation_yields_wait_for_inputs():
    # Model cites only issuer release and forgets the SEDAR+ filing
    answer = canadian_stock_answer()
    answer["evidence_ids"] = ["issuer_release"]
    response, _ = run_canadian_stock(answer=answer)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["recommendation"]["amount"] is None


def test_canadian_stock_in_new_cash_allocation():
    from analyst.financial_data import FakeFinancialProvider
    from tests.test_allocation import (
        allocation_answer,
        allocation_evidence,
        allocation_request,
        comparison_judgments,
    )

    class FakeDiscovery:
        async def scan(self, snap):
            return DiscoveryScan.model_validate({
                "scanned_at": datetime.now(UTC),
                "as_of": snap.as_of,
                "source": "Canadian discovery screen fixture",
                "candidates": [
                    {
                        "position": {
                            "id": "candidate-shop",
                            "account_id": "broker",
                            "kind": "stock",
                            "currency": "CAD",
                            "ticker": "SHOP",
                            "listing": "XTSE",
                            "company_id": "shopify",
                            "company_name": "Shopify Inc",
                            "shares": "0",
                        },
                        "as_of": snap.as_of,
                        "source": "Canadian screen",
                        "signal": "Operating performance and software platform expansion in Canada.",
                    }
                ],
            })

    req = allocation_request()
    discovery = FakeDiscovery()
    turns = [
        ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("scan", "scan_opportunities", "{}")]),
        ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(comparison_judgments()))]),
        ModelTurn(calls=[ToolCall("sizing", "size_allocation", json.dumps({
            "position_id": "fund", "min_weight": "0.55", "max_weight": "0.65",
            "reason": "Diversified exposure and cash reserve offer stronger balance than prospective addition."
        }))]),
        ModelTurn(answer=allocation_answer()),
    ]
    model = ScriptedModel(turns)
    response = TestClient(create_app(
        model=model,
        data=FakeDataProvider(),
        financial=FakeFinancialProvider(allocation_evidence()),
        discovery=discovery,
    )).post("/api/analyze", json=req)
    assert response.status_code == 200, response.text
    scan = response.json()["allocation"]["scan"]
    assert any(c["position"]["ticker"] == "SHOP" and c["position"]["listing"] == "XTSE" and c["position"]["currency"] == "CAD" for c in scan["candidates"])


@pytest.mark.anyio
async def test_canadian_stock_in_portfolio_review_reunderwriting():
    from analyst.calculations import review_portfolio
    from analyst.reunderwriting import bind_holdings
    from analyst.schemas import ReunderwritingResult, Snapshot
    snap_data = snapshot()
    snap_data["positions"].append({
        "id": "p_can",
        "account_id": "broker",
        "kind": "stock",
        "ticker": "SHOP",
        "listing": "XTSE",
        "company_id": "shopify",
        "company_name": "Shopify Inc",
        "shares": "10",
        "currency": "CAD",
        "mark": {"value": "100", "as_of": "2026-09-30", "source": "Manual mark"},
    })
    snap = Snapshot.model_validate(snap_data)
    review = review_portfolio(snap, FinancialEvidence())
    can_research = CompanyResearch.model_validate({
        "company_id": "shopify",
        "sector": "industrial",
        "cyclical": False,
        "documents": [
            {
                "id": "sedar_doc",
                "authority": "sedar_plus",
                "company_id": "shopify",
                "url": "https://www.sedarplus.ca/csa-party/records/document.html?id=shop",
                "published_on": "2026-02-01",
                "as_of": "2025-12-31",
                "title": "Shopify SEDAR+ filing",
                "excerpt": "Financial results",
                "available": True,
                "qa_available": False,
            }
        ],
        "facts": [],
        "issues": [],
    })
    from analyst.schemas import PortfolioReviewInput
    provider = ReviewedResearchProvider({"shopify": can_research})
    result = ReunderwritingResult(context=PortfolioReviewInput())
    await bind_holdings(snap, review, provider, result)
    assert "p_can" in result.research
    assert result.research["p_can"].company_id == "shopify"
    assert result.research["p_can"].documents[0].authority == "sedar_plus"
    assert not any("p_can: listing-specific cases are unavailable" in q for q in result.qualifications)
