# AGENTS.md — repo guidance for coding agents

Single-file Python CLI (`ag`, stdlib only, no install) for agent orchestration. POSIX only.

## Setup

```sh
git clone https://github.com/Segzman/ag.git && cd ag && chmod +x ag
./ag selfcheck && ./ag agents doctor && ./ag agents list
```

State: `./.agent/` in cwd, overridden by `--dir` / `$AGENT_CLI_DIR`. **Always pass `--dir` explicitly** — running from another cwd silently starts a fresh roster. Never commit state (`./.agent/`, `approvals.json`, `history.jsonl`, `notes.jsonl`, `todos.json`).

Skills: templates in `skills/ag-cli` (CLI/PTY/secrets/setup) and `skills/ag-agents` (routing, delegation); `ag setup` renders them into harness skill dirs. Details: `README.md`, `docs/AGENT_CONTEXT.md` (scoped context), `custom.md` (scope chain).

## Rules

- Absolute path to this clone (`<repo>/ag`), one invocation per shell call. No `;`/`&&` chains around it.
- Never `tui` / `attach` / `shell` / `handoff --exec` from agent context (needs TTY). `spawn` + `snap` instead.
- Delegate via `ag` (`chat send`, `delegate`, `wake`, `spawn --wake`, `context assign/show/checkpoint`). Same agent queues FIFO (steer first); different agents parallelize. `wake` is at-most-once launch — verify via `events` + `wakes`.
- Harness profiles share skills/MCP/memory via scoped files under `.agent/profiles_effective/` — never write home/global config.
- Every feature commit updates README.md and the relevant skills/ template in the same commit; tests/test_ag_doc_coverage.py enforces command coverage.
- Surgical changes only; match existing style. Verify with `./ag selfcheck` and `python3 tests/<relevant>.py`.
