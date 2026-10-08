"""Company-case arithmetic checked against values worked out independently (by hand or in plain floats), plus the
decision gating, comparison value and output precision around it."""

import copy
import re
from decimal import Decimal

from tests.test_analysis import snapshot
from tests.test_stock import (
    company_judgments,
    research_fixture,
    run_stock,
    stock_answer,
    stock_request,
)


def case(name, *, growth, margin, conversion="1", reinvestment="0", dilution="0", payout="0", discount="0.1", multiple="10"):
    row = company_judgments()["cases"][0]
    as_list = lambda value: value if isinstance(value, list) else [value] * 5  # noqa: E731
    row.update(name=name, growth=as_list(growth), margins=as_list(margin), cash_conversion=as_list(conversion),
               reinvestment=as_list(reinvestment), dilution=as_list(dilution), payout=as_list(payout),
               discount_rate=discount, exit_multiple=multiple, exit_sensitivity=[multiple] * 3)
    return row


def judgments(method, cases):
    paths = company_judgments()
    paths.update(method=method, cases=cases, metric_fact_id="fcf" if method == "fcf_exit" else None)
    return paths


def research(revenue, shares, fcf=None):
    record = research_fixture()
    record["facts"][0]["value"], record["facts"][1]["value"] = revenue, shares
    if fcf is not None:
        record["facts"].append({**record["facts"][0], "id": "fcf", "metric": "free_cash_flow", "value": fcf, "definition": "Reported free cash flow"})
    return record


def request(price):
    body = copy.deepcopy(stock_request())
    body["portfolio"]["positions"][0]["mark"]["value"] = price
    return body


def stock_of(response):
    assert response.status_code == 200, response.text
    return response.json()["stock"]


def close(value, expected, places=4):
    assert abs(Decimal(value) - Decimal(str(expected))) < Decimal(10) ** -places, (value, expected)


def test_fcf_case_when_growth_equals_the_discount_rate():
    # Revenue 1,000 grows 10% a year and is discounted at 10%, so growth and discounting cancel exactly:
    # value today = 1,000 x (0.2 x 0.9 x 0.9) x 15 / 100 shares = 24.3 per share. Year 5: revenue 1,610.51,
    # FCF 1,610.51 x 0.162 = 260.90262, equity 3,913.5393, per share 39.135393, discount factor 1.1^5 = 1.61051.
    paths = judgments("fcf_exit", [case(name, growth="0.1", margin=margin, conversion="0.9", reinvestment="0.1", multiple="15")
                                   for name, margin in [("downside", "0.1"), ("base", "0.2"), ("upside", "0.3")]])
    response, _ = run_stock(research=research("1000", "100", fcf="180"), paths=paths, request=request("30"))
    stock = stock_of(response)
    base = stock["cases"][1]
    assert (base["starting_metric"], base["starting_shares"]) == ("1000", "100")
    assert [year["revenue"] for year in base["path"]] == ["1100", "1210", "1331", "1464.1", "1610.51"]
    assert base["path"][-1]["metric"] == "260.9" and base["path"][-1]["metric_margin"] == "0.162"
    assert (base["equity_value"], base["terminal_price"], base["discount_factor"]) == ("3913.54", "39.1354", "1.61051")
    assert (base["present_value_of_exit"], base["present_value_of_distributions"], base["present_value_per_share"]) == ("24.3", "0", "24.3")
    assert [row["present_value_per_share"] for row in stock["cases"]] == ["12.15", "24.3", "36.45"]
    valuation = stock["valuation"]
    # Price 30 sits between base (24.30) and upside (36.45); 30 / 24.3 - 1 = 0.234567...
    assert (valuation["price"], valuation["position"], valuation["price_to_base"]) == ("30", "base_to_upside", "0.2346")
    # Reported FCF 180 / revenue 1,000 = 18%; the base case's first year is 0.2 x 0.9 x 0.9 = 16.2%: within a quarter.
    assert (valuation["reported_margin"], valuation["modeled_first_year_margin"], valuation["notes"]) == ("0.18", "0.162", [])


