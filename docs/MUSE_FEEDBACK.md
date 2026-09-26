# Muse feedback — brief items 1–8, disposition (integrated final)

Source: Codex brief (8 headless-orchestration asks). Integrated in main:
harness selection + behavior presets, wake control (timeout/cancel/kill),
and multi-host portable snapshots. No numeric concurrency cap is enforced
anywhere; queue/dependencies, ephemeral agents, and automatic post-turn
hooks remain deferred (see table).

| # | Brief item | Disposition |
|---|---|---|
| 1 | 2-concurrency cap / refuse-or-queue | Not built. No ag-side 2-cap was found: the original report was a backend `exit 1` of unproven cause, and this run observed a provider 429 instead — neither confirms an ag kill. Same-agent turns serialize on the shared guard (no arrival-order guarantee); different agents run in parallel. No queue. |
| 2 | Opaque failures (`backend exit 1`) | Covered: launch failures distinguish missing binary vs missing project dir (`_describe_backend_launch`); backend stderr tail is retained in the wake failure record. |
| 3 | `chat send` has no timeout | Covered: `chat send`/`delegate --timeout` default 1800, `0` = unlimited, non-finite/negative rejected. Expiry kills the backend process group (descendants included) and returns JSON `timed_out:true` with CLI exit 124. Wake jobs take `--max-runtime` (guard acquisition starts the budget). See [WAKE_CONTROL.md](WAKE_CONTROL.md). |
| 4 | Queue / `--depends-on` | Deferred. Cron-sequencer workaround stands. |
| 5 | Completion notification (`wait`/callback) | Covered for wake: `wakes --wait`, `--notify` (chat tool-note), `--on-done` shell hook. Callback hook for sync turns deferred. |
| 6 | Ephemeral agents (`wake --ephemeral`) | Deferred. Manual `agents add → wake → agents rm` stands. |
| 7 | Memory pre/post hooks | Partially covered: harness profiles inject instructions/memory/skills per agent; `context assign/show/checkpoint` covers handoffs. Automatic post-turn hooks deferred. |
| 8 | Headless-first, no TTY assumptions | Partially audited: the added paths (`harness use/current`, `roles list/add/set/show/reset/rm`) all work headless with `--json` and nonzero-exit errors. Pre-existing CLI surface outside this scope was not re-audited. See [HARNESS_ROLES.md](HARNESS_ROLES.md). |

Multi-host: portable task snapshots over outbound SSH (`hosts`, `sync pull/_export`), offline only with explicit `--source-stopped`. See [MULTI_HOST.md](MULTI_HOST.md).

Out of scope everywhere here: per-agent resource limits, usage accounting,
event-stream follow, `--dry-run`, job GC, `ag doctor` preflight — future work.
