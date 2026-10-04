# Dated financial sources (ticket 02)

All workflows still use `POST /api/analyze` and the shared Python decision pipeline. The model can call `resolve_identities`, `get_quotes`, `get_fx` and `review_portfolio`. All tools take an empty object; the backend binds the submitted positions and reporting currency. The first source access refreshes a request-local evidence set once; subsequent tools and calculations reuse that evidence. The loop is limited to six model turns. There is no continuous refresh or order execution.

## Source qualification checked 2026-10-04

The [yfinance documentation](https://ranaroussi.github.io/yfinance/) identifies personal-use limits and directs users to Yahoo's terms. A library license is not a data license. This review did not establish permission for this application's automated access and reuse or reliable coverage for every submitted listing. Therefore yfinance remains a disabled candidate: no dependency or automatic Yahoo request is installed. A labeled user-entered broker-display mark is the price fallback. These marks never establish independent identity verification or confident sizing.

The [Bank of Canada Valet guide](https://www.bankofcanada.ca/valet-api-how-to/) supports daily FX series without an API key. The [daily rates](https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates/) express foreign currency in Canadian dollars. The [terms](https://www.bankofcanada.ca/terms/) require attribution, accuracy and respect for request limits; the rates are indicative analytical references, not execution benchmarks. This personal application preserves attribution and discloses inverse conversion when applicable. No Bank logo or endorsement is used. This qualification does not authorize unrelated third-party content or commercial resale.

Set `BOC_FX_ENABLED=true` in **backend/.env** to fetch one daily observation per needed CAD pair on demand. USD to CAD uses `FXUSDCAD`; CAD to USD uses its reciprocal, rounded to ten decimal places and labeled as an inverse. Other CAD series follow the same convention. Unsupported pairs, unavailable observations, weekends or source failures remain missing when no explicitly supplied fallback exists. No earlier day or cross rate is silently selected. It defaults off, so the application remains usable with manually supplied FX. The test environment injects mock HTTP responses; it never queries the Bank.

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
