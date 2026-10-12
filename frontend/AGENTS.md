<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# UI components: Arc Free first, then shadcn/ui

Arc Free (https://uiarc.dev) is the preferred component library for new or redesigned reusable UI in this app: buttons, inputs, dialogs, tables, charts, navigation, chat and every other reusable control. Official shadcn/ui components fill the gaps Arc Free leaves. Custom TFS-specific interfaces are composed from these components.

## Rules

1. Search Arc before writing any UI. Use the `arc` MCP server (`search_components`, `list_components`) or the `@uiarc` registry (`npx shadcn@latest search @uiarc -q "<intent>"`).
2. Read the component documentation before using it (`get_component`, or `https://uiarc.dev/components/<id>/markdown`). Follow its "when not to use" guidance and use documented props only.
3. Install the original component with the shadcn CLI from this directory: `npx shadcn@latest add @uiarc/<id>`. Files land in `components/arc/`. Do not edit, fork or restyle their internals.
4. When Arc Free has no suitable equivalent, use the official shadcn/ui component (`npx shadcn@latest add <id>`, default registry). Do not add any other component library (Radix themes, MUI, Chakra, Mantine, Ant Design, HeroUI, Headless UI kits and similar).
5. Compose custom TFS-specific interfaces (the desk, thread view, composer, financial memos) from Arc Free and shadcn/ui components. Keep these compositions outside `components/arc/` and `components/ui/`.
6. Maintain Arc's visual design system across both libraries: style shadcn/ui components and custom compositions with Arc's semantic tokens (`--background`, `--surface`, `--foreground`, `--text-secondary`, `--border`, `--accent`, `--success`, `--warning`, `--danger`, `--radius-*`, `--space-*`), Arc type, Arc motion tokens and the shared `:focus-visible` ring from `foundation.css`. A shadcn/ui component must not bring its own palette or Tailwind color classes.
7. Never copy or reconstruct Arc Pro components. If only a Pro item fits, use a shadcn/ui component or a custom composition of free components instead, and do not imitate the Pro item's source.
8. Load the Arc design skill before building or reviewing UI: `.claude/skills/arc/SKILL.md`, and run `.claude/skills/arc/checklist.md` before finishing. `.claude/skills/arc/INSTRUCTIONS.md` holds the always-on design rules (tokens, type, copy, motion, states, responsive widths).

## Setup state

- Registry: `components.json` maps `@uiarc` to `https://uiarc.dev/r/{name}.json`. Arc needs no Tailwind; components use CSS modules and CSS variables.
- MCP: the `arc` server is declared in the repository root `.mcp.json` (`https://uiarc.dev/api/mcp`). The first connection opens a browser sign-in. If a tool call fails with an auth error, ask the user to reconnect instead of guessing.
- Foundation: `components/arc/foundation.css` is imported once in `app/layout.tsx`, with `data-theme="dark"` on `<html>` and Geist and Inter loaded as `--font-geist` and `--font-inter`. The desk tokens in `app/(desk)/desk.css` are aliases of Arc's semantic tokens, so existing desk screens and Arc components share one palette, type and focus ring.
- Installed Arc Free components: `alert`, `badge`, `button`, `checkbox`, `json-viewer`, `skeleton`, `text-shimmer`.
- Path alias: `@/*` resolves to this directory (`tsconfig.json`).
- Existing desk components in `components/desk/` predate this rule. Leave their structure alone until a ticket redesigns them, then move that surface to Arc.