def test_dilution_and_payout_without_discounting():
    # Flat 1,000 revenue at a 10% net margin earns 100 a year. Shares grow 2% a year from 100, half of earnings is
    # paid out and nothing is discounted. Payouts: 0.5 x (1.02^-1 + ... + 1.02^-5) = 2.3567298;
    # exit: 100 x 10 / (100 x 1.02^5) = 9.0573081; together 11.4140379 per share.
    paths = judgments("earnings_exit", [case(name, growth="0", margin="0.1", dilution="0.02", payout="0.5", discount="0") for name in ("downside", "base", "upside")])
    stock = stock_of(run_stock(research=research("1000", "100"), paths=paths, request=request("100"))[0])
    base = stock["cases"][1]
    close(base["present_value_of_distributions"], 2.3567298)
    close(base["terminal_price"], 9.0573081)
    close(base["present_value_per_share"], 11.4140379)
    assert base["path"][-1]["diluted_shares"] == "110" and base["path"][0]["distribution_per_share"] == "0.4902"
    assert stock["valuation"]["position"] == "above_upside"  # 100 is far above an 11.41 value in every case


AVGO = {  # live base case from 2026-10-08, against Broadcom's FY ended 2025-11-02 as filed with SEC
    "growth": ["0.18", "0.15", "0.12", "0.10", "0.08"], "margins": ["0.31", "0.32", "0.33", "0.34", "0.34"],
    "cash_conversion": ["0.90", "0.92", "0.93", "0.93", "0.94"], "reinvestment": ["0.12", "0.12", "0.11", "0.10", "0.10"],
}


def test_avgo_base_case_audit():
    base = case("base", growth=AVGO["growth"], margin=AVGO["margins"], conversion=AVGO["cash_conversion"],
                reinvestment=AVGO["reinvestment"], dilution="0.01", payout="0.2", discount="0.10", multiple="20")
    others = [case(name, growth="0", margin="0.3", conversion="0.9", reinvestment="0.1", dilution="0.01", payout="0.2", multiple="20") for name in ("downside", "upside")]
    paths = judgments("fcf_exit", [others[0], base, others[1]])
    stock = stock_of(run_stock(research=research("63887000000", "4853000000", fcf="26914000000"), paths=paths, request=request("376.51"))[0])
    row = stock["cases"][1]
    # Independently, in floats: revenue 63.887B x 1.18 x 1.15 x 1.12 x 1.10 x 1.08 = 115.3524B; FCF = x 0.34 x 0.94 x 0.90
    # = 33.17998B; shares 4.853B x 1.01^5 = 5.100552B; exit 33.17998B x 20 / 5.100552B = 130.10348; / 1.1^5 = 80.78403;
    # discounted 20% payouts 3.86353; today 84.64756 per share.
    close(row["path"][-1]["revenue"], 115352445479.04, 2)
    close(row["terminal_metric"], 33179977417.59, 2)
    close(row["terminal_shares"], 5100551773, 0)
    close(row["equity_value"], 663599548351.82, 2)
    close(row["terminal_price"], 130.10348, 4)
    close(row["present_value_of_exit"], 80.78403, 4)
    close(row["present_value_of_distributions"], 3.86353, 4)
    close(row["present_value_per_share"], 84.64756, 4)
    valuation = stock["valuation"]
    # 0.31 x 0.90 x 0.88 = 24.552% modeled against 26.914 / 63.887 = 42.128% reported: flagged as a judgment gap.
    assert (valuation["modeled_first_year_margin"], valuation["reported_margin"]) == ("0.2455", "0.4213")
    assert any("first-year free cash flow margin (24.6%) differs from the reported 42.1%" in note for note in valuation["notes"])
    assert valuation["price_to_base"] == "3.448"  # 376.51 / 84.64756 - 1


