---
name: using-ag
description: Use when driving the `ag` agent-cli — orchestrating sub-agents across claude/opencode/gemini/codex/cursor backends, running long or interactive programs in non-blocking PTY sessions (spawn/snap/send/kill), background wakes, harness profiles, scoped context/compact, or when a command creates a stray .agent/ state dir or an ag session appears to hang.
---

# Using ag

Single-file Python CLI, stdlib only. Lives at `<repo>/ag` (clone root). No install, POSIX only.

**Invoke via absolute path to your clone: `<repo>/ag`. One invocation per shell call** — no `;`/`&&` chains, no shell var for the path. Chaining resets cwd mid-chain and later commands silently hit the wrong `.agent/`.

Same file serves Claude + Opencode. Invoke via Bash in either.

## State dir — read first

Strict resolution, no upward search: `--dir` → `$AGENT_CLI_DIR` → `$PWD/.agent`.

Running from the wrong cwd silently creates a fresh `.agent/` (default roster `claude`/`oc`, empty sessions/logs). Looks wiped, isn't.

**Always pass `--dir`.** Example:

```sh
<repo>/ag --dir <state> agents list
export AGENT_CLI_DIR=<state>   # once per session, alternative to --dir
```

Confirm which roster the task means before writing. Never commit `<state>/` contents.

## Never from agent context

Needs a real TTY, hangs/garbles a tool call: `tui`, `attach`, `shell`, `handoff --exec`.

Want an interactive program? `spawn` it, then `snap` it. Tell the user to run TUI commands themselves.

## Model policy

Per-agent backend/model: see `ag agents list`. New agents: `ag agents add <name> --backend opencode --role sub` (add `--model <id>` to pin). Explicit custom models always win; `default` means "no `-m` flag" (except ag-level OpenCode default, see README). Never switch backends/models unless asked.

Per-task depth via roles, not model switches: `planner` = think hard (assumptions, verifiable steps, ask when ambiguous); `implementer` = execute minimal diffs; `reviewer` = terse bug/security check. Per turn: `ag agents set <name> --persona planner` persists, or `--persona` / `--system @file` overrides one call.

## Delegation

| Need | Command |
|---|---|
| Health / roster | `ag agents doctor` · `ag agents list` · `ag agents add w --backend echo --role sub` |
| Roles | `ag roles list` (system-prompt presets; `--persona` / `--system @file` overrides per turn) · `ag roles reset reviewer` restores one builtin |
| Harness | `ag harness use oc codex` · `ag harness current oc` (headless backend select; details: `docs/HARNESS_ROLES.md`) |
| One headless turn | `ag chat send oc "task"` · read back with `ag chat log oc` |
| Orchestrator assign | `ag delegate oc "task"` (logs into orchestrator chat) |
| Background turn | `ag wake oc "task"` (returns now) · `ag wakes` · `ag events --limit 20` |
| Opencode native cmd | `ag chat send oc --command review "path"` (opencode-only; others reject) |
| Slash (local, never to model) | `ag chat send oc "/model"`, `/harness use opencode`, `/profile use docs`, `/help` |

`chat send` / `delegate` take `--timeout` (default 1800s, `0` = unlimited): hard backend budget starting after the per-agent guard is acquired — lock wait is unbounded, then the turn gets the full budget. `wake` is at-most-once *launch*, no retry — verify via `events` + `wakes`. `wake --max-runtime` is the same hard budget for background turns; `wake --timeout` only marks `stalled`, worker keeps running. `wakes --cancel <job>` cancels one job (records `failed`, `cancelled=true`). `queued` = worker hasn't acquired the guard yet (honest, never phantom-active). One shared per-agent flock guard excludes concurrent turns (no order guarantee); backend/profile/model switches refuse while busy.

## Live sessions (non-blocking)

```sh
ag spawn -- bash
ag snap <id> --clean              # many readers OK; --since <offset> for incremental
ag send <id> "ls"                 # or --key ctrl-c|enter|tab|esc|up|down|left|right|ctrl-d
ag wait <id> --timeout 30         # only blocking cmd; always bound it
ag kill [--force] <id>
ag sessions · ag status · ag events --limit 20
```

`spawn` returns instantly; nothing blocks unless asked. Poll with `snap`. Daemon `kill -9` → session `stale` (`exit=-1`), pending wake does NOT fire — re-wake manually. `forget <id>` wipes logs (auth traces).

## Memory / context

`ag note "..."` / `ag notes` · `ag todo add|list|done|clear` · `ag history` · `ag context show <name>` (what agent got + warnings) · `ag compact show <name>` (default off) · `ag context checkpoint <name> --file ckpt.md` (shared handoff).

Small file ops (`run`/`read`/`write`/`edit`/`ls`/`grep`) exist but prefer native tools — use `ag run` only for `.agent/history.jsonl` audit trail. Dangerous `run` blocks → `ag approvals` → `ag approve <id>` (10 min) or re-run with `--force`. `ag run -- echo --json` (separator required, else `--json` eaten as global flag). Multi-token commands are shell-quoted; old approvals re-prompt once.

## Harness profiles (opt-in, shared)

Scoped files under `.agent/profiles_effective/<name>/`. No home/global writes.

```sh
ag harness show|export|import|link --to DIR
ag harness profile add docs --instructions ./AGENTS.md --memory ./MEMORY.md --skills ./.claude/skills --mcp ./.mcp.json
ag harness profile list|show docs|validate docs|status
ag harness profile set oc --profile docs|--none   # clears session
```

