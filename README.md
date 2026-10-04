# Personal Investment Analyst

A standalone Next.js and FastAPI app for ticket 01: enter a dated whole-portfolio snapshot, ask a question, and receive calculated holdings, cash, weights, direct-company exposure and a qualified review. All code is new.

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

Set an as-of date and reporting currency, add accounts, then holdings or cash rows. For direct stocks, use a stable **company ID shared across all accounts and listings** and a company name. A ticker plus exchange identifies the supplied listing. ETFs need a ticker and listing; indirect company exposure stays unknown. Identities are user supplied, never presented as independently verified.

Supply shares, an unadjusted mark, its actual date and a source label. Cash uses a balance and currency. For foreign-currency positions, supply a direct FX pair into the reporting currency, its rate, actual date and source. Rates are reporting-currency units per one local-currency unit. Marks and FX must match the snapshot date to support valuation; older, future or missing data stays unknown. No cross rates, inverse rates or live refresh are inferred.

Download the CSV header template in the app. A worked fictional example is [examples/portfolio.csv](examples/portfolio.csv), dated **2026-09-30** in **CAD**. Its expected totals are CAD 3,600 portfolio value, CAD 2,300 stock value, CAD 1,300 cash, and CAD 2,300 direct Acme exposure (63.89%). It contains two accounts, two listings and two cash balances. This is a calculation fixture, not an investment suggestion.

The exact CSV header is:

```csv
row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source
```

Use `stock`, `etf`, `cash`, `fx`, or `account` row types. An `account` row preserves an empty account. Position rows carry a unique position `id` and consistent `account_id,account_name`; FX rows use `currency,to_currency,fx_rate,fx_date,fx_source`. Fields irrelevant to the row type must be empty. Import replaces the editor snapshot after successful validation; malformed input leaves the current snapshot intact. Files are limited to 1 MB, accounts to 100, positions to 2,000 and FX pairs to 100. This milestone accepts nonnegative shares and cash, and rejects shorts or negative balances explicitly.

## Shared decision boundary

`POST /api/analyze` accepts `{question, portfolio}` and returns `{status, question, portfolio, recommendation}`. `/docs` on FastAPI exposes the complete typed contract. `POST /api/portfolio/csv` accepts `{csv, as_of, reporting_currency}` and returns the same snapshot shape accepted by analysis.

The backend validates the request, binds the snapshot from the data provider, asks the model to call `review_portfolio`, dispatches the allowlisted Python tool, returns its computed result to the model, validates the final recommendation and preserves the deterministic result in the response. The tool has no arguments: a model cannot replace the submitted holdings or supply financial values. The loop is bounded to three turns. Production uses one backend-owned OpenAI Responses integration, without extra orchestration frameworks or a database. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) document the integration pattern.

Python uses Decimal arithmetic, pandas grouping across accounts, and NumPy completeness checks. Values are serialized as decimal strings; weights are fractions rounded to eight decimal places. The browser only formats these fields. No adjusted-price returns, dividends, splits, costs or tax effects are inferred from a snapshot.

Missing valuation makes the complete total and all portfolio weights `null`. A separately labeled known subtotal remains available. Missing baseline and guardrails remain `null`. Recommendations at this milestone are limited to review, clarification or no action, and amount must be `null`. Unknown tools, invalid arguments, extra output fields, unsupported numeric prose, trade direction, refusals and incomplete answers fail without a completed recommendation. Qualitative claims still require separate human judgment; schema and arithmetic checks do not establish reasoning quality.

Later tickets extend `analyst.pipeline.analyze`, the provider boundaries and the request/result contract. Live data, numeric guardrails, evidence research, scenarios, ETF look-through and saved decisions remain deferred to their numbered tickets.

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