def test_avgo_required_exit_multiple_includes_discounting_and_payouts():
    # Same AVGO base case. Solve 376.51 = M x 6.5051741 / 1.1^5 + 3.8635333 for M:
    # (376.51 - 3.8635333) x 1.61051 / 6.5051741 = 92.2575. Price / year-5 FCF per share alone would be 57.88x.
    base = case("base", growth=AVGO["growth"], margin=AVGO["margins"], conversion=AVGO["cash_conversion"],
                reinvestment=AVGO["reinvestment"], dilution="0.01", payout="0.2", discount="0.10", multiple="20")
    paths = judgments("fcf_exit", [case(name, growth="0", margin="0.3", conversion="0.9", reinvestment="0.1", multiple="20") for name in ("downside", "upside")])
    paths["cases"].insert(1, base)
    stock = stock_of(run_stock(research=research("63887000000", "4853000000", fcf="26914000000"), paths=paths, request=request("376.51"))[0])
    assert stock["cases"][1]["required_exit_multiple"] == "92.26"


def test_provisional_inputs_and_missing_rules_do_not_hide_the_view():
    answer = stock_answer()
    answer.update(preferred_action="add", reason="Add conditionally: the base case supports the current price.",
                  alternatives=[{"action": "hold", "reason": "Holding keeps today's exposure if the cases weaken."}])
    response, _ = run_stock(answer=answer)  # no settings, a manual broker-display mark and supplied FX
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "add" and result["recommendation"]["amount"] is None
    withheld = " ".join(result["stock"]["sizing_withheld"])
    assert "No single-company cap is set" in withheld and "The price is manual" in withheld and "USD/CAD is" in withheld


def test_set_rules_that_are_broken_still_block_adding():
    answer = stock_answer()
    answer.update(preferred_action="add", reason="Add conditionally: the base case supports the current price.")
    body = stock_request()
    body["settings"] = {"single_company_cap": "0.05", "active_budget": "0.9"}
    response, _ = run_stock(answer=answer, request=body)
    assert response.json()["recommendation"]["preferred_action"] in {"review_only", "wait_for_inputs"}


def test_missing_company_data_still_waits():
    record = research_fixture()
    record["facts"] = [fact for fact in record["facts"] if fact["metric"] != "revenue"]
    response, _ = run_stock(research=record)
    assert response.json()["recommendation"]["preferred_action"] == "wait_for_inputs"


def test_comparison_value_known_before_unmodeled_costs_and_taxes():
    comparison = run_stock()[0].json()["comparison"]
    company = next(alt for alt in comparison["alternatives"] if alt["selection"]["id"] == "company")
    keep = next(alt for alt in comparison["alternatives"] if alt["selection"]["id"] == "keep")
    base = company["cases"][1]
    assert base["terminal_value"] is None and base["comparison_value"] == base["known_terminal_value"] == "1300"
    assert base["unmodeled"] == ["transaction costs", "taxes"]
    assert keep["cases"][1]["unmodeled"] == ["taxes"]  # no trade, so no trading cost


def test_api_values_have_sensible_precision():
    paths = judgments("fcf_exit", [case(name, growth="0.07", margin="0.33", conversion="0.91", reinvestment="0.13", dilution="0.013", payout="0.17", discount="0.093", multiple="17")
                                   for name in ("downside", "base", "upside")])
    stock = stock_of(run_stock(research=research("63887000000", "4853000000"), paths=paths, request=request("376.51"))[0])
    decimals = lambda value: len(value.split(".")[1]) if value and "." in value else 0  # noqa: E731
    for row in stock["cases"]:
        assert max(decimals(row[key]) for key in ("terminal_price", "present_value_per_share", "present_value_of_exit")) <= 4
        assert max(decimals(row[key]) for key in ("terminal_metric", "equity_value", "known_terminal_value")) <= 2
        assert decimals(row["terminal_shares"]) == 0 and decimals(row["required_exit_multiple"]) <= 2
    assert not re.search(r"\d\.\d{11,}", str(stock["cases"]))
    assert snapshot()["positions"][0]["mark"]["value"] == "100"  # fixtures are not mutated by the requests above
