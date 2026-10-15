# TFS — Personal Investment Analyst

## What is TFS?

TFS is a local investment research application that you run on your own computer. You ask questions in a chat, and an AI (artificial intelligence) analyst uses Python tools to research stocks and analyze your portfolio. TFS does not place trades with a broker.

## Features

- **Investment research:** TFS reads dated SEC (US Securities and Exchange Commission) filings, issuer reports and cited web results.
- **Portfolio analysis:** TFS calculates value, cash, company exposure, currency exposure and ETF (exchange-traded fund) overlap.
- **Stock valuation:** TFS calculates five-year company cases from reported facts and explained assumptions.
- **Scenarios:** TFS compares a stock with an ETF, cash and no action on the same capital.
- **AI conversations:** TFS keeps chat threads, tool results and sources on your computer.
- **Saved decisions:** You can save an analysis result as a decision and read it later.
- **Confirmed portfolio changes:** Imports and edits make a proposal. Your portfolio changes only after you select **Confirm changes**.

## How TFS Works

| Part | Function |
| --- | --- |
| Frontend | Next.js (React) in `frontend/`. It shows the chat, portfolio and results at `http://localhost:3000`. |
| Backend | FastAPI (Python) in `backend/analyst/`. It runs on `http://127.0.0.1:8000`. |
| AI orchestration | The backend sends your question to the AI model. The model can call only the tools in the backend registry. |
| Financial data | yfinance and EODHD supply delayed quotes. SEC EDGAR, OpenFIGI and EODHD identify securities. The Bank of Canada supplies exchange rates for CAD (Canadian dollars). |
| Calculations | Python calculates all numbers with `Decimal`, pandas and NumPy. The model does not calculate values. Missing inputs stay "unknown". |
| Persistence | SQLite stores portfolio revisions, proposals, threads and runs in `backend/data/portfolio/portfolio.db`. |

The browser never receives API keys or ChatGPT tokens. Both servers listen only on your computer (loopback).

## Requirements

- **Operating system:** Windows 10 or 11 (native, not WSL), or macOS.
- **Python:** 3.11 or later.
- **Node.js:** 20.9 or later, with npm.
- **Git.**
- **AI access (optional):** an eligible ChatGPT account or an OpenAI API (application programming interface) key. You can view and edit a portfolio without AI access.

ChatGPT sign-in encrypts tokens with Windows DPAPI (Data Protection API) or stores them in the macOS login Keychain. Linux is not supported. On Linux, ChatGPT sign-in is not available, and startup is not tested.

## Quick Start

Do steps 1 to 4 one time.

1. Get the code. Use the `ticket-02-to-09-actions-and-persistence` branch. The `main` branch does not have `npm run dev` yet.

   ```sh
   git clone --branch ticket-02-to-09-actions-and-persistence https://github.com/aradhyas8/TFS.git
   cd TFS
   ```

2. Install the backend.

   On Windows (PowerShell):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
   Copy-Item backend/.env.example backend/.env
   ```

   On macOS (Terminal):

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -e './backend[dev]'
   cp backend/.env.example backend/.env
   ```

3. Do not commit `backend/.env`. It holds your configuration and keys.
4. Install the frontend:

   ```sh
   npm --prefix frontend ci
   ```

5. Start TFS from the repository root:

   ```sh
   npm run dev
   ```

6. Wait for the message `TFS ready at http://localhost:3000`.
7. Open `http://localhost:3000` in your browser.
8. To stop TFS, press **Ctrl+C** in the terminal. This stops the backend and the frontend.

To start TFS again, do steps 5 to 7. Your portfolio and sign-in stay saved.

If startup fails, read the `[dev]` message in the terminal:

| Message | Action |
| --- | --- |
| `Missing Python virtual environment` | Do step 2 from the repository root. |
| `Missing frontend dependencies` | Do step 4. |
| `Port 8000` or `Port 3000` `is unavailable` | Stop the other program that uses the port. TFS does not stop other programs. |
| `Backend dependency/configuration check failed` | Do step 2 again. Then do step 5. |

## Connect ChatGPT

TFS can use your ChatGPT plan for AI requests. This sign-in connects the AI provider. It is not a TFS account.

1. Select **Continue with ChatGPT** at the top of the page.
2. Sign in to ChatGPT in the browser window that opens.
3. Go back to the TFS tab.
4. Open **AI connection**.
5. Select a model in **ChatGPT model**. The list shows only the models that your account can use.
6. Select **Use ChatGPT plan**.

If no models show, select **Refresh connection**. If the error says HTTP 520, see the known upstream issue under **Limitations**. If the connection expires, select **Reconnect ChatGPT**. To remove the saved sign-in, select **Sign out of ChatGPT**.

**API-key alternative:** If you cannot use ChatGPT sign-in, use an OpenAI API key.

1. Stop TFS.
2. In `backend/.env`, set `LLM_PROVIDER=openai`.
3. Set `OPENAI_API_KEY` to your key.
4. Set `OPENAI_MODEL` to a Responses API model that your account can use.
5. Start TFS with `npm run dev`.
6. If **AI connection** shows **Use API-key provider**, select it.

