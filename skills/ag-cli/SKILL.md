---
name: ag-cli
description: Drives the `ag` CLI itself (state dir, non-blocking PTY sessions, logged runs and approvals, secret prompts, notes/todos, models, setup, update, schedule tick). Use when running `ag spawn/snap/send/wait/kill`, `ag run`, `ag approve`, sudo/ssh passphrase/OTP prompts, `ag models`, `ag setup`, `ag update`, `schedule install`, or when a command creates a stray .agent/ dir or a session seems to hang. For delegating to other agents see ag-agents.
---

# ag CLI (scope: {{SCOPE}})

Single-file Python CLI, stdlib only, POSIX. Binary: `{{AG}}`. Delegation to other agents: skill `ag-agents`.

**One invocation per shell call.** No `;`/`&&` chains, no shell var for the path: chaining resets cwd and later calls hit the wrong `.agent/`. All commands take `--json`.

## State dir (read first)

Resolution, no upward search: `--dir` -> `$AGENT_CLI_DIR` -> `$PWD/.agent`. Wrong cwd = fresh empty roster (looks wiped, isn't). **Always pass `--dir`.** Tool calls are fresh shells, so `export` does not persist. `--dir` (and `--json`) also work after the subcommand (`{{AG}} agents rm w --dir <state>`); after `agents add|set` `--dir` means the agent workdir (state dir not supported there). Never commit `<state>/`.

```sh
{{AG}} --dir <state> agents list
```

## Never from agent context

Need a real TTY, hang the tool call: `tui`, `attach`, `shell`, `handoff --exec`. Want an interactive program? `spawn` + `snap`. Tell the user to run TUI commands.

## Routing

Run `{{AG}} route` for the effective job -> model table of the calling harness; `{{AG}} route JOB` for one job. Change it with `ag setup`, never by hand.

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

## Secrets: never see them

ag pops a native masked macOS dialog and types the value into the program; the model never sees it. Never request secrets in chat or put them in argv. Details: `references/secrets-ui.md`.

```sh
{{AG}} --dir <state> spawn -- sudo make install   # prompt detected -> dialog -> typed into PTY
{{AG}} --dir <state> send <id> --secret           # value from dialog; never logged
{{AG}} --dir <state> ui notify "build done"       # silent; `ui confirm|choose|ask` for dialogs
```

## Other commands

```sh
{{AG}} --dir <state> note "..."                   # also: notes, todo add|list|done|clear, history, events
{{AG}} models [--backend B] [--rank] [--free] [--zdr] [--tier small|balanced|big]
{{AG}} setup --scope project --yes --dry-run      # agent context: flags + --yes; ask the user to run it bare
{{AG}} update --status                            # auto-updates daily; AG_AUTO_UPDATE=0 in scripts/tests
{{AG}} selfcheck                                  # also: agents doctor (backends + rtk), keys (TUI cheat-sheet)
```

## References (load only when needed)

- `references/secrets-ui.md` - secret prompts, askpass, `ui` dialogs, `notify` inbox.
- `references/keys-providers.md` - Keychain `keys`, `providers`, `api` backend, ollama/lmstudio.
- `references/models-privacy.md` - `ag models` flags/ranking/openrouter, data-privacy rule + zdr/retain/train table.
- `references/setup-update.md` - `ag setup` flags and UIs, `ag update`, `ag push`.
- `references/schedule-relay-tick.md` - global schedule tick install, relay tick/admin, `ratelimit`.
- `references/access-budget-link.md` - `access`, `usage`, `budget`, VM -> Mac `link`.

Deeper: `README.md`, `docs/INSTALL.md` in the ag repo.
