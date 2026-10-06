# UI Direction D — design system and New Cash workflow

Status: approved. New Cash is implemented at `/new-cash` (`frontend/app/new-cash/`, `frontend/components/desk/`). Other workflows still use the classic page at `/`.
Visual boards: https://claude.ai/artifact/A5CuE6vFYVvCN9k3bhofLr (page "Direction D · New cash").
Source of truth for problems: `.impeccable/critique/2026-10-06T04-38-30Z__frontend-app-page-tsx.md` (13/40).

Direction D = Research Desk structure + Quiet Ledger visual language.

## 1. Principles

1. **Answer first.** The center states what to do and why in one sentence and one paragraph. Everything else sits one layer below.
2. **Evidence one step away.** Every paragraph that rests on a source carries a citation number. Clicking it opens that source in the right panel. Never more than one click.
3. **Context is ambient.** Portfolio, rules and past decisions live in the left rail. Each answer names the snapshot it used.
4. **Honest states.** No simulated progress. Unknown values say "Unknown" and why. A conditional answer reads as conditional.
5. **Rules, not boxes.** Hairlines and spacing group content. A raised surface means selected, editable or quoted, and nothing else.
6. **One accent, two signals.** Brass marks the answer and anything you can act on. Sage means pass. Amber means caution, breach or "needs you".

Pane questions:

| Pane | Answers |
|---|---|
| Left rail | What portfolio and decision context am I working with? |
| Center memo | What should I do and why? |
| Right panel | Show me the evidence and details behind that answer. |

## 2. Layout (≥1280)

| Element | Size |
|---|---|
| Left rail | 248 fixed; collapses to a 64 icon rail |
| Center | fluid, min 560 |
| Memo column | max 720, including the 128 margin-label column and a 24 gap; side padding 48 |
| Context header | 56 tall: workflow, amount/destination, snapshot date, Save |
| Composer | pinned to the bottom of the center; 56 field plus 16/24 padding; same 720 column |
| Right panel | 400 default; min 360, max 440, resizable |
| Panel tab bar | 48 |
| Panel padding | 16 |

Each pane scrolls on its own. Once the recommendation scrolls out of view, it moves into the context header ("Add C$1,000–1,500 to MAPLE").

## 3. Typography

Families:
- **Instrument Sans** for all interface text.
- **Newsreader** for judgment moments only: the recommendation, page and sheet titles, the wordmark.
- Every number uses `font-variant-numeric: tabular-nums`. No monospace face.

| Role | Spec | Use |
|---|---|---|
| Display | Newsreader 300, 32/40, −0.01em (26/34 under 1024) | Recommendation sentence, empty-state prompt |
| Title | Newsreader 400, 26/32 | Page and sheet titles (Decisions, Portfolio) |
| Figure | Sans 500, 20/28, tnum | Key numbers: portfolio total, scenario values |
| Memo body | Sans 400, 15/24 | Center reading text, in text-2 |
| UI | Sans 400/500, 14/20 | Controls, panel text, lists |
| Meta | Sans 400, 12/16 | Dates, sources, helper text, in muted |
| Label | Sans 500, 11/16, uppercase, 0.1em | Margin labels and rail section labels only |

Minimum text size is 12, except the uppercase label at 11.

## 4. Color and surface

| Token | Hex | Use | On ground |
|---|---|---|---|
| `rail` | #0F0E0D | Left rail, right panel | — |
| `ground` | #131211 | Center memo | — |
| `raised` | #1A1917 | Selected item, inputs, quotes, composer | — |
| `raised-2` | #211F1C | Hover on raised | — |
| `rule` | #2A2825 | Hairlines | — |
| `rule-strong` | #3A3631 | Input edges, secondary button edge | — |
| `text` | #ECE8E1 | Primary text; primary button fill | 15.3:1 |
| `text-2` | #C9C3B9 | Memo body | 10.7:1 |
| `muted` | #9A948A | Meta, labels | 6.2:1 |
| `faint` | #6F6A62 | Disabled and icons only, **never text** | 3.5:1 |
| `brass` | #CDAE73 | Answer amount, citations, links, active tab, focus ring | 8.8:1 |
| `brass-hover` | #E2C88F | Link hover | — |
| `brass-dim` | #4A3F2C | Current-weight fill in impact bars | — |
| `sage` | #9DBFA3 | Pass / within rule | 9.3:1 |
| `amber` | #E0A35C | Caution, breach, needs input, stale | 8.5:1 |

Rules:
- **Unknown** is muted text plus a dashed rule. It never gets its own color.
- **Status** is always a word ("Within", "Over", "Unknown"), with color as a second cue.
- **Effects:** no gradients, glows or shadows. The one exception is the mobile bottom sheet.
- **Radius:** 6 for controls, 8 for raised blocks, 12 for sheets.
- **Focus:** a 2px brass ring with a 2px offset on every interactive element.

