---
name: ag-agents
description: Delegates work to other coding agents through `ag` (headless turns, background wakes, parallel fan-out, second opinions, scheduled runs, per-agent permissions, worktree isolation). Use when asked to "have another agent do X", pick a model/backend for a job, run tasks in parallel or in the background, cross-check a result, verify or escalate a delegated turn, queue/stop/steer an agent, schedule a wake, or hand over context.
---

# ag agents (scope: {{SCOPE}})

Binary: `{{AG}}`. One invocation per shell call, always `--dir <state>` (state/TTY/secret rules: skill `ag-cli`). Same agent: turns queue FIFO (steer first); different agents run in parallel.

## Routing

Pick the job type first (mechanical, implement, review, debug, plan, hardest), then use the routed model. Run `{{AG}} route` for the effective table of the calling harness (detected via `AG_HARNESS` > parent processes > env), `{{AG}} route JOB` for one job. Cheapest model that can do the job; escalate on failure. Change routing with `ag setup`, never by hand.

First ag delegation in a project: run `{{AG}} route --check --json`. If `configured` is false, ask the user the returned `questions` with your harness's question tool (Claude Code: AskUserQuestion), then `{{AG}} setup --scope project --answers '<json>'` (shape in `answers_format`). Never guess answers.

## Core loop

```sh
{{AG}} --dir <state> agents list                         # roster; `agents doctor` = backends + auth
{{AG}} --dir <state> agents add w --backend opencode --role sub --job implement [--mode edits]
{{AG}} --dir <state> chat send w "<self-contained task>" --job implement   # one headless turn
{{AG}} --dir <state> chat log w --last                   # final reply, raw text
{{AG}} --dir <state> wake w "<task>" --max-runtime 900   # background, returns now
{{AG}} --dir <state> wakes --wait <job> --timeout 60     # block on jobs; exit 0 ok, 1 failed, 124 timeout
{{AG}} --dir <state> chat send w "<task>" --verify "<cmd>" --escalate m2,m3   # check the work, climb models on fail
{{AG}} --dir <state> crosscheck a b "<task>" [--judge j] # second opinion, AGREE/DISAGREE
{{AG}} --dir <state> chat steer w "new instruction"      # interrupt, run next
{{AG}} --dir <state> stop w [--cascade]                  # hold + cancel + interrupt; `queue w --release` resumes
{{AG}} --dir <state> channel post room "@a review, then @b ship"   # shared room hand-off chain
{{AG}} --dir <state> schedule add w --every 15m "msg"    # recurring (needs global tick; see ag-cli)
```

## Rules

- Tasks are self-contained: what to do, what done looks like. Agents see only the task + their own history.
- Fan-out = different agents; two chunks to one agent serialize.
- Agent self-reports lie: use `--verify "<cmd>"` with a real check; review `chat diff` before accepting.
- `wake` is at-most-once *launch*: verify via `events` + `wakes`; relaunch lost ones with `wakes --reap`. Retried calls: `--request-id K`.
- Prefer `--mode ro` for review/plan jobs; never `full` unless asked. Child mode cannot exceed the parent's.
- Data privacy: work code only to zdr models (`agents add|set --data work|public`; `work` refuses train/unknown, exit 2).
- Don't switch backends/models unless asked. Never request secrets via chat. Unknown `/word` errors; don't retry as prose.
- Persistent bots = `schedule add` (or `spawn --wake`). Never use `tui`/`attach`/`shell`/`handoff --exec`.
- Shared dir = agents clobber each other: use `agents add ... --worktree` for parallel writers.
- Usage limit hit? Result has `usage_limit: true` + `reset_at`; configure `--fallback BACKEND/MODEL`. See reliability reference.

## References (load only when needed)

- `references/delegation.md` - delegate table, `chat send|log`, `delegate`, timeouts, verify/escalate, idempotency, notify, slash text.
- `references/crosscheck.md` - second opinions, suggested ZDR pairs, benchmarks, openrouter.
- `references/wakes-cohorts.md` - `wake`, `wakes` wait/cancel/reap, cohorts, queue/stop/steer.
- `references/roster-permissions.md` - `agents add|set|create|fork|rm|reset`, caps/modes, effort, stall, per-agent env/keys, ACP, roles, `harness use`.
- `references/channels.md` - `channel create|post|log|list|rm`, hop caps.
- `references/reliability-limits.md` - retries, rate limits, usage-limit detection, `--fallback`, `ratelimit`, reap.
- `references/schedule-relay.md` - `schedule add` flags (`--tz`), relay mailbox, remote schedules, one-shot cloud wake.
- `references/worktrees-checkpoints.md` - worktree per agent, per-turn checkpoints/revert, `chat usage`, context handoff, compact.
- `references/harness-multihost.md` - harness profiles, hosts/sync multi-host.
- `references/privacy-access-budget.md` - privacy table, guardrails, `access`, `usage`, `budget`.
