<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# UI components: Arc UI only

Arc UI (https://uiarc.dev) is the exclusive source for new or redesigned reusable UI components in this app: buttons, inputs, dialogs, tables, charts, navigation, chat and every other reusable control.

## Rules

1. Search Arc before writing any UI. Use the `arc` MCP server (`search_components`, `list_components`) or the `@uiarc` registry (`npx shadcn@latest search @uiarc -q "<intent>"`).
2. Read the component documentation before using it (`get_component`, or `https://uiarc.dev/components/<id>/markdown`). Follow its "when not to use" guidance and use documented props only.
3. Install the original component with the shadcn CLI from this directory: `npx shadcn@latest add @uiarc/<id>`. Files land in `components/arc/`. Do not edit, fork or restyle their internals.
4. Never recreate a component that Arc provides, and never add a competing component library (shadcn/ui default registry, Radix themes, MUI, Chakra, Mantine, Ant Design, HeroUI, Headless UI kits and similar).
5. Custom layout and TFS-specific compositions are allowed and may combine Arc components. Keep them outside `components/arc/`.
6. If a required component is unavailable in Arc, or is Pro-only and not unlocked, stop and report the gap. Do not silently substitute another library or hand-build a lookalike. Never reconstruct Pro source.
7. Load the Arc design skill before building or reviewing UI: `.claude/skills/arc/SKILL.md`, and run `.claude/skills/arc/checklist.md` before finishing. `.claude/skills/arc/INSTRUCTIONS.md` holds the always-on design rules (tokens, type, copy, motion, states, responsive widths).

## Setup state

- Registry: `components.json` maps `@uiarc` to `https://uiarc.dev/r/{name}.json`. Arc needs no Tailwind; components use CSS modules and CSS variables.
- MCP: the `arc` server is declared in the repository root `.mcp.json` (`https://uiarc.dev/api/mcp`). The first connection opens a browser sign-in. If a tool call fails with an auth error, ask the user to reconnect instead of guessing.
- Foundation: `components/arc/foundation.css` and `components/arc/lib/motion-tokens.ts` are installed. `foundation.css` is not imported yet, because its global `:focus-visible` rules use `!important` and would override the existing desk focus styling in `app/(desk)/desk.css`. The first ticket that renders an Arc component must import it once in `app/layout.tsx` (`import "@/components/arc/foundation.css";`), set `data-theme` on `<html>`, load Geist and Inter as `--font-geist` and `--font-inter`, and recheck the existing desk screens.
- Path alias: `@/*` resolves to this directory (`tsconfig.json`).
- Existing desk components in `components/desk/` predate this rule. Leave them alone until a ticket redesigns them, then move that surface to Arc.
