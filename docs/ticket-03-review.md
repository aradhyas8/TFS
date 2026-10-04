# Ticket 03 implementation review

Reviewed the implementation against starting commit `7a46ba923686a92695300036d85acf8bb87a5b27`, the local ticket, and `specs/personal-investment-analyst.md`. The complete implementation was staged for review before committing on the existing branch. Two native Codex review agents independently checked Standards and Spec using the code-review skill.

## Standards

No documented-standard violations remain. A low-severity duplicated ETF-exposure predicate was consolidated into `has_etf_exposure`, shared by current checks, previews and final recommendation enforcement. Re-review found no new meaningful standards findings.

## Spec

No actionable spec findings remain. The initial review found model waiver language could survive in secondary recommendation fields. `enforce_guardrails` now replaces every rendered recommendation field for current breaches, blocked or unknown proposals, and relevant unknown configured current checks. API regression tests cover each prose field across current, blocked-preview and unknown-preview contexts; a browser test injects waiver language into both reason and downside and verifies it is absent from the completed display. Re-review independently repeated the original reproduction and confirmed it resolved.

Cross-account company aggregation, pre/post exposures, explicit active-budget classifications, partial baselines, deliberate excess cash, unknown settings, unusable valuation, indirect policy preservation and conditional above-cap reduction paths are implemented. Model previews cannot invent new cash or change settings and valuation inputs. Sizing remains ineligible; previews are hypothetical exposure checks with no orders, confirmed trades or justified allocation amounts. Sponsor holdings, research workflows and persistence remain in their later numbered tickets.

## Verification

- Full backend suite: **126 passed**; includes fake SDK HTTP transport and scripted model tool calls.
- Full browser suite: **17 passed**; real Next.js/FastAPI input-to-result journeys with fake providers.
- Backend mypy and Ruff: passed.
- Frontend TypeScript and production Next.js build: passed.
- Git whitespace checks: passed.
- Inspected the rendered guardrail screenshot at `frontend/artifacts/portfolio-guardrails.png` (generated artifact, ignored by Git).

Automated tests prohibit nonlocal network connections. No live OpenAI or financial-data services were called, and no completed live answer-quality assessment is claimed. One browser-suite startup encountered a transient existing Next.js development-server lock; the rerun completed with all tests passing. The existing FastAPI test-client deprecation warning remains nonfatal.

Final findings: **Standards 0; Spec 0**.
