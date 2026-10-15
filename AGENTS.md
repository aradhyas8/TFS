# Agent instructions

Frontend-specific rules are in `frontend/AGENTS.md`.

## Testing efficiency

- Run the tests that cover the files you changed. Do not run the full backend, frontend or Playwright suite by default.
- Expand testing with change risk. Run nearby regression tests when you change shared behavior.
- Do not repeat a passing test run unless a relevant file changed after it.
- Run Ruff and mypy on changed backend files, and TypeScript checks on changed frontend files.
- Use mocked HTTP for provider integration tests. Do not make paid or live model API calls unless the task requires them.
- Run broader validation for database migrations, financial calculations and release-critical changes.
- CI gives broad coverage on pull requests. Do not duplicate it locally without a reason.
