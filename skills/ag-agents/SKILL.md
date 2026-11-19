---
name: ag-agents
description: Delegates work to other agents through `ag` (headless chat send turns, background wakes, parallel fan-out, model routing, second opinions, verify/escalate, queue/stop/steer, schedules, worktrees, relay mail). Load when asked to "have another agent do X", run tasks in parallel/background, pick a model/backend for a job, cross-check a result, or steer/stop/queue/schedule an agent. State/PTY/secret rules: skill ag-cli.
---

# ag agents (scope: {{SCOPE}})

Binary: `{{AG}}`. One invocation per shell call, always `--dir <state>`. Same agent: turns queue FIFO (steer first); different agents run in parallel.

## Routing

Pick the job type first (mechanical, implement, review, debug, plan, hardest), then the routed model. `{{AG}} route [JOB]` = effective table of the calling harness; cheapest model that can do the job, escalate on failure. Change routing with `ag setup`, never by hand.

First ag delegation in a project: `{{AG}} route --check --json`; if `configured` is false, ask the user the returned `questions` with your harness's question tool, then `{{AG}} setup --scope project --answers '<json>'`. Never guess answers.

## Quick reference

```sh
{{AG}} --dir <state> agents list                                        # roster; agents doctor = backends + auth
{{AG}} --dir <state> agents add w --backend opencode --role sub --job implement [--mode edits] [--worktree] [--data work] [--fallback codex/default]
{{AG}} --dir <state> chat send w "<self-contained task>" --job implement
{{AG}} --dir <state> chat send w "<task>" --verify "<cmd>" --escalate m2,m3   # check the work, climb models on fail
{{AG}} --dir <state> chat log w --last                                  # final reply, raw text; chat diff = turn patch
{{AG}} --dir <state> chat steer w "new instruction"                     # interrupts the running turn, runs next (--timeout 30 bounds the whole steer)
{{AG}} --dir <state> wake w "<task>" --max-runtime 900 [--request-id K]  # background, returns now
{{AG}} --dir <state> wakes --wait <job> --timeout 60                    # block: exit 0 ok, 1 failed, 124 timeout
{{AG}} --dir <state> crosscheck a b "<task>" [--judge j]                # second opinion, AGREE/DISAGREE
{{AG}} --dir <state> stop w [--cascade] [--all-wakes]                   # hold + cancel + interrupt; chat stop w = same; queue w --release resumes
{{AG}} --dir <state> agents set w --fallback codex/default              # failover on usage limit; primary retried after reset
{{AG}} --dir <state> channel post room "@a review, then @b ship"        # shared room hand-off chain
{{AG}} --dir <state> schedule add w --every 15m "msg"                   # recurring; needs global tick (ag-cli reference)
{{AG}} --dir <state> relay name claim muse [--session ID] [--default]   # per-session mail; then relay inbox --name muse, relay say
```

## Pitfalls

- Tasks are self-contained: what to do, what done looks like. Agents see only the task + their own history.
- `agents add|set --dir` = the agent workdir (there is no `--cwd` there). One writer per worktree: parallel writers need `agents add ... --worktree` (state `--dir` must be inside a git repo); a shared dir = agents clobber each other.
- `--verify "<cmd>"` runs via `sh -c` in the agent's workdir: give test paths relative to it.
- `wake` is at-most-once *launch*: verify via `events` + `wakes`; relaunch lost ones with `wakes --reap`.
- Usage limit hit: not retried; result has `usage_limit: true` + `reset_at`. Configure failover with `agents add|set --fallback BACKEND/MODEL` (e.g. `codex/default`).
- `chat steer` interrupts the running turn (queued turns keep FIFO order). Idle agent: steer says so and exits - use `chat send`. A stuck-looking agent: `ag stop NAME` (or `ag chat stop NAME`), then `ag queue NAME --release`.
- Wait with `wakes --wait` or `events --follow --exit-on-match`, never shell polling loops.
- Agent self-reports lie: use `--verify "<cmd>"` with a real check; review `chat diff` before accepting.
- Prefer `--mode ro` for review/plan jobs; never `full` unless asked. Child mode cannot exceed the parent's.
- Data privacy: work code only to zdr models (`--data work`; `work` refuses train/unknown, exit 2). Never request secrets via chat. Don't switch backends/models unless asked. Never `tui`/`attach`/`shell`/`handoff --exec`.

## References (load only when needed)

- `references/delegation.md` - delegate table, `chat send|log`, `delegate`, timeouts, verify/escalate, idempotency, notify, slash text.
- `references/crosscheck.md` - second opinions, suggested ZDR pairs, benchmarks, openrouter.
- `references/wakes-cohorts.md` - `wake`, `wakes` wait/cancel/reap, cohorts, queue/stop/steer.
- `references/roster-permissions.md` - `agents add|set|create|fork|rm|reset`, caps/modes, effort, stall, per-agent env/keys, ACP, roles, `harness use`.
- `references/channels.md` - `channel create|post|log|list|rm`, hop caps.
- `references/reliability-limits.md` - retries, rate limits, usage-limit detection, `--fallback`, `ratelimit`, reap.
- `references/schedule-relay.md` - `schedule add` flags (`--tz`), relay mailbox, relay names/inbox, remote schedules, one-shot cloud wake.
- `references/worktrees-checkpoints.md` - worktree per agent, per-turn checkpoints/revert, `chat usage`, context handoff, compact.
- `references/harness-multihost.md` - harness profiles, hosts/sync multi-host.
- `references/privacy-access-budget.md` - privacy table, guardrails, `access`, `usage`, `budget`.
