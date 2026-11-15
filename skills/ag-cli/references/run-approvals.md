# run, approvals, audit

```sh
{{AG}} --dir <state> run -- git status          # logged to history.jsonl; `--` required if child takes flags
{{AG}} --dir <state> run [--cwd D] [--env K=V] [--timeout S] [--force] [--raw] -- make install
{{AG}} --dir <state> approvals | approve <id> | history [--limit 50]
```

Dangerous `run` blocks (never executes) until `approve` or re-run with `--force`. Multi-token commands are shell-quoted; old approvals re-prompt once; approval lives 10 min. Prefer native file tools; `read|write|edit|ls|grep` exist as small logged file ops. `notify` writes a macOS notification without stealing focus (`ui confirm|choose|ask` are dialogs; details: `references/secrets-ui.md`).

**rtk:** if `rtk` is on PATH, `run` rewrites the command to its token-compact form (result JSON has `rtk`). `run --raw` skips it for one call; `AG_RTK=0` disables globally. Agents on codex/gemini/cursor get a prompt hint to use `rtk`; claude/opencode use their own rtk hooks. `agents doctor` reports rtk coverage.
