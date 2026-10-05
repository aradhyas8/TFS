"""Only imported by Playwright's local test server; production has no fake-mode flag."""

import asyncio
import json
import socket
from typing import Any

from analyst.api import create_app
from analyst.config import Settings
from analyst.financial_data import FakeFinancialProvider
from analyst.providers import FakeDataProvider, ModelTurn, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import CompanyResearch, FinancialEvidence, Snapshot
from tests.test_allocation import allocation_answer, allocation_evidence, comparison_judgments
from tests.test_comparison import case_drivers, driver, judgments
from tests.test_freshness import evidence_fixture
from tests.test_portfolio_review import assessment, review_answer, review_research_fixture
from tests.test_stock import (
    company_judgments,
    research_fixture,
    stock_answer,
    stock_comparison_judgments,
)

# No network outside the local test servers, even if a provider is changed accidentally.
original_connect = socket.socket.connect


def local_connect(sock: socket.socket, address: Any) -> None:
    if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
        raise AssertionError("E2E tests must not contact live services.")
    original_connect(sock, address)


socket.socket.connect = local_connect  # type: ignore[method-assign]


class BrowserTestModel:
    async def respond(self, messages: list[dict[str, Any]], *, require_tool: bool) -> ModelTurn:
        if require_tool:
            if json.loads(messages[1]["content"])["question"] == "Slow model review":
                await asyncio.sleep(31)
            return ModelTurn(calls=[ToolCall("browser_tool", "review_portfolio", "{}")])
        request = json.loads(messages[1]["content"])
        tool = json.loads(messages[-1]["output"])
        if request.get("theme"):
            from tests.test_theme import theme_turns
            scripted = theme_turns(request)
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            for turn in scripted[:-1]:
                if turn.calls[0].name not in called:
                    return turn
            return scripted[-1]
        if request.get("new_cash"):
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            for name, args in [("scan_opportunities", {}), ("calculate_comparison", comparison_judgments()),
                               ("size_allocation", {"position_id": "fund", "min_weight": "0.55", "max_weight": "0.65",
                                                    "reason": "Diversification and a retained reserve justify this exposure range."})]:
                if name not in called:
                    return ModelTurn(calls=[ToolCall(name, name, json.dumps(args))])
            return ModelTurn(answer=allocation_answer())
        if request.get("portfolio_review"):
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            if "reunderwrite_holding" not in called:
                args = assessment()
                if not request["portfolio_review"]["prior_theses"]:
                    args["assessment"]["status"] = "unknown"
                return ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(args))])
            if "calculate_comparison" not in called:
                return ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(request)))])
            if request["question"] == "Review supported reduction" and "size_review" not in called:
                return ModelTurn(calls=[ToolCall("sizing", "size_review", json.dumps({"position_id": "p1", "cash_position_id": "c2", "min_weight": "0.4", "max_weight": "0.5", "reason": "Weaker demand justifies lower issuer exposure and a retained reserve."}))])
            answer = review_answer()
            answer.update(preferred_action="reduce", reason="Weaker demand warrants a conditional reduction despite prior ownership.",
                          alternatives=[{"action": "no_action", "reason": "Retaining exposure is conditional on the current demand evidence improving."}],
                          evidence_ids=["review-p1-filing", "review-p1-issuer"])
            return ModelTurn(answer=answer)
        if request.get("stock"):
            called = {item["name"] for item in messages if item.get("type") == "function_call"}
            for name, args in [("get_sec_filings", {}), ("get_issuer_material", {}),
                               ("calculate_company_cases", company_judgments()),
                               ("calculate_comparison", stock_comparison_judgments(request))]:
                if name not in called:
                    return ModelTurn(calls=[ToolCall(name, name, json.dumps(args))])
            answer = stock_answer()
            if request["question"] == "Review unavailable primary facts":
                answer["evidence_ids"] = []
            return ModelTurn(answer=answer)
        if request.get("comparison") and "total_value" in tool:
            template = judgments()["alternatives"][0]["cases"]
            positions = {row["id"]: row for row in request["portfolio"]["positions"]}
            selected = []
            for alt in request["comparison"]["alternatives"]:
                ids = request["comparison"]["scope_position_ids"] if alt["kind"] == "no_action" else [alt["position_id"]]
                drivers = [driver(key, cash=positions[key]["kind"] == "cash") for key in ids]
                selected.append({"alternative_id": alt["id"], "cases": [
                    {**case, "drivers": case_drivers(drivers, case["name"])} for case in template
                ]})
            return ModelTurn(calls=[ToolCall("browser_comparison", "calculate_comparison", json.dumps({"alternatives": selected}))])
        if request["question"] == "Check model proposal" and "total_value" in tool:
            return ModelTurn(calls=[ToolCall("browser_proposal", "check_proposed_changes", json.dumps({
                "new_cash": [], "trades": [{"position_id": "p2", "shares_change": "2", "cash_position_id": "c2"}],
            }))])
        # Actual deterministic tool output has to exist; this provider cannot skip it.
        assert "total_value" in tool and "direct_companies" in tool or "post_total_value" in tool and "guardrails" in tool or "alternatives" in tool and "calculation_basis" in tool
        answer = {
            "preferred_action": "review_only",
            "amount": None,
            "reason": f"Regarding '{request['question']}': review the supplied direct company exposure across accounts.",
            "alternatives": [
                {
                    "action": "clarify_inputs",
                    "reason": "Supply a baseline and personal risk context.",
                }
            ],
            "downside": "Company-specific losses can affect concentrated holdings.",
            "assumptions": ["Supplied marks represent this dated snapshot."],
            "uncertainty": ["Personal guardrails and indirect fund overlap remain unknown."],
            "what_could_change": [
                "Verified identities, updated marks or risk context could change the review."
            ],
        }
        if request["question"] == "Return invalid output":
            answer["amount"] = "1000"
        if request["question"] == "Check model proposal":
            answer.update(preferred_action="no_action", reason="Strong conviction permits an exception to configured limits.")
            answer["downside"] = "Strong conviction permits an exception in the downside explanation."
        if request.get("comparison"):
            answer.update(
                preferred_action="no_action",
                reason="The compared cases do not establish a superior action while fund exposure evidence and personal risk context remain provisional. Retaining the actual holdings and cash is a conditional alternative.",
                alternatives=[{"action": "clarify_inputs", "reason": "Confirm fund exposure, costs, income and personal risk context before deciding."}],
                downside="The diversified fund retains equity and currency downside; cash faces falling reinvestment rates. No action retains the existing mix and its risks.",
                assumptions=["Exposure, income, rate and currency paths are judgments; calculations use the dated common capital basis."],
                uncertainty=["Unknown transaction costs and personal tax consequences leave terminal values incomplete."],
                what_could_change=["Supported fund facts, revised rate or currency paths, and supplied risk context could distinguish the alternatives."],
            )
        return ModelTurn(answer=answer)


