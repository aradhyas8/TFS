# Ticket 01 implementation review

Reviewed against `.scratch/personal-investment-analyst/issues/01-greenfield-app-and-dated-portfolio-review.md` and `specs/personal-investment-analyst.md`. The initial local repository baseline is `42dcd68`, containing the specification and tickets. The application is entirely new. Git index writes are prohibited by the active workspace permissions, so the reviewers inspected all new implementation files directly instead of a committed diff.

The implement skill's code-review workflow ran independent native Codex Standards and Spec agents. They reviewed fixes and reported no remaining actionable findings in those fixes. No external agent runtime or live model/data service was used.

## Standards

- **P1, fixed — Proxy timeout:** Next.js's default timeout could terminate a valid multi-turn review. The server proxy now permits 150 seconds, matching the browser request budget and covering the bounded backend loop. A browser regression completes a real application request with a 31-second fake model turn.
- **P2, fixed — Financial display precision:** Converting authoritative Decimal strings to JavaScript Number could corrupt large values. The UI now groups decimal digits directly without numeric coercion or rounding. A browser regression verifies every digit of an independently expected large value.

## Spec

- **P1, fixed — Unsupported sizing in prose:** The structured amount was null, but an invented target such as “Increase Acme to half your portfolio” could pass validation. Backend validation now rejects quantitative sizing and proposed execution direction, with request-boundary regressions.
- **P2, fixed — Explicitly declining rebalancing:** A valid “do not rebalance” qualification was rejected by a blanket word blacklist. Validation now permits the explicitly declined verb while still rejecting positive direction elsewhere in the answer.
- **P2, fixed — Security classification correction:** Changing stock to ETF erased shared supplied fields. Classification changes now preserve ticker, listing, shares and dated marks. A browser regression checks preserved data and recalculated direct-company exposure.
- **P2, fixed — Descriptive downside:** Risk explanations such as “Company-specific losses can reduce portfolio value” were rejected as proposed transactions. Validation now distinguishes proposed adjustment language from descriptive risk, with boundary regressions for downside and concentration explanations.

Standards: 2 findings fixed; worst initial issue P1 proxy timeout. Spec: 4 findings fixed; worst initial issue P1 invented sizing. These checks establish request integrity and calculation/display behavior; qualitative investment reasoning still requires separate human assessment.

## Verification

- 41 backend request-boundary checks pass, including actual production OpenAI SDK calls intercepted by an in-memory HTTP transport. External network connections are blocked in tests.
- 9 Playwright checks pass through dedicated real Next.js/FastAPI test servers, fake model/data boundaries and real Python portfolio calculations.
- Strict Python and TypeScript typechecks, Ruff, and the Next.js production build pass.
- Dummy backend credentials are absent from returned payloads, frontend source and loaded browser scripts.
- A captured browser review was visually inspected. Supplied dates/sources, account values, weights, direct exposure and qualifications are visible.

Production configuration is intentionally unset. See `README.md` to configure backend-only credentials and run the application. No live OpenAI or market-data call, live answer-quality evaluation, remote deployment or order execution occurred.

Local `.git` metadata has read-only access and the runtime rejects permission escalation. Remote publication can use the native GitHub connector to commit the verified files on `main` independently of the protected local Git metadata. Updating local branches or remotes requires Git write access.
