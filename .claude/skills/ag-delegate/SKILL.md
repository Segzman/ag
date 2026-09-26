---
name: ag-delegate
description: Delegate work through `ag` — headless turns, orchestrator fan-out, background wakes, session-bound follow-ups, and scoped context handoffs. Use for any "have another agent do X" task instead of inventing your own queue.
---

# Delegate via ag

`ag` is the only scheduler. No FIFO, no priority, no retry — one shared per-agent flock guard serializes turns on the same agent (no order guarantee); different agents run in parallel.

Below `ag` means `<repo>/ag --dir <STATE>` with explicit `--dir` every call.

## Pick the primitive

| Need | Command |
|---|---|
| One headless turn, wait for reply | `ag chat send <agent> "<task>"` then `ag chat log <agent>` |
| Orchestrator assigns, logged to its chat | `ag delegate <agent> "<task>"` |
| Background turn, returns now | `ag wake <agent> "<task>"` then `ag wakes` / `ag events --limit 20` |
| Follow-up when a session ends | `ag spawn --wake <agent> --wake-message "<task>" -- <cmd>` (fires at most once) |
| Chain with memory | `ag context assign/show/checkpoint` (see Handoff) |

`chat send` / `delegate` take `--timeout` (default 1800s, `0` = unlimited): hard backend budget starting after the per-agent guard is acquired — lock wait is unbounded. Prefer `wake` for anything slow. Background: `wake --max-runtime` is the same hard budget; `wake --timeout` only marks `stalled` (worker keeps running). `wakes --cancel <job>` cancels one job. `queued` means the worker hasn't acquired the guard yet. `wake` is at-most-once *launch*: a crash between claim and enqueue, or `kill -9` of a worker, can lose it. Verify via `events` + `wakes`, re-`wake` manually.

## 1. Headless turn (default)

```sh
ag --dir <state> agents list                       # roster with backend/model/role
ag --dir <state> chat send oc "implement X per <spec>, report files changed"
ag --dir <state> chat log oc                        # read reply
```

Same agent = serialized; switches (`agents set --backend/--model`, profile set, `/model`) refuse while busy — retry when idle. Headless backend select: `ag harness use oc codex` / `ag harness current oc`; `ag roles reset reviewer` restores one builtin preset (details: `docs/HARNESS_ROLES.md`). Keep tasks self-contained: what to do, what done looks like.

## 2. Fan-out (parallel subagents)

Independent chunks to different agents, collect via logs.

```sh
ag --dir <state> chat send oc "chunk A per <spec>, report files changed"
ag --dir <state> chat send cx "chunk B per <spec>, report files changed"
ag --dir <state> chat log oc
ag --dir <state> chat log cx
```

Never fan out two chunks to the same agent and expect parallelism — it serializes. Different agents = parallel.

## 3. Background wake (fire-and-forget)

```sh
ag --dir <state> wake oc "summarize test failures, propose fix"
ag --dir <state> wakes                              # queued|running|done|failed + worker link
ag --dir <state> events --limit 20                  # wake / wake_done / wake_fail trail
ag --dir <state> chat log oc                        # follow-up lands here
ag --dir <state> kill <worker-sid>                  # stop a wake worker (job marked failed)
ag --dir <state> wakes --cancel <job>               # cancel one job (failed, cancelled=true)
```

## 4. Session-bound follow-up

At most one follow-up per session (atomic claim; exit/kill/force-kill all count). Worker completion never re-fires (no loops). Stale daemon (`kill -9`, `exit=-1`) does NOT fire — re-wake manually.

```sh
ag --dir <state> spawn --wake watcher --wake-message "summarize output, file follow-up" -- ./poll-loop.sh
ag --dir <state> sessions
ag --dir <state> snap <id> --clean --since <offset>
```

## 5. Handoff (multi-agent chain)

Scoped task + memory so the next agent continues, not restarts.

```sh
ag --dir <state> context init
ag --dir <state> context assign ui-a --scope ui --task "own chat panel" --acceptance "tests pass" --brief-file brief-a.md
ag --dir <state> chat send ui-a "build it"
ag --dir <state> context checkpoint ui-a --file ckpt-a.md
ag --dir <state> context assign ui-b --scope ui --task "continue chat panel" --brief-file brief-b.md
ag --dir <state> context show ui-b   # what B actually got (sources + warnings)
```

Same scope inherits prior `HANDOFF.md`; sibling scopes isolated. Native TUI gets no auto-inject — run `context show NAME` manually there.

## Guardrails

- One invocation per shell call; always `--dir` (stray `.agent/` = wrong cwd, not wiped state).
- Never `tui` / `attach` / `shell` / `handoff --exec` from agent context (needs TTY). `spawn` + `snap` instead.
- Slash (`/model`, `/profile use docs`, `/help`) runs locally, never reaches the model as prose. Unknown `/word` errors — don't retry as prose.
- Dangerous `run` blocks → `approvals` → `approve <id>` (10 min) or `--force`. Audit via `history`, `events`.
- Full command reference: `using-ag` skill. Capability matrix + limits: `<repo>/README.md`. Outbound multi-host: `docs/MULTI_HOST.md` (explicit stop-source-first handoff, no automatic failover).