financial = FakeFinancialProvider()


class BrowserTestData(FakeDataProvider):
    def snapshot(self, supplied: Snapshot) -> Snapshot:
        # Browser journeys run serially. Bind the external source fixture to the
        # submitted listing; every request resets it, including ordinary broker marks.
        financial.reference = FinancialEvidence()
        stock_research.records = {"acme": CompanyResearch.model_validate(review_research_fixture())}
        for position in supplied.positions:
            if position.id == "fund" and position.mark and position.mark.source == "Fixture allocation":
                financial.reference = allocation_evidence()
            if position.id == "p1" and position.mark and position.mark.source.startswith("Fixture "):
                fixture = evidence_fixture()
                scenario = position.mark.source.removeprefix("Fixture ")
                if scenario == "missing_research":
                    stock_research.records = {}
                    continue
                if scenario == "ambiguous":
                    fixture["identities"]["p1"]["status"] = "ambiguous"
                else:
                    fixture["quotes"]["p1"]["status"] = scenario
                financial.reference = FinancialEvidence.model_validate(fixture)
        return super().snapshot(supplied)


stock_research = ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())})


app = create_app(
    model=BrowserTestModel(),
    data=BrowserTestData(),
    financial=financial,
    research=stock_research,
    settings=Settings("sk-test-backend-only-never-browser", "test-model"),
)
