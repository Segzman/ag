---
name: ag-cli
description: Use when driving the `ag` CLI itself (not delegating to agents) — state dir/--dir rules, non-blocking PTY sessions (spawn/snap/send/wait/kill), logged `run` + approvals, secret prompts (sudo/ssh passphrase/OTP), notes/todos, `ag models`, `ag setup`, `ag update`, the global schedule tick (`schedule install`), or when a command creates a stray .agent/ dir or a session seems to hang.
---

# ag CLI (scope: {{SCOPE}})

Single-file Python CLI, stdlib only, POSIX. Binary: `{{AG}}`. Delegation to other agents: skill `ag-agents`.

**One invocation per shell call.** No `;`/`&&` chains, no shell var for the path: chaining resets cwd and later calls hit the wrong `.agent/`. All commands take `--json`.

## State dir — read first

Resolution, no upward search: `--dir` -> `$AGENT_CLI_DIR` -> `$PWD/.agent`. Wrong cwd = fresh empty roster (looks wiped, isn't). **Always pass `--dir`.** Tool calls are fresh shells, so `export` does not persist.

```sh
{{AG}} --dir <state> agents list
```

Never commit `<state>/`.

## Never from agent context

Need a real TTY, hang the tool call: `tui`, `attach`, `shell`, `handoff --exec`. Want an interactive program? `spawn` + `snap`. Tell the user to run TUI commands.

## PTY sessions (non-blocking)

```sh
{{AG}} --dir <state> spawn -- bash
{{AG}} --dir <state> snap <id> --clean            # many readers OK; --since <offset> incremental
{{AG}} --dir <state> send <id> "ls"               # Enter appended; --no-enter; --key ctrl-c|ctrl-d|esc|tab|enter|up|down|left|right
{{AG}} --dir <state> wait <id> --timeout 30       # only blocking cmd; always bound it
{{AG}} --dir <state> kill [--force] <id>
{{AG}} --dir <state> sessions | status | events --limit 20
```

`spawn` returns instantly; poll with `snap` (offset comes from prior output). `spawn --name N --cwd D --env K=V --on-exit CMD`. Daemon `kill -9` -> session `stale` (`exit=-1`); its pending wake fires once on next reconcile (`sessions`/`snap`/`wait`/`kill`). `forget <id>` wipes logs (auth traces). `spawn --wake` = follow-up when session ends: see `ag-agents`.

## run, approvals, audit

```sh
{{AG}} --dir <state> run -- git status           # logged to history.jsonl; `--` required if child takes flags
{{AG}} --dir <state> approvals
{{AG}} --dir <state> approve <id>                # dangerous cmd blocked; approval lives 10 min
{{AG}} --dir <state> history
```

Dangerous `run` blocks (never executes) until `approve` or re-run with `--force`. Multi-token commands are shell-quoted; old approvals re-prompt once. Prefer native file tools; use `run` for the audit trail. `read|write|edit|ls|grep` exist as small file ops.

**rtk:** if `rtk` is on PATH, `run` rewrites the command to its token-compact form (result JSON has `rtk`). `run --raw` skips it for one call; `AG_RTK=0` disables globally. Agents on codex/gemini/cursor get a prompt hint to use `rtk`; claude/opencode use their own rtk hooks. `agents doctor` reports rtk coverage.

## Secrets (passwords, passphrases, OTP)

ag pops a native macOS dialog (masked) and hands the value to the program; the model never sees it.

```sh
{{AG}} --dir <state> spawn -- sudo make install   # prompt detected -> dialog -> typed into PTY
{{AG}} --dir <state> send <id> --secret           # value from dialog; never logged
{{AG}} --dir <state> spawn --no-secret-popup -- ...   # per session; AG_SECRET_POPUP=0 globally
```

Backend turns and spawned sessions get `SUDO_ASKPASS`/`SSH_ASKPASS`/`GIT_ASKPASS` -> `<state>/bin/ag-askpass` (+ a `sudo` shim adding `-A`). `ag askpass` only answers when its parent is sudo/ssh/git; never call it yourself. Secret never hits disk: `input.log` records `[secret input redacted]`, events log outcome only. Never request secrets in chat or put them in argv. Cancel in dialog = nothing sent. macOS only; elsewhere detection is off. A program that echoes its own input can still leak to `output.log`.

## Notes / todos

`ag note "..."` / `ag notes` · `ag todo add|list|done|clear` · `ag history` · `ag events`.

## Update

ag auto-checks GitHub once a day in a detached process; applies on next run. Git checkout: `fetch` + `merge --ff-only`, only if clean, tracking, not ahead. Copy install: download `master`, compile check, atomic swap.

```sh
{{AG}} update                  # run now
{{AG}} update --status         # state
{{AG}} update --auto off|on    # toggle (~/.config/ag/config.json)
```

`AG_AUTO_UPDATE=0` disables per shell (use it in scripts/tests). Log `~/.cache/ag/update.log`.

## Models

```sh
{{AG}} models [--backend B] [--refresh] [--rank] [--sort intel|coding|speed|popular] [--tier small|balanced|big] [--free] [--auto on|off] [--status] [--json]
```

Live list per backend (opencode, codex, cursor discovered; claude/gemini static aliases). Cache `~/.cache/ag/models.json`, TTL 24h, auto-refresh when stale; `--refresh` forces. A failing source keeps its last good list and records `error`. Use it to pick valid model ids before `agents add --model`. `--rank` shows Artificial Analysis scores (intelligence/coding/agentic) + tok/s + latency from openrouter.ai/rankings, plus ctx, price/`free`, release date, `new`/`fast` badges. Tier is derived from the intelligence score (big >= 40, balanced 20-40, small < 20); `tier~` = no score, guessed from the name. Sorted by intelligence per backend; `--sort intel|coding|speed|popular`; `--tier T`/`--free` filter; `--json` has every field + `sources` fetch times. Source: "scores: Artificial Analysis via openrouter.ai/rankings". `AG_AA_KEY` adds the official AA API as an override. Stale caches refresh in a detached background run (never blocks); `{{AG}} models --auto on|off` toggles it (also the daily update's catalog refresh), `--status` shows last fetch per source. Small-fast vs big-smart: `models --backend opencode --free --tier big`, `--sort speed`. `{{AG}} route [JOB] [--json]` shows effective job routing (see `ag-agents`).

## Setup

```sh
{{AG}} setup [--scope global|project] [--harness claude,opencode,codex|all] [--preset cost-first|balanced|quality-first] [--set JOB=claude:ALIAS] [--set JOB=ag:BACKEND/MODEL] [--set backend.B.enabled=true|false] [--set backend.B.model=ID] [--set backend.B.mode=ro|edits|auto|full|unset] [--set backend.B.plan=true|false] [--claude-md|--no-claude-md] [--yes] [--dry-run] [--ui web|cli|plain]
```

Ask the user to run it bare (interactive). Agent context: pass flags + `--yes` (or `--dry-run` first). `--ui` / `AG_SETUP_UI`: `web` (alias `mac`; auto on a local macOS desktop) = local browser form on 127.0.0.1 (random port + one-time token URL; `AG_SETUP_NO_OPEN=1` prints the URL instead of opening; Jobs per profile with grouped model dropdowns + capability panel, Backends, ranked Models, Skills; Save/Cancel, 15 min idle exits writing nothing), `cli` = curses (jobs table + Backends table, needs 60x23), `plain` = prompts. Writes `routing.json` (jobs + per-backend defaults that feed `agents add`), renders `ag-cli`/`ag-agents` skills into harness skill dirs, routing block in `~/.claude/CLAUDE.md` (claude/global). Atomic, never deletes. Job routed to a disabled backend: error, no save.

## Schedule tick (mechanics)

Scheduled wakes live in `ag-agents`. One global launchd job drives all state dirs:

```sh
{{AG}} schedule install                 # print launchd plist (label org.ag.schedule)
{{AG}} schedule install --write         # write ~/Library/LaunchAgents + bootstrap
{{AG}} schedule install --cron          # print crontab line instead
{{AG}} schedule tick --all --reap       # what the job runs every 60s; --dry-run, --now ISO
```

Registry `$AG_CONFIG_HOME/schedule-dirs.json`. Concurrent ticks are lock-safe; corrupt `schedules.json` refuses the tick.

## Sanity

`{{AG}} selfcheck` (regression checks, own temp dir) · `{{AG}} keys` (TUI cheat-sheet) · `{{AG}} agents doctor` (backends + rtk).

Deeper: `README.md`, `docs/INSTALL.md` in the ag repo.
