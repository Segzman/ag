# ag-tui.command

Double-click macOS launcher for this repo's TUI. No install, no copies.

- Resolves its repo root from its own path (spaces-safe), runs `ag --dir "$SDIR" tui` via `exec`.
- State dir: `$1` when given, else `${AGENT_CLI_DIR:-$HOME/.agent}`.
- QA: `scripts/ag-tui.command /private/tmp/ag-qa-state` (never daily `~/.agent`).
- Errors: missing/non-executable `ag`, more than one argument, or `-flag`-like state dir all exit non-zero with one-line usage.
