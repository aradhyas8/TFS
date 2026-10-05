# Personal Investment Analyst

A standalone Next.js and FastAPI app for tickets 01–05: enter a dated whole-portfolio snapshot, supply optional personal settings, select comparison alternatives, ask a question, and receive calculated exposure, guardrail checks and conditional five-year cases. All code is new.

## Run locally

Requires Python 3.11+ and Node.js 20.9+. From this directory on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
Copy-Item backend/.env.example backend/.env
```

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in **backend/.env**. Choose a model available to your account that supports Responses API function calling and structured outputs. No model is silently selected. Never put credentials in frontend files or `NEXT_PUBLIC_*` variables.

`backend/requirements-dev.lock` records the exact verified dependency versions. For a matching environment, install it with `python -m pip install -r backend/requirements-dev.lock` inside the virtualenv, then install the project with `python -m pip install --no-deps -e backend`. The frontend uses `package-lock.json` with `npm ci`.

Start FastAPI in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn analyst.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

Start Next.js in another:

```powershell
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. Without backend credentials, portfolio entry and CSV loading work, and question submission returns an explicit configuration error. The deployed application runs independently of Codex.

For production, set the Next.js server's `BACKEND_URL` **before building**, run `npm run build`, then `npm start`. The default is `http://127.0.0.1:8000`. Run FastAPI separately; Next.js forwards `/api/*` to it on the server. Only the backend owns OpenAI credentials. These instructions target a local personal app; remote public hosting is outside ticket 01.

## Snapshot entry and CSV

Set an as-of date and reporting currency, add accounts, then holdings or cash rows. For direct stocks, use a stable **company ID shared across all accounts and listings** and a company name. A ticker plus exchange identifies the supplied listing. ETFs need a ticker and listing; indirect company exposure stays unknown. Identities remain user supplied unless independently reviewed backend source records resolve and verify them. Verified source metadata can fill omitted issuer/listing fields; conflicts remain unresolved.

Supply shares, an unadjusted mark, its actual date and a source label. Cash uses a balance and currency. For foreign-currency positions, supply a direct FX pair into the reporting currency, its rate, actual date and source. Rates are reporting-currency units per one local-currency unit. Marks and FX must match the snapshot date to support valuation; older, future or missing data stays unknown. No cross rates or older dates are inferred. The optional Bank of Canada adapter supports dated CAD rates and discloses inverse conversion. Quote capture times and price basis can be added in the editor; unknown capture times remain unknown.

Download the CSV header template in the app. A worked fictional example is [examples/portfolio.csv](examples/portfolio.csv), dated **2026-09-30** in **CAD**. Its expected totals are CAD 3,600 portfolio value, CAD 2,300 stock value, CAD 1,300 cash, and CAD 2,300 direct Acme exposure (63.89%). It contains two accounts, two listings and two cash balances. This is a calculation fixture, not an investment suggestion.

The exact CSV header is:

```csv
row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source
```

Use `stock`, `etf`, `cash`, `fx`, or `account` row types. An `account` row preserves an empty account. Position rows carry a unique position `id` and consistent `account_id,account_name`; FX rows use `currency,to_currency,fx_rate,fx_date,fx_source`. Fields irrelevant to the row type must be empty. Import replaces the editor snapshot after successful validation; malformed input leaves the current snapshot intact. Files are limited to 1 MB, accounts to 100, positions to 2,000 and FX pairs to 100. This milestone accepts nonnegative shares and cash, and rejects shorts or negative balances explicitly.

## Shared decision boundary

`POST /api/analyze` accepts `{question, portfolio, settings?, proposed_changes?, comparison?, stock?}` and returns `{status, question, portfolio, recommendation, proposals, comparison, stock}`. `/docs` on FastAPI exposes the complete typed contract. `POST /api/portfolio/csv` accepts `{csv, as_of, reporting_currency}` and returns the same snapshot shape accepted by analysis.

The backend validates the request, binds the snapshot from the data provider, asks the model to call `review_portfolio`, dispatches the allowlisted Python tool, returns its computed result to the model, validates the final recommendation and preserves the deterministic result in the response. The portfolio tool has no arguments: a model cannot replace the submitted holdings or valuation inputs. The loop is bounded to eight turns. Backend-bound `resolve_identities`, `get_quotes` and `get_fx` tools share one request-local evidence set with `review_portfolio`. All take no financial inputs from the model. When alternatives are selected, the model must call `calculate_comparison` with explained future-driver judgments before answering. Production uses one backend-owned OpenAI Responses integration, without extra orchestration frameworks or a database. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) document the integration pattern.

Python uses Decimal arithmetic, pandas grouping across accounts, and NumPy completeness checks. Values are serialized as decimal strings; weights are fractions rounded to eight decimal places. The browser only formats these fields. No adjusted-price returns, dividends, splits, costs or tax effects are inferred from a snapshot.

Missing valuation makes the complete total and all portfolio weights `null`. A separately labeled known subtotal remains available. Missing baseline and guardrails remain `null`. Recommendations at this milestone are limited to review, clarification or no action, and amount must be `null`. Unknown tools, invalid arguments, extra output fields, unsupported numeric prose, trade direction, refusals and incomplete answers fail without a completed recommendation. Explicit hypothetical changes can be checked by `check_proposed_changes`; they are previews, never orders or justified sizing. Qualitative claims still require separate human judgment; schema and arithmetic checks do not establish reasoning quality.