## 5. Spacing

The base unit is 4. Scale: 4, 8, 12, 16, 20, 24, 32, 48, 64.

| Value | Use |
|---|---|
| 4 | Icon to text, citation offset |
| 8 | Inside a list item; meta under a title |
| 12 | Between rail and panel list items |
| 16 | Pane inner padding |
| 20 | Memo row vertical padding |
| 24 | Label-to-body gap; section groups in the panel |
| 32 | Question to answer |
| 48 | Center side padding; between turns |
| 64 | Empty-state breathing room |

## 6. Component hierarchy

```
AppShell
├─ Rail
│  ├─ Wordmark + NewAnalysisButton
│  ├─ PortfolioSummary   (total, top weights, rules line; opens Holdings tab)
│  └─ DecisionList       (DecisionItem: title, date, status dot)
├─ Center
│  ├─ ContextHeader      (workflow · amount → destination · snapshot · Save / Saved)
│  ├─ Thread
│  │  ├─ QuestionEcho    (question + one-line input summary)
│  │  └─ Memo
│  │     ├─ MemoRow(label, body)         ← every section uses this
│  │     ├─ RecommendationBlock          (display sentence, brass amount, meta line)
│  │     ├─ ImpactBar                    (now · change · cap tick; within / near / over / unknown)
│  │     ├─ AlternativesList             (action + one-line reason)
│  │     ├─ InputRequest                 ("Needs you": labelled fields + Re-run)
│  │     ├─ WaitingBlock                 (elapsed timer, scope list, cancel)
│  │     ├─ DetailLinks                  (rows that open the panel or expand in place)
│  │     └─ SaveAndConfirm               (record what you did)
│  └─ Composer           (workflow prefix, input, ⌘↵; SlashMenu; NewCashForm)
└─ DetailPanel (tabs)
   ├─ EvidenceTab        (EvidenceItem: number, title, source/date, quote, facts, link; "Looked for, not found")
   ├─ ScenariosTab       (ScenarioCase × 3: value/share, vs price, drivers, outcome on amount)
   ├─ HoldingsTab        (grouped by account; per-row provenance on expand)
   └─ GuardrailsTab      (GuardrailLine: status word, value / limit, explanation)
```

## 7. Interaction rules

- **Starting a workflow.** Type `/` in the composer to open the slash menu (5 workflows; arrow keys, Enter, Esc). The chosen workflow shows as a plain brass prefix in the composer and is removed with Backspace. With no workflow chosen, the question runs as a portfolio review, which is today's backend behavior.
- **Required inputs are collected before sending,** in the composer's expanded form.
  - New cash needs an amount, a destination cash account, and a confirmation that the money is new. Analyze stays disabled until all three are set, and the button names what's missing.
  - Loss tolerance is optional, and the form says what leaving it out costs.
- **Inputs the backend says are missing** come back as an "Needs you" memo row with labelled fields. "Re-run with these" sends a new analysis.
- **Citations.** The backend links evidence to the recommendation (`evidence_ids`) and to facts (`document_ids`), not to individual sentences. Citation numbers therefore sit at the end of the paragraph they support. Clicking one opens the Evidence tab with that item focused and its excerpt shown.
- **Center versus panel.**
  - Reasoning lists (assumptions, uncertainty, what would change) expand in place in the center.
  - Numeric detail (scenarios, guardrails, holdings, sources) opens in the panel.
- **Follow-ups** start a new analysis with the same portfolio and workflow. The backend has no conversation memory, and the composer placeholder says so.
- **Saving.**
  - Save in the context header stores the decision. It then becomes "Saved 10:45".
  - A "Saved" memo row offers "What did you do?" (maps to the confirm endpoint: action + notes). "Later" is allowed.
  - Unconfirmed decisions get an amber dot in the rail.
- **Reopening a decision** shows it read-only: a historical band with the snapshot date, the question, the memo, and "You did". The composer is replaced by "Re-run with today's portfolio".
- **Keyboard.**
  - ⌘↵ sends; `/` opens workflows; Esc closes the menu or sheet.
  - Tabs are a real `tablist`. The panel is reachable from a skip link.

## 8. Responsive

| Width | Rail | Center | Panel |
|---|---|---|---|
| ≥1280 | 248 | fluid | 400 sibling |
| 1024–1279 | 248 (kept; the center still has ≥776) | fluid | 400 sheet over the center, opened by citations, tabs or the Details button |
| 768–1023 | Drawer from the header | full | Full-height sheet, max 480 |
| <768 | Drawer | single column, 16 gutters | Bottom sheet at 85% height |

Under 768:
- Margin labels move above their body.
- The composer sits after the memo instead of sticking to the bottom, so the expanded New Cash form never covers the answer.
- The missing-input reason moves above the input line.
- Touch targets are 44.

