---
name: ag-cli
description: Drives the `ag` CLI itself (state dir, PTY sessions spawn/snap/send/wait/kill, logged run + approvals, secret dialogs, notes/todos, models/routing/setup/update, event watching). Load to run or troubleshoot `ag` commands, for sudo/ssh passphrase/OTP prompts, to watch events or wait on a session, or when a command creates a stray `.agent/` dir. For delegating work to other agents use ag-agents.
---

# ag CLI (scope: {{SCOPE}})

Single-file Python CLI, stdlib only, POSIX. Binary: `{{AG}}`. Delegating to other agents: skill `ag-agents`.

**One invocation per shell call.** No `;`/`&&` chains, no shell var for the path: chaining resets cwd and later calls hit the wrong `.agent/`. All commands take `--json`.

## Quick reference

```sh
{{AG}} --dir <state> spawn [--name N] [--cwd D] [--env K=V] -- bash    # pty session; returns instantly
{{AG}} --dir <state> snap <id> [--clean] [--since <offset>]            # output; offset from prior snap
{{AG}} --dir <state> send <id> "cmd" [--no-enter] [--key ctrl-c|esc]   # type into session
{{AG}} --dir <state> wait <id> --timeout 30                            # only blocking cmd; always bound it
{{AG}} --dir <state> kill <id> [--force]                               # also: sessions, status
{{AG}} --dir <state> events --follow [--exit-on-match] [--agent A] [--type wake_done,wake_fail] [--timeout S]
{{AG}} --dir <state> run [--cwd D] [--force] -- git status             # logged to history.jsonl
{{AG}} --dir <state> approvals                                         # approve <id> clears a blocked run; history --limit 50
{{AG}} --dir <state> note "..." [--tag T]                              # also: todo add|list|done|clear
{{AG}} models [--free] [--rank] [--zdr] [--tier small|balanced|big]
{{AG}} route [JOB] [--check --json]                                    # effective model table; change via ag setup
{{AG}} setup --scope project --yes --dry-run                           # ask the user to run it bare
{{AG}} update --status                                                 # auto daily; AG_AUTO_UPDATE=0 in scripts
```

## Pitfalls

- State dir: `--dir` -> `$AGENT_CLI_DIR` -> `$PWD/.agent`, no upward search. Wrong cwd = fresh empty roster (looks wiped, isn't). Tool calls are fresh shells: `export` does not persist. Always pass `--dir` explicitly.
- zsh does not word-split `$VAR`: `D="--dir /x"; {{AG}} $D ...` passes ONE arg. Write the flag and path out (or use a bash array).
- After `agents add|set`, `--dir` means the agent workdir (there is no `--cwd` there; `spawn --cwd` is the session dir). State dir goes before the subcommand.
- Secrets: never ask the user to paste them. `spawn -- sudo ...` and `send <id> --secret` pop a native masked dialog and type the value; the model never sees it. Never in argv.
- Never `tui`/`attach`/`shell`/`handoff --exec` (need a TTY, hang the tool call): `spawn` + `snap` instead.
- Prefer `events --follow --exit-on-match` over shell polling loops; `--timeout S` exits 124 when nothing matches.

## References (load only when needed)

- `references/pty-sessions.md` - snap offsets, stale daemon sessions, `forget`, `spawn --wake`, events details.
- `references/run-approvals.md` - dangerous-run blocking, approval lifetime, rtk compaction, small file ops.
- `references/secrets-ui.md` - secret prompts, askpass, `ui` dialogs, `notify` inbox.
- `references/keys-providers.md` - Keychain `keys`, `providers`, `api` backend, ollama/lmstudio.
- `references/models-privacy.md` - `ag models` flags/ranking/openrouter, data-privacy rule + zdr/retain/train table.
- `references/setup-update.md` - `ag setup` flags and UIs, `ag update`, `ag push`.
- `references/schedule-relay-tick.md` - global schedule tick install, relay tick/admin, `ratelimit`.
- `references/access-budget-link.md` - `access`, `usage`, `budget`, VM -> Mac `link`.

Deeper: `README.md`, `docs/INSTALL.md` in the ag repo.
