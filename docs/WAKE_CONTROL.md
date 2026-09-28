# Wake control: cancellation + hard runtime limit

## Commands

```sh
ag wake NAME --max-runtime 60 MESSAGE     # hard backend limit, 0 = unlimited
ag wakes --cancel JOB                     # cancel one wake job (not --wait)
ag wakes --wait JOB --timeout 30          # unchanged client-side wait
ag wake NAME --timeout S MESSAGE          # unchanged stall notification
ag chat send NAME --timeout 300 TEXT      # default 1800, 0 = unlimited
ag delegate NAME --timeout 300 TASK       # default 1800, 0 = unlimited
```

`--max-runtime` / `--timeout` reject negative / NaN / Inf before launch
(no job created, no turn started). `--cancel` + `--wait` together is an
error. Terminal cancel is a no-op report.

## Semantics

- Budget starts when the per-agent guard is acquired (`on_start`), so queue
  wait burns nothing. Covers active backend execution.
- Hard expiry kills the backend process group (TERM, 2s grace, KILL),
  releases the guard, records `failed` with `timed_out=true`, exit 124,
  reason `max-runtime Ns exceeded`.
- Cancel records `failed` with `cancelled=true`, exit 130, reason
  `cancelled`. Terminal statuses stay `done`/`failed` (backward compatible).
- Cancel signals only that job's worker session + worker pid, sets
  `cancel_requested`, finishes via `_wake_finish` idempotence (a concurrent
  worker done/failed wins; no replay, no `spawn --wake` followup — wake
  workers carry `wake_agent=""` and cancel never calls `maybe_fire_wake`).
- `--timeout` still only marks `stalled` + `wake_stall`; the worker keeps
  running. `--wait --timeout` still only bounds the waiting client.
- Foreground budget (`chat send` / `delegate --timeout`, default 1800s,
  explicit 0 = unlimited) covers backend execution after the per-agent guard
  is acquired. Time spent waiting for a busy agent's lock is NOT counted —
  a queued foreground turn waits as long as needed, then gets the full
  budget. Wake `--max-runtime` behaves the same via the same `timeout_s`
  path. JSON reports timeouts as `{exit: 124, timed_out: true, error}`.

## Race rules (cancel)

- Startup merges fresh job state before publishing `running`, preserving a
  concurrent `cancel_requested`; a post-write recheck exits without running
  the backend when cancel landed mid-startup. A cancelled job never flips
  back to running and its message is never replayed.
- Kill scope is strictly per-job: the worker's session process group plus a
  direct worker-pid signal guarded by a `ps` check for `_wake_run <jid>`
  (pid reuse can never hit an unrelated holder; `ps` missing = skip).
- Terminal metadata patches (`cancelled`, `timed_out`) re-read the job
  first and only patch the winner's record (`failed` + matching reason);
  a concurrent worker done/failed is reported as already-terminal, never
  overwritten. All terminal transitions go through `_wake_finish`
  idempotence. Per-job flock (state track) will serialize these further;
  this track only does read-merge-write and never reorders enqueue/startup.

## Interfaces

```python
run_turn(sdir, name, text, on_event=None, command=None,
         on_start=None, timeout_s=0)
agent_cancel(sdir, jid, reason="cancelled")  # _wake_cancel alias
delegate_task(sdir, agent, task, on_event=None, timeout_s=0)
```

`on_start` fires right after guard acquisition (worker records
`guard_acquired_at`). Backend runs with `start_new_session=True` so expiry
kills only its own group (`killpg(pid)` where pid is the group leader, only
while alive). Worker SIGTERM/SIGINT first stops the active backend, honors
`cancel_requested`, then exits — so `wakes --cancel` never orphans backends.

## Limitation

`ag kill [--force] <worker-session>` stops the session but does not run the
wake-cancel path: a TERM-ignoring backend (and its children) may survive as
an orphan. Use `ag wakes --cancel JOB` to stop wake work reliably.

## Parallelism

No global cap: distinct agents run concurrently; only same-agent turns
serialize on the per-agent flock guard (`run_turn` owns it, crash-releases).
Use different agent names for parallel work. Provider-side limits (e.g.
free-tier 429) surface as backend failures. `ag` does not terminate sibling
jobs when one fails; providers can independently reject multiple requests. Regression:
`python3 tests/test_ag_parallel.py` (4-way fake-backend overlap,
same-name serialize, one-timeout survivors).

## Tests

`python3 tests/test_ag_wake_control.py` — fake `printf` backends on PATH
(TERM-ignoring parent + child, pidfile-verified), temp state only.
