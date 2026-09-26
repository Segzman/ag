# Wake-state reliability

Scope: `wake` enqueue + terminal transitions only. Statuses stay
`queued`/`running`/`done`/`failed`. At-most-once launch, no retries, no
coalescing: every user `wake` enqueues exactly one durable job.

## Ordering

`launch_wake_chat` pre-generates the worker session id, prepares the session
status, then publishes the job (`session` set) + `wake` event via a pre-fork
hook, and only then forks the daemon (`_prepare_tracked` / `pre_fork` in
`_spawn_tracked` / `_spawn_wake_session`). No startup gap: reconcile never
sees a published sid without a live session. The parent never rewrites the
job after spawn, so a fast worker that marks `running`/`done` in between
cannot be clobbered back to `queued`. Spawn failure marks the job `failed`
via `_wake_finish` with a nonzero `backend_exit` (exactly once).

## Terminal exactly-once

`_wake_finish` serializes the read-check-write under a per-job flock
(`wake/locks/job-<jid>.lock`, crash-releases). The loser sees a terminal
job and returns untouched. If the guard cannot be acquired, the job is
returned untouched (no unlocked transition). If the terminal write fails to
persist, no terminal events/hooks fire. Events (`wake_done`/`wake_fail`) and
notify/`on_done` hooks fire after the guard is released.

Lock discipline: the job guard is a leaf. Never acquire the per-agent turn
guard (`wake/locks/<agent>.lock`, owned by `run_turn`) while holding it,
and never hold it across backend turns or hook execution. The worker's
`queued`->`running` write stays outside the guard (a future `on_start`
move is compatible: one spawn per job, parent never rewrites).

## Out of scope

`stalled` flag writes, `run_turn`/`on_start`, CLI surface, retries.
