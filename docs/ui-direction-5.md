# UI Direction 5: Oat and Ink

The selected visual direction for TFS. It replaces Direction D (`ui-direction-d.md`) as the design reference.
The mockups this was chosen from are the "TFS Visual Directions" page, direction 5. Where this file and a mockup
disagree, the mockup wins, except for the accessibility changes listed at the end.

## Idea

A calm consumer-investing look for a chat-first product. Very little on screen, set large. Ink does the acting.
Color is kept for meaning.

- Ink: text and every primary action (send, confirm, new conversation).
- Green: good standing only. A rule within its limit, a changed value, the live portfolio. Never a button.
- Blue: analysis and evidence. Citations, source focus, price markers.
- Amber: needs you, or over a rule.
- Red: real downside, and a failed or interrupted run.
- A status is a word first. Color is the second cue.

## Tokens

Values live in `frontend/app/theme.css`, loaded after Arc's `foundation.css`.

| Role | Token | Value |
| --- | --- | --- |
| Page | `--background` | `#FDFCFA` |
| Rail | `--rail-surface` | `#F5F2EC` |
| Card | `--surface` | `#F6F3EE` |
| Lifted (composer, menus, inputs) | `--surface-raised` | `#FFFFFF` |
| Raised and hover | `--surface-muted` | `#ECE8E0` |
| Border | `--border` | `#E2DDD3` |
| Ink | `--foreground`, `--accent` | `#26241F` |
| Secondary text | `--text-secondary` | `#57534B` |
| Muted text | `--text-muted` | `#6B655C` |
| Green | `--success`, text `--success-strong`, fill `--success-subtle` | `#1F7A54`, `#17623F`, `#DCEBE1` |
| Blue | `--analysis`, fill `--analysis-subtle` | `#3565A8`, `#E1E9F4` |
| Amber | `--warning`, text `--warning-strong`, fill `--warning-subtle` | `#9C6410`, `#8A5609`, `#F5E8CD` |
| Red | `--danger`, fill `--danger-subtle` | `#B4412F`, `#F5DFD9` |

## Type

- Hanken Grotesk 400, 500 and 600 for the interface and all numbers. Numbers are tabular.
- Newsreader 500 for the greeting (46, 38 on a tablet, 30 on a phone) and the verdict of an analysis. Nowhere else.
- Body 15 on 24 in a conversation, 14 on 20 in chrome, 12 for captions.

## Surfaces and shapes

- Cards are a slightly darker oat on the near-white page, with no outline and no shadow.
- Only things that float are white with a soft shadow: the composer, menus, the review dialog.
- Buttons, rail rows, chips and pills are fully round. Cards use radius 18 to 20. The composer uses 28.
- Spacing is on a 4 pixel base: 12 in rows and chips, 14 to 20 in cards, 24 between page regions.

## Layout

- Rail 252 wide: new conversation, Portfolio, Decisions, conversations, saved decisions, and what the portfolio is.
- One conversation column, 720 wide.
- Details are closed at every width until something asks for them: a citation, a result tab, Portfolio in the rail,
  or the Details button. Nothing remembers them open across a reload.
- On screens 1280 and wider open details are a third column. Below 1280 they slide over the page. Below 1024 the rail
  becomes a drawer. Below 768 the details are a bottom sheet.
- Below 1024 everything a finger has to hit is at least 44px.
- Home: greeting, composer, the five workflows as starters, one summary strip. Centered. On a phone it is top aligned
  and the starters become a list.

## Phases

1. Foundation and main workspace: tokens, type, rail, home, composer, responsive shell. Done.
2. Conversation and analysis results. Done.
3. Portfolio and decisions.
4. Proposal review and confirmation.

Until a surface reaches its phase it keeps its structure and takes only the new tokens.

## Conversation (phase 2)

- The question is a warm bubble on the right. The answer is plain ink text, 15 on 24, with no bubble.
- What the analyst did is one line above its result: a green check, or amber when it paused for inputs, or red when a
  step failed, then each step separated by a dot.
- A stored result is an oat card. Its header names the result and the company, the portfolio revision it was computed
  from, and the listing and currency. Its foot has Open details and Save decision. The question is not repeated inside.
- The verdict is the serif at 26 (24 on a phone). A stock result draws the downside, base and upside values per share
  on one line, with today's price as a blue marker when it is in the same currency. Below the line the three values
  are written out. A line on the company cap follows: amber when over it, green when within. Nothing is drawn when the
  backend did not return the values. When the verdict makes no call (not enough to decide, or held back by another
  rule), the line is grey and says "No call yet", and a within-cap line is grey and says it is a rules check only.
- Citation numbers are small blue pills. In an analysis result they open the source in the details. In a web answer
  they sit after the words they support and link to the source below the answer. A number is placed only where the
  citation's stored offsets address those words in the shown text; a citation without usable offsets is listed under
  the answer and never placed by searching for its words. On touch layouts each number is a 44px target.
- Web pages carry a "Web · not verified" chip wherever they are shown. Filings and issuer material in the details are
  labeled by kind (SEC filing, SEDAR+ filing, Issuer material) and are never mixed with web pages.
- The details have a header that names what they belong to and its portfolio revision, then ink-underlined tabs.
  Sources are oat cards. The one a citation points at is ringed in blue. Sources looked for and not found sit in a
  dashed box.
- The composer is a text area that grows with what is typed, up to eight lines. Enter sends, Shift+Enter starts a new
  line. Slash commands, the workflow forms and their keys are unchanged.
- The header shows the conversation's first question as its title.

## Where the app differs from the mockup on purpose

- Muted text is `#6B655C`, not `#756F66`, and amber and green text use the `-strong` values. The mockup values fall
  below 4.5:1 on the oat surfaces.
- The send button is dimmed while there is nothing to send.
- The case values are written under the line, not on it, because cases can sit close together and their labels
  would overlap.
- The details have no Result tab. The result is in the conversation, and its tabs open the details.
- A result card keeps every row the result had before: position, thesis, filings, business quality, valuation, cases,
  portfolio impact, sizing, risks, alternatives and notes. The mockup shows only the summary.
- Step lines name the steps the backend stored. The mockup's "read SEC filings, got quotes and FX" wording is not
  invented where no such step was recorded.
- Some sizes come straight from the mockup and sit outside the 15, 14 and 12 scale and the 4 pixel grid: 13.5 in
  tabs, source titles and the cap line, 12.5 in step lines, 11 in source numbers, and the bubble's 10 by 16 padding.
  Result and source cards carry their icon in a tinted tile, as the mockup does, which Arc's own rules avoid.
- The case line uses a soft red to blue to green fill under the cases. It carries meaning (downside to upside), so it is
  kept although Arc reserves gradients for marketing.
- The summary strip shows holdings and accounts, the rules as set, and what is waiting. A portfolio value and a count of
  companies over the cap need a valuation, which exists only after an analysis.

## Accessibility

- Escape closes the rail drawer, then the details.
- Below 1024 the rail drawer, and below 1280 the details, cover the page. While one is open, the rest of the desk is
  `inert` (no pointer, keyboard or screen reader), and Tab and Shift+Tab cycle inside it.
- On 1280 and wider the open details are a normal column: nothing is inert and focus moves freely.
- Opening the details from the Details button, or while they cover the page, moves focus to the selected tab.
- Closing them returns focus to the control that opened them (a citation, a result tab, Open details, the Details
  button), or to the Details button when that control is gone.
- Covered by `tests/details_panel.spec.ts` and `tests/conversation_phase2.spec.ts` at 390, 768 and 1440.
