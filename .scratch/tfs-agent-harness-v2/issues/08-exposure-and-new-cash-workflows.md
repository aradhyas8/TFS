# 08: Migrate exposure review and new-cash allocation

**What to build:** Existing review and new-cash desk/conversation requests complete through V2 with unchanged investment results, clarification and decision-saving behavior.

**Blocked by:** 07 — Interruption and proposal safety.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Exposure and allocation execute by direct structured dispatch or existing conversational workflow tools using the same validated inputs.
- [ ] Migrate only the model-driven orchestration; retain screening, calculation, benchmark/rule binding, sizing validation and artifact contracts.
- [ ] Preserve dated/undated evidence, empty rules, benchmark fund treatment, missing loss-tolerance clarification and explicit submission of new cash.
- [ ] All inference/retries use V2 resource accounting; no migrated workflow retains a hidden medium-effort ceiling or independent normal-completion turn budget.
- [ ] Existing result panels, follow-up retrieval, decision saving, cancellation and original-engine rollback work.
- [ ] Record financial/evidence parity against the existing audit fixtures and disclose any intentional behavioral difference before default cutover.

## Verification

Run focused exposure/allocation/workflow tests and parity fixtures. Exercise existing review/new-cash browser flows when affected, including clarification and follow-up. Run Ruff/mypy and TypeScript for changed frontend code.