Backend gets: claude `--append-system-prompt` + `--mcp-config` + `--plugin-dir`; opencode `OPENCODE_CONFIG` + `OPENCODE_CONFIG_DIR` (skills native); codex scoped `CODEX_HOME`; gemini/cursor skills+MCP unsupported (prompt context only, reported).

## How to use — recipes

Below `ag` means `<repo>/ag --dir <STATE>` with explicit `--dir` every call. `export AGENT_CLI_DIR=...` works only within one shell session — tool calls are fresh shells, so prefer explicit `--dir`.

### 1. Parallel subagents (fan-out)

Independent chunks to different backends, collect via logs.

```sh
ag --dir <state> agents doctor
ag --dir <state> agents list
ag --dir <state> chat send oc "implement chunk A per <spec>, report files changed"
ag --dir <state> chat send cx "implement chunk B per <spec>, report files changed"
ag --dir <state> chat log oc
ag --dir <state> chat log cx
```

Same agent = serialized (flock, no order guarantee). Different agents = parallel. Switches refuse while busy — retry when idle.

### 2. Long-running program (server / watch / tests)

Never blocks unless asked. Poll incremental output.

```sh
ag --dir <state> spawn -- npm run dev
ag --dir <state> snap <id> --clean
ag --dir <state> snap <id> --clean --since <offset>
ag --dir <state> send <id> --key ctrl-c
ag --dir <state> wait <id> --timeout 30
ag --dir <state> kill <id>
```

`offset` comes from prior `snap` output. Many readers OK. `send "text"` appends Enter by default.

### 3. Background wake (fire-and-forget turn)

```sh
ag --dir <state> wake oc "summarize test failures, propose fix"
ag --dir <state> wakes
ag --dir <state> events --limit 20
ag --dir <state> chat log oc
```

At-most-once launch, no retry — re-`wake` manually. `wakes --cancel <job>` cancels a queued/running job (`failed`, `cancelled=true`); `kill <worker-sid>` stops a wake worker (job marked failed).

### 4. Context handoff (multi-agent chain)

Scoped task + memory so next agent continues, not restarts.

```sh
ag --dir <state> context init
ag --dir <state> context assign ui-a --scope ui --task "own chat panel" --acceptance "tests pass" --brief-file brief-a.md
ag --dir <state> chat send ui-a "build it"
ag --dir <state> context checkpoint ui-a --file ckpt-a.md
ag --dir <state> context assign ui-b --scope ui --task "continue chat panel" --brief-file brief-b.md
ag --dir <state> context show ui-b
```

`show` = what agent actually got (sources + warnings). Same scope inherits prior `HANDOFF.md`; sibling scopes isolated. Native TUI gets no auto-inject — run `context show NAME` manually there.

### 5. Harness profiles (shared skills/MCP/memory)

One named profile, N agents, all backends get scoped config (no global writes).

```sh
ag --dir <state> harness profile add docs --instructions ./AGENTS.md --memory ./MEMORY.md --skills ./.claude/skills --mcp ./.mcp.json
ag --dir <state> harness profile validate docs
ag --dir <state> harness profile set oc --profile docs
ag --dir <state> harness profile set claude --profile docs
ag --dir <state> chat send oc "hi"
```

Effective files live under `.agent/profiles_effective/<name>/`. `set --none` clears. `rm` refuses while agents use it. gemini/cursor: skills+MCP reported unsupported, prompt context only.

### 6. Approvals + audit (dangerous commands)

Dangerous `run` blocks, never executes. Approve explicitly.

```sh
ag --dir <state> run -- rm -rf /tmp/stale-cache
ag --dir <state> approvals
ag --dir <state> approve <id>
ag --dir <state> history
ag --dir <state> events --limit 20
```

Approval lives 10 min. Or re-run with `--force`. `run -- <cmd>` separator required when child takes flags. Every `run` lands in `history.jsonl`.

### 7. Persistent bots (jobs that stay on system)

Pattern: long-lived `spawn` + `--wake` follow-up + `--on-exit` hook. `ag` has no scheduler and no auto-restart — persistence = tracked session plus explicit re-queue.

```sh
ag --dir <state> spawn --wake watcher --wake-message "summarize output, file follow-up task" -- ./poll-loop.sh
ag --dir <state> sessions
ag --dir <state> snap <id> --clean --since <offset>
ag --dir <state> wakes --limit 20
```

Rules: at most one wake fires per session (atomic claim, exit/kill/force-kill all count). Worker completion never re-fires (no loops). If the *daemon* dies before firing (`stale`), the follow-up is still lost — re-wake manually. For true always-on, pair with host scheduler (`cron`/`launchd`) re-issuing `ag wake <agent> "check queue"` — each tick is one durable job. Keep bot state in `context checkpoint` / `note` so a replacement worker resumes. `kill <id>` stops the bot; `forget <id>` wipes its logs.

## Sanity

`ag selfcheck` · `ag keys` (cheat-sheet).

Deeper: `<repo>/README.md` (setup, capability matrix, TUI keys, limitations) + `docs/AGENT_CONTEXT.md` (scoped context) before touching `harness profile` or debugging what a backend actually received. Outbound multi-host sync: `docs/MULTI_HOST.md` (explicit stop-source-first handoff, no automatic failover).