## 9. Loading, error and unknown

**Waiting.** The backend returns one completed result, so the page never shows section-by-section progress. What it shows:
- A real elapsed timer, and the range of the user's last three run durations (stored client-side). Before any history exists: "usually 1–3 minutes".
- A "This run checks" list. It describes the request's scope, built from the inputs, and is not a progress list. Nothing in it gets a check mark.
- A 1px indeterminate brass line under the context header. Reduced motion makes it a static half-opacity line.
- The tab title changes when the answer arrives.
- The Holdings tab and Decisions stay usable.
- **Stop waiting** drops the pending answer in the browser. The server run is not cancelled, and nothing is saved.
- **After 3 minutes:** "Taking longer than usual. It will keep going for up to 10 minutes."

**Error.** Shown as a memo row labelled "Not finished", in plain words, with the question and inputs kept. Actions: "Try again" and "Details" (backend detail and fields).

**Unknown or insufficient evidence** (`wait_for_inputs`, null amount, stale quote, unavailable documents):
- The display sentence states it ("Not enough to size this yet.") and shows no brass amount.
- A "Needs you" row asks for exactly what's missing.
- The impact bar is dashed with an "Unknown" label.
- The Evidence tab lists "Looked for, not found".

**Stale data.** The rail header turns amber with the reason ("MAPLE price stale").

## Rail weights (decided)

- Before any analysis: holdings and share counts only, with "Values and weights appear after the first analysis."
- After an analysis: the backend-calculated values and weights, labelled "Weights · <date> analysis".
- Reopened decisions: the rail keeps today's portfolio. Saved decisions don't store weights, so none are shown as historical; if they're stored later, label them "Historical weights · <date>".
- Sample SYNTH stays above its cap; the Guardrails tab shows "Over, before this cash" with `/rebalance`.

## 10. New Cash sequence (boards on the canvas)

| # | Board | Shows |
|---|---|---|
| 1 | Shell | Returning user; rail with portfolio and decisions; prompt; workflow starters; Holdings tab |
| 2 | Start | Slash menu open over a dimmed center; cash available in the panel |
| 3 | Inputs | Expanded composer: amount, destination, optional loss tolerance, required confirmation; Analyze disabled with its reason; panel previews where the cash lands |
| 4 | Waiting | Honest wait as described in section 9 |
| 5 | Answer | Recommendation, why (cited), portfolio impact, alternatives, downside, detail links; Evidence tab with item 2 focused |
| 6 | Scenarios | Downside/assumptions/uncertainty/what-would-change expanded in place; Scenarios tab with three cases, drivers and outcome on C$1,500 |
| 7 | Unknown | `wait_for_inputs`: no amount, "Needs you" (stale price + loss tolerance), dashed impact, evidence gap |
| 8 | Saved | Saved state, record-what-you-did form; Guardrails tab (within, over before this cash, unknown indirect, target mix not set) |
| 9 | Reopen | Historical band, read-only memo, "You did", saved evidence, re-run |

Field mapping:
- `recommendation.preferred_action` + `amount` drive the display sentence.
- `reason` drives "Why".
- `evidence_ids` produce the citations.
- `allocation.judgment` produces the impact range.
- `recommendation.alternatives` produce Alternatives.
- `downside` / `assumptions` / `uncertainty` / `what_could_change` produce the reasoning rows.
- `allocation.stocks[].cases` produce Scenarios.
- `previews[].guardrails` produce the Guardrails tab.
- `missing_inputs` produce "Needs you".
- `research.documents[].available === false` produces "Looked for, not found".

## 11. Extending to the other workflows

Every workflow reuses the same shell, memo rows, panel tabs and states. Only the row set and the default tab change.

| Workflow | Recommendation sentence | Center rows | Default panel tab |
|---|---|---|---|
| Stock analysis | "Hold MAPLE; worth C$46 in the base case." | Why · Valuation method · Downside · Detail | Scenarios |
| Portfolio review | "Review only: SYNTH is above your cap." | Exposure (direct and through funds) · Concentration · Overlap | Holdings, extended with fund overlap |
| Rebalance / re-underwrite | One sentence across all holdings | One row per holding: changed / unchanged / unknown, action, thesis delta | Guardrails, with proposals as previews |
| Theme discovery | "Supports 1 of 3; size within 2–4%." | Mechanism · per-candidate verdict (supports / challenges / unknown) | Evidence |
| Saved decisions | — | The rail list plus a full Decisions page: a question-first register with filters (Not confirmed, workflow) and the same reopen view | — |

- **Theme discovery:** the "awaiting agreement" status becomes a "Needs you" row with the shortlist.
- **Proposals and previews** become a "Scenarios" or "Guardrails" comparison: before and after, with no new panels.
- **Fixed for every workflow:** numbered evidence, the honest wait, unknown states and save/confirm.