`LLM_PROVIDER=openrouter` with `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` is also available for development. Any OpenRouter model that supports tools, tool choice and structured outputs works (for example `nvidia/nemotron-3-super-120b-a12b:free`). TFS never falls back to another model or host, caps `:free` models at zero price, and does not retry rate-limited requests. Hosted web search is unavailable on OpenRouter, so answers that need current web research say so. Free models are slow (60 to 140 seconds per large request) and may not finish full stock-analysis, review, theme or allocation workflows within the 600-second run limit; use ChatGPT sign-in or a stronger model for those. Rate-limit, unavailable-model, unsupported-request and timeout failures are reported with their cause. Keep all keys in `backend/.env` only. Do not put a key in a `NEXT_PUBLIC_*` variable. API billing is separate from your ChatGPT plan.

**Limitations:**

- ChatGPT sign-in works only on native Windows and macOS.
- On macOS, the Keychain can ask you to allow access. Select **Always Allow**.
- TFS keeps one saved ChatGPT account.
- Model availability and usage limits depend on your ChatGPT plan.
- You cannot change the provider while an AI request or sign-in is in progress.
- The automated tests use a mocked sign-in. No test has yet used a real ChatGPT account.
- **Known upstream issue (October 2026):** With a real ChatGPT sign-in, OpenAI's `api.openai.com` returns a Cloudflare HTTP 520 for both the model catalog (`GET /v1/models`) and inference (`POST /v1/responses`). TFS shows "OpenAI's model catalog is unavailable (HTTP 520)". Requests without a valid token still get a normal HTTP 401. That means the fault is OpenAI's handling of ChatGPT sign-in tokens, not TFS or your network. Other apps report the same error ([OpenAI community thread](https://community.openai.com/t/persistent-http-520-cloudflare-on-post-v1-responses-with-sign-in-with-chatgpt-oauth-tokens/1406366)). Signing in again does not help. Until OpenAI fixes it:
  1. Use the API-key alternative above for AI questions.
  2. Select **Refresh connection** later. Your login stays saved, so the model list loads without a new sign-in once OpenAI fixes the issue.

## First Use

**Ask a question.** Type a question in the chat box, for example: "What is the difference between market price and intrinsic value?" Send the question.

**Import holdings:**

1. On the **Start with what you own** screen, select **Template** to download a CSV (comma-separated values) file.
2. Add your accounts, tickers and shares to the file.
3. Set the holdings date and the reporting currency.
4. Load the file. TFS shows the changes.
5. Select **Confirm changes**. TFS saves a new portfolio revision.
6. If TFS cannot identify a holding, open **Portfolio**. Supply the missing exchange or type.
7. Select **Confirm changes** again.

**Analyze an investment.** Select a workflow: **Analyze a stock**, **Place new cash**, **Rebalance** or **Explore a theme**. Complete the form. Send the form.

**Confirm changes.** If you ask TFS to change a holding, TFS shows a proposal. Review the proposal. Then select **Confirm changes** or cancel. The AI model cannot confirm a proposal.

## Available Tools and Workflows

The AI model can call only these backend tools. The registry is in `backend/analyst/orchestrator.py`.

| Group | Tools | Function |
| --- | --- | --- |
| Context | `get_holdings`, `get_artifact`, `get_proposal`, `get_messages` | Read saved holdings, results, proposals and earlier messages. |
| Financial data | `review_portfolio`, `get_financial_data`, `resolve_candidate`, `refresh_quotes`, `check_proposed_changes` | Calculate exposure, read dated prices and FX (foreign exchange), and check hypothetical trades. |
| Company research | `get_sec_filings`, `get_sedar_filings`, `get_issuer_material`, `get_company_research` | Read dated primary evidence for one US or Canadian stock. |
| Valuation and comparison | `calculate_company_cases`, `calculate_comparison`, `scan_opportunities` | Calculate company cases, compare alternatives and read the opportunity screen. |
| Workflows | `run_exposure_review`, `run_stock_analysis`, `run_new_cash_allocation`, `run_portfolio_reunderwriting`, `run_theme_exploration` | Run a full workflow. Each workflow needs its form inputs. |
| Web | `web_search` | Search the public web through the AI provider. Each result shows its source. TFS treats results as untrusted. |
| Proposals | `prepare_portfolio_proposal` | Prepare a portfolio edit that you stated. Only you can confirm it. |

No tool places trades, moves money or commits portfolio changes.

## Testing and Documentation

Run these commands from the repository root:

```powershell
npm run test:startup
cd backend
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m mypy analyst
..\.venv\Scripts\python.exe -m ruff check .
cd ..\frontend
npm run typecheck
npm test
npm run build
```

On macOS, use `../.venv/bin/python` in place of `..\.venv\Scripts\python.exe`. `npm run test:startup` needs free ports 8000 and 3000. `npm test` uses Microsoft Edge and ports 3100 and 8100. The tests use fake AI and market data.

Detailed documentation:

- [Technical reference: CSV format, calculations and persistence](docs/technical-reference.md)
- [Startup and ChatGPT connection](docs/dev-chatgpt/README.md)
- [Financial data sources](docs/financial-sources.md)
- [US stock analysis](docs/us-stock-analysis.md) and [Canadian stock evidence](docs/canadian-stock-evidence.md)
- [ETF, cash and no-action comparison](docs/etf-cash-comparison.md)
- [New cash allocation](docs/new-cash-allocation.md)
- [Portfolio re-underwriting](docs/portfolio-reunderwriting.md)
- [Theme discovery](docs/theme-discovery.md)
- [ETF indirect overlap](docs/etf-indirect-overlap.md)
