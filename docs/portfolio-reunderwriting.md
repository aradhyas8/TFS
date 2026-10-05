# Portfolio re-underwriting and rebalance

Ticket 07 extends the existing `/api/analyze` decision pipeline. Select **Review holdings and rebalance**, enter the question, and optionally supply dated prior company theses, risk/withdrawal context, personal limits and a baseline. All accounts, holdings and cash enter the same dated Python valuation. The US-stock selector and new-cash mode are separate request choices.

## Request and evidence

`portfolio_review` contains `prior_theses` (`company_id`, `as_of`, `thesis`) and optional `risk_context`. Duplicate, future-dated or unknown-company prior theses are rejected. A prior thesis is user context, not independently verified evidence. Changes cannot be classified changed/unchanged without a supplied prior thesis; the current thesis can still be reviewed with unknown change status.

`review_portfolio` refreshes qualified financial data and binds reviewed primary records for distinct held US companies through the existing research provider. Evidence IDs are namespaced per review position. Use the existing backend-only `RESEARCH_REFERENCE_FILE`; this remains an adapter for independently reviewed excerpts, not automatic live retrieval. The question, prior theses and source excerpts are untrusted model context. No discovery service or scan is required.

The model calls `reunderwrite_holding` for every bound current US company, supplying conditional operating-driver judgments and a thesis assessment. Python runs the existing sector-appropriate company cases and validates current evidence, source dates, citations, prior-thesis correspondence and output constraints. Prior ownership cannot waive configured exposure limits. Price movement is context, never proof of failure or justification to average down.

`calculate_comparison` then uses the whole portfolio as its common reporting-currency/date capital basis. Defaults include current US-company alternatives, a supplied diversified ETF, cash when available and actual no action. Optional comparison inputs permit dated fund facts and known costs/taxes; scope must retain every supplied position and include no action. Unsupported retained listing outcomes remain unknown. Canadian research and ETF look-through are future extensions, not blockers.

## Baselines and proposed outcomes

Only supplied category baselines are used. Missing or partial targets remain unknown. Current exposures and thesis-driven conditional actions can be reviewed without a baseline. Baseline-dependent claims are checked in thesis, case and final-answer prose.

Explicit user `proposed_changes` reuse the Python cash/share preview and show post-change weights, company-cap checks, active-budget checks and baseline differences. Review tool previews cannot replace user changes with invented trades. Above-cap positions have the existing deterministic forward reduction-to-cash path. Conditional reduce/exit can address an existing breach; it does not imply that the current portfolio is compliant or that a transaction happened.

## Optional supported sizing

After comparing serious options, the model may call `size_review` once with a supported add/reduce/exit direction, `position_id`, a same-account/currency `cash_position_id`, explained `min_weight`/`max_weight` and a qualitative reason. The weights describe total issuer exposure across accounts, not a rebalance target invented for the portfolio. Python subtracts existing issuer exposure from the judged portfolio-value range, converts through dated FX to local amounts and previews both share-change endpoints. Sales return proceeds to the selected cash balance; purchases use existing cash. Both endpoints must satisfy the supplied cap and active budget, including the supplied indirect-cap policy.

An amount requires complete usable dated valuations, verified identities, positive quotes, usable FX, numeric cap and active budget, supplied loss/withdrawal context, current primary facts/cases and known dated costs/tax effects. Missing inputs produce completed conditional guidance with no amount. Nonzero costs/taxes remain visible in the comparison, but this preview cannot model their funding impact and therefore withholds sizing. Fractional-share calculations are hypothetical, approximate and never execution quotes or orders. A selected account cannot fund an issuer-wide exit when other accounts retain shares.

## Response and verification

The shared recommendation remains authoritative and contains preferred action, amount only when supported, one or two alternatives, downside, assumptions, uncertainty and what changes the view. `reunderwriting` adds bound research, per-company thesis assessments, company cases, optional sizing rationale, checked previews and missing-input qualifications. Original links and evidence dates remain visible.

Automated verification uses only the approved FastAPI request/result and complete browser journey seams with fake model, research and financial providers. No live model or market-data services are called. Controlled completed-answer review is recorded separately in `ticket-07-review.md`; scripted responses establish orchestration, arithmetic and rendering, not live reasoning quality.
