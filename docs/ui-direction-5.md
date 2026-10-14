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
- Details beside the conversation on screens 1280 and wider. They can be closed there, and the choice is remembered.
- Below 1280 the details slide over the page. Below 1024 the rail becomes a drawer. Below 768 the details are a bottom sheet.
- Home: greeting, composer, the five workflows as starters, one summary strip. Centered. On a phone it is top aligned
  and the starters become a list.

## Phases

1. Foundation and main workspace: tokens, type, rail, home, composer, responsive shell. Done.
2. Conversation and analysis results.
3. Portfolio and decisions.
4. Proposal review and confirmation.

Until a surface reaches its phase it keeps its structure and takes only the new tokens.

## Where the app differs from the mockup on purpose

- Muted text is `#6B655C`, not `#756F66`, and amber and green text use the `-strong` values. The mockup values fall
  below 4.5:1 on the oat surfaces.
- The send button is dimmed while there is nothing to send.
- The summary strip shows holdings and accounts, the rules as set, and what is waiting. A portfolio value and a count of
  companies over the cap need a valuation, which exists only after an analysis.
