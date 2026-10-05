# Canadian-Stock Evidence Support

## Overview

Ticket 10 extends the established stock-assessment path to Canadian issuers using permitted primary evidence:
- **SEDAR+ primary filings:** Provided as exact, user-opened verification links (`https://www.sedarplus.ca/...`).
- **Permitted issuer material:** Investor-relations releases, presentations, and webcast remarks with verifiable dates.
- **Single shared architecture:** Preserves the Next.js frontend, Python/FastAPI backend, deterministic financial arithmetic, and guardrails outside the model.

## Supported Canadian Listings & Securities

Securities are classified according to explicit market listing MICs and currency:
- **Supported Canadian Listings:** `XTSE` (Toronto Stock Exchange), `XTSX` (TSX Venture Exchange), `NEOE` (Cboe Canada / NEO), `XCNQ` (Canadian Securities Exchange).
- **Listing Currency:** `CAD`.
- **Classification Rules:**
  - `is_canadian_security(currency, listing)`: Returns `True` if currency is `CAD` or listing is in `CANADIAN_LISTINGS`.
  - `is_supported_stock(row)`: Recognizes valid US stocks (`XNAS`, `XNYS`, `XASE` in `USD`) and Canadian stocks (`XTSE`, `XTSX`, `NEOE`, `XCNQ` in `CAD`).

## Primary Evidence Boundary & SEDAR+ Tooling

### Tool Dispatch
- **Canadian Issuers:** Orchestration provides `get_sedar_filings` and `get_issuer_material`.
- **US Issuers:** Orchestration provides `get_sec_filings` and `get_issuer_material`.
- **Mismatched Tool Enforcement:** If a model attempts to call `get_sec_filings` on a Canadian stock or `get_sedar_filings` on a US stock, the backend rejects the call immediately (`HTTP 502`) to ensure geographic evidence integrity.

### Exact User-Opened Verification Links
- SEDAR+ documents must carry authority `sedar` or `sedar_plus` and specify an exact HTTPS URL on `www.sedarplus.ca` or `sedarplus.ca` (e.g., `https://www.sedarplus.ca/csa-party/records/document.html?id=...`).
- Non-HTTPS URLs, wrong domains, or bare root URLs (`https://www.sedarplus.ca/`) fail strict Pydantic validation.
- In the frontend, SEDAR+ filings display a distinct `SEDAR+ verification link` badge and a clear instruction directing users to open the exact link to confirm primary disclosure directly.

### Strict Non-Goals & Boundaries
- **No Automated Scraping:** SEDAR+ documents are never automatically scraped.
- **No SEDAR+ Database:** No database of SEDAR+ records or automated indexing platform is maintained or implied.
- **No Live Retrieval:** Fake model and fake data boundaries are strictly preserved during automated verification; no live network calls are made.

## Deterministic Calculations & Fact Verification

- **Reported Facts:**
  - Canadian company facts must be denominated in `CAD` (matching the position currency) and Diluted Shares.
  - Facts require all four verification checks: `filing_checked`, `notes_checked`, `custom_tags_checked`, and `segments_checked`.
  - Facts must reference an available primary filing (`sedar` or `sedar_plus`).
- **Gaps & Conflicts:**
  - Missing facts, contradictory disclosures, or unavailable SEDAR+ documents leave per-share calculations unknown (`terminal_price: null`).
  - Missing decisive primary evidence automatically converts any recommendation into conditional direction (`wait_for_inputs`) with undetermined amounts.
- **Valuation Scenarios:**
  - Calculated outside the model in Python with 60-decimal precision.
  - Three disciplined operating cases: downside, base, and upside across five annual periods.
  - Reverse valuation computes the required exit multiple to recover current unadjusted price without inventing market beliefs.

## Guardrails & Prose Validation

The backend inspects all generated prose (`validate_prose`) for prohibited claims:
1. **No Automated Scraping Claims:** Phrases referencing automated SEDAR+ scrapers or SEDAR+ scraping engines trigger `InvalidReview` (HTTP 502).
2. **No SEDAR+ Database Claims:** Claims that an internal SEDAR+ database was queried trigger `InvalidReview` (HTTP 502).
3. **No Fabricated Tax Claims:** Assertions of guaranteed tax-free gains or account-based tax exemptions (e.g., "tax-free under TFSA capital gains exemption" or "guaranteed 0% tax") trigger `InvalidReview` (HTTP 502).
4. **No Claim of Unreviewed Transcript Q&A:** Referencing transcript Q&A when `qa_available` is false triggers `InvalidReview` (HTTP 502).

## Cross-Workflow Availability

Canadian stock support is seamlessly accessible across the application workflows:
- **Stock Assessment:** Direct stock analysis dropdown includes Canadian listings (`XTSE`, `XTSX`, `NEOE`, `XCNQ`).
- **New Cash Allocation (`new_cash`):** `scan_opportunities` and `research_candidate` support Canadian discovery candidates alongside US equities.
- **Portfolio Review Re-Underwriting (`portfolio_review`):** Held Canadian stocks are re-underwritten through `reunderwrite_holding` against current SEDAR+ evidence.
- **Theme Exploration (`theme`):** Shortlisted Canadian stocks are evaluated through `test_theme_mechanism` and sized using the shared Python engine.
