# Ticket 06 review

Fixed point: `9d9e0b7fc46f1763a6bd5fe4729d3e671f9a4747` (start of implementation). Native Codex reviewers inspected `git diff 9d9e0b7fc46f1763a6bd5fe4729d3e671f9a4747...HEAD`, then the working-tree fixes. The originating issue is `.scratch/personal-investment-analyst/issues/06-new-cash-allocation-with-bounded-discovery.md`; its authority is `specs/personal-investment-analyst.md`.

## Standards

No verified hard violations of documented repository standards. Two concrete P2 findings were resolved:

- Discovery provenance was overwritten. The original source capture is now preserved separately from request scan time, shown in the frontend, and qualified as dated screening evidence.
- Valid citations from both serious companies could be rejected by the selected-company validator. Candidate document IDs are now distinct, all citations are checked against bound available evidence, and selected-company SEC/issuer sufficiency is checked separately. Original source URLs and fact references are preserved.

Two baseline smells, both judgment calls, were resolved: duplicate company-judgment prose checks now use one helper; the candidate research dictionary now uses the `CompanyResearch` domain type instead of `Any`.

Follow-up read-only review found all four findings resolved and no remaining concern in the fixes.

## Spec

One P2 finding was resolved: original discovery evidence capture time had been lost, despite the ticket's requirements for a fresh bounded scan and dated evidence. The result now preserves source capture and request timestamps separately, and explicitly states that rereading signals does not make them newly published evidence.

No additional substantiated gap was found in research bounds, sizing arithmetic, FX denominator, configured-limit enforcement or frontend journey. A targeted probe confirmed that an unavailable purchase quote on an unheld ETF does not itself invalidate conditional ETF return-path cases; the selected sizing destination still requires usable evidence.

Follow-up read-only review confirmed the provenance fix and a further regression fix: both recommendation and supplementary allocation amounts are cleared when final selected-company evidence validation blocks an addition.

Review totals: Standards had two P2 correctness/documentation findings and two judgment-call smells, all resolved; Spec had one P2 provenance finding, resolved. No unresolved finding on either axis. The highest original severity on each axis was P2.

Final verification: 222 backend tests and 23 browser journeys passed, along with backend/frontend typechecks, lint and Next.js production build. Automated tests used fake providers only.
