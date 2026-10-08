# Dated financial sources (ticket 02)

All workflows still use `POST /api/analyze` and the shared Python decision pipeline. The model can call `resolve_identities`, `get_quotes`, `get_fx` and `review_portfolio`. All tools take an empty object; the backend binds the submitted positions and reporting currency. The first source access refreshes a request-local evidence set once; subsequent tools and calculations reuse that evidence. The loop is limited to six model turns. There is no continuous refresh or order execution.

## Source qualification checked 2026-10-04

The [yfinance documentation](https://ranaroussi.github.io/yfinance/) identifies personal-use limits and directs users to Yahoo's terms. A library license is not a data license. This review did not establish permission for this application's automated access and reuse or reliable coverage for every submitted listing. Therefore yfinance remains a disabled candidate: no dependency or automatic Yahoo request is installed. A labeled user-entered broker-display mark is the price fallback. These marks never establish independent identity verification or confident sizing.

The [Bank of Canada Valet guide](https://www.bankofcanada.ca/valet-api-how-to/) supports daily FX series without an API key. The [daily rates](https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates/) express foreign currency in Canadian dollars. The [terms](https://www.bankofcanada.ca/terms/) require attribution, accuracy and respect for request limits; the rates are indicative analytical references, not execution benchmarks. This personal application preserves attribution and discloses inverse conversion when applicable. No Bank logo or endorsement is used. This qualification does not authorize unrelated third-party content or commercial resale.

Set `BOC_FX_ENABLED=true` in **backend/.env** to fetch one daily observation per needed CAD pair on demand. USD to CAD uses `FXUSDCAD`; CAD to USD uses its reciprocal, rounded to ten decimal places and labeled as an inverse. Other CAD series follow the same convention. Unsupported pairs, unavailable observations or source failures remain missing when no explicitly supplied fallback exists. No cross rate is selected. Superseded 2026-10-06: it now defaults on and uses the latest published day within seven days, labeled with that date (see below). The test environment injects mock HTTP responses; it never queries the Bank.

## Independently reviewed reference data

An optional backend-only `FINANCIAL_REFERENCE_FILE` absolute path loads a `FinancialEvidence` JSON object. This is the small personal-app adapter for independently checked exchange/issuer listing references and permitted dated price extracts. It is not an upload endpoint. The operator must check the actual cited issuer/exchange record and price-source terms before entering this file; a model or submitted portfolio cannot declare itself independently verified.

The fictional [examples/financial-reference.json](../examples/financial-reference.json) demonstrates its schema. Its example.test URLs and invented prices are test examples, not real coverage or verification; do not configure it as real portfolio evidence. The complete contract is also available in FastAPI `/docs`.

Identity records are keyed by submitted position ID and retain status, ticker, exchange, kind, company ID/name, currency, source URL, source date and capture time. Verified records require dated source provenance and matching supplied identity. Ambiguous/conflicting records stay unresolved. Corrections require updating the submitted identity and reviewed reference; the model cannot guess the listing.

Provider quotes must match ticker, listing and currency. Nonmanual quotes require source-specific terms URL, dated qualification, permitted personal use, covered listing, capture time and a price basis. Missing coverage or permission invokes the broker-mark fallback when supplied. A contradictory identity, currency, stale quote or adjusted-price basis is not silently replaced by a convenient mark. The source status remains visible. Cached and manual inputs can support a provisional dated valuation but do not establish usable sizing inputs.

## Dates and calculation basis

The existing same-date rule remains explicit: quote and FX dates must equal the requested snapshot date. No elapsed-time freshness cutoff is invented. Age is displayed relative to the snapshot date, capture date and actual request date, with the review timestamp retained. A valued historical snapshot is not a current price. Unknown capture times stay unknown. Future or contradictory capture times make the input unusable.

`source_inputs_usable` describes verified identity and usable dated source inputs only. `sizing_eligible` remains false for the whole review because personal caps, budgets and risk context are still unknown. Later guardrails must check both source limitations and personal inputs. Recommendations continue to have `amount: null`.

Values use snapshot-date shares and **unadjusted** same-date quotes, plus directed dated FX. Shares must already reflect splits on that date; no split factor is applied again. Split-adjusted, dividend/total-return-adjusted and unknown price bases are rejected. No dividends are added to prices or credited to the supplied cash balance. No historical return is inferred from one snapshot. This prevents a price adjustment and separately counted dividend or split from double counting the same benefit.

The existing CSV schema stays compatible. Loaded marks and FX are manual; capture time stays unknown and the existing mark column means an unadjusted broker-display mark. The editor can add actual capture timestamps with timezone or identify an unusable adjusted/unknown price basis before submission.

## Automatic portfolio enrichment and the daily quote cache (2026-10-07)

A holdings CSV needs only account, ticker and shares; average cost, currency and type are optional. Market data is refreshed separately from analysis, within the EODHD free plan (20 requests a day).

- **Identity, once.** Import and Identify resolve each holding and save it in the portfolio. SEC's `company_tickers_exchange.json` (free; `SEC_USER_AGENT` must name a contact email) identifies US stocks with their exact exchange and CIK. EODHD search (`EODHD_API_KEY`, one request) is used only for Canadian listings and US securities SEC does not list, such as ETFs. A TSX issuer is verified when its EODHD name matches a unique SEC registrant legal name (CM.TO matches CIBC, CIK 1045520); otherwise it stays supplied, identified by ISIN. Several matches ask for the exchange; a missing type or exchange is the only question. Reviews re-check identity against SEC only and never call EODHD for it.
- **Quotes, once a day.** `POST /api/market/refresh` fetches EODHD real-time (delayed about 15-20 minutes) quotes for the saved holdings in one batched request (EODHD counts one request per symbol) and writes them to `backend/data/market/quotes.json` (`MARKET_DATA_DIR`). A holding already fetched today (New York date) is skipped. The desk calls it on load and after import; **Refresh prices** forces a new fetch. Before fetching, the free `/user` endpoint gives the requests left today; only that many holdings are fetched, the rest wait. A refused request (402/403/429) keeps the cache and says so.
- **Analysis reads the cache only.** Portfolio Review, Stock Analysis and New Cash use the cached quote with its source, currency, trade date, capture time and status. Today's fetch is `delayed`; an earlier day's is `cached`. Neither is called live. A holding with no cached price stays unknown ("could not be priced from the cache; use Refresh prices").
- **FX.** Bank of Canada Valet is on by default (`BOC_FX_ENABLED=true`). It uses the latest published day within seven days of the snapshot date and keeps that date.

Provider prices and FX may predate the snapshot date by up to seven days (`RECENT_DAYS`) for valuation, shown with their own dates. Manual marks and manual FX still must be on the snapshot date. Only same-day, non-cached provider inputs count as `source_inputs_usable` for sizing. The desk values a saved portfolio without manual marks or FX as of today.

Valuation, account values, weights, direct-company and currency exposure need no personal rules. Missing values are reported once per cause, naming the holdings. API keys are sent only in requests and never stored in sources, issues, results or the cache.

Financial Modeling Prep was evaluated on 2026-10-06/07 and removed: the available key returned 402 for quotes of most holdings and for TSX profiles.