Ticket 02 adds dated financial evidence and refreshed valuation to `analyst.pipeline.analyze`. See [docs/financial-sources.md](docs/financial-sources.md) for source qualification, broker fallback, independently reviewed backend references and optional Bank of Canada FX. Quote source, status, as-of date, capture time and age appear in the same result. Cached/manual valuations are provisional; unusable identity, quote or FX leaves values unknown. `source_inputs_usable` describes source completeness; `sizing_eligible` stays false because this milestone checks exposures without establishing justified sizing. Issuer research, ETF look-through and saved decisions remain in later tickets.

## Explicit portfolio settings and previews

Ticket 03 adds optional `settings`: `single_company_cap`, `active_budget`, `baseline`, `indirect_cap_policy`, and `cash_is_deliberate_tilt`. Weights are decimal fractions between zero and one. No limits, baseline, cash intent or risk score are inferred. Settings live in the frontend form and request-local backend context; there is no persistence or risk-profile subsystem. Clearing a field restores unknown. A baseline can partially specify `stocks`, `diversified_etfs`, `sector_theme_etfs`, and `cash`; supplied weights cannot sum above one. Missing targets remain unknown and are not normalized or filled.

Direct company caps aggregate all accounts and listings. Existing breaches show a conditional reduction to cash retained in the whole portfolio; this is a dated exposure gap before unknown costs and taxes, not an order or an approved exception. Individual stocks count toward the active budget. Each ETF needs an explicit `etf_role` of `diversified` or `sector_theme`, available in the editor after CSV import. Diversified ETFs are excluded; sector/theme ETFs count. Deliberate excess cash contributes `max(cash - baseline.cash × portfolio total, 0)` only with explicit cash intent and a cash baseline. An explicit false cash intent excludes that tilt; absent intent or baseline leaves the contribution unknown. A known active subtotal may breach a budget, but missing contributions cannot clear it.

Choose `direct_only` or `include_known_indirect` as the indirect cap policy. ETF holdings data is still unavailable until ticket 09. If ETFs are present and a cap could depend on overlap, a below-cap direct position does not clear the cap; missing overlap stays unknown. A direct breach remains a breach and its reduction is only a lower bound when overlap could count.

`proposed_changes` contains `new_cash: [{cash_position_id, amount}]` and `trades: [{position_id, shares_change, cash_position_id}]`. Both arrays are required when a preview is supplied. It references existing rows: new cash uses the selected balance's currency, and each share change must use a cash balance in the same account and quote currency. Positive share changes consume cash; negative changes return proceeds to cash. Duplicate changes, short positions and negative final cash are disallowed. The backend calculates pre/post weights with all existing cash and explicit new cash using the same dated marks and FX. It does not infer transfers, currency execution, costs or tax effects.

The model's `check_proposed_changes` tool uses the same Python checks and cannot change holdings identities, valuation inputs or settings. Model previews must use exactly the user-supplied new cash, or none if absent. Every checked preview remains in the result. A cap/budget breach produces `blocked`; missing applicable inputs produce `unknown`. Either prevents the final recommendation from clearing the preview, regardless of model conviction. `within_limits` means only that the supplied limits passed under the dated valuation; allocation amount remains unknown. Automated tests cover the API and rendered browser result using fake providers only.

## Conditional ETF, cash and no-action comparison

Ticket 04 extends this same request with optional selected alternatives. Enable the comparison, select actual holdings/cash as a common capital basis, and choose a supplied diversified ETF, cash or short government bills, and/or no action. A candidate ETF can be entered with zero shares and a dated mark. No action keeps each actual selected holding and cash balance; retained stock outcomes remain unknown until stock-specific analysis is available.

Supply available dated fund exposure, costs and income; blanks mean unknown and explicit zero means known zero. Optional transaction cost and terminal tax amounts are in reporting currency with their date and source. The backend validates model judgments, calculates all three cases and exposes future paths separately from supplied facts. Known subtotals exclude unknown effects; fully specified values remain conditional, nominal outcomes. Five years is not a sell date, and long-term real-wealth aspirations are not forecast hurdles. See [docs/etf-cash-comparison.md](docs/etf-cash-comparison.md) for the contract, formulas and fixture review.

## Verify

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m mypy analyst
..\.venv\Scripts\python.exe -m ruff check .
cd ../frontend
npm run typecheck
npm test
npm run build
```

Browser tests use installed Microsoft Edge in headless mode and start dedicated Next.js and FastAPI test servers on ports 3100 and 8100. On another OS, set `TEST_PYTHON` to the virtualenv executable and change Playwright's browser channel to an installed browser. Every automated journey uses fake model/data providers, with real request handling and Python calculations. An HTTP transport fixture also exercises the production OpenAI SDK request shape. Tests prohibit external network connections and use dummy credentials only; no live model or market-data request occurs. Browser tests check rendered values, question correspondence, failure display, and actual browser-script absence of the dummy key.

Tests are at the two seams approved by the ticket: the public FastAPI request/result boundary and the whole browser input-to-result journey. Fixed scripted answers validate the integration and rendering, not investment reasoning quality. No live answer-quality assessment is claimed.


## US stock questions

Ticket 05 adds the US stock selector to the existing question form and returns dated SEC/issuer evidence, company operating-driver cases, exit sensitivity and portfolio-aware conditional actions. Company, fund, cash and no-action alternatives use the shared comparison pipeline. Configure backend-only `RESEARCH_REFERENCE_FILE` with independently reviewed primary excerpts; missing records stay unknown. This adapter does not claim live filing retrieval. Allocation amounts remain undetermined. See [docs/us-stock-analysis.md](docs/us-stock-analysis.md) for provenance, methods, formulas, setup and verification limits.
