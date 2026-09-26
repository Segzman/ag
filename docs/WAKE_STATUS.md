# Wake status (honest) + launch errors

Job states: `queued` → `running` → `done` / `failed`. Terminal states are
exact-once via `_wake_finish`. Same-agent jobs serialize on the shared
per-agent turn guard (POSIX flock, crash-releases); no ordering guarantee,
no scheduler, no concurrency cap.

## Honest queued vs running

- `queued`: the worker has not yet acquired the turn guard. It may carry
  `wait_reason: "waiting for agent turn guard"` while blocked.
- `running`: the worker holds the guard and the backend is launching or
  launched. Set only from `run_turn`'s `on_start` callback, fired right
  after guard acquisition. Fields: `pid`, `running_at`.
- A pending same-agent job therefore never looks active, and killing a
  queued worker reconciles to `failed (worker gone)`, never to a phantom
  stall.

Interface: `run_turn(sdir, name, text, on_event=None, command=None,
on_start=None)`. The control track appends `timeout_s=0` after `on_start`;
callers must pass everything by keyword after `text`. Slash/validation/
removed-agent paths never fire `on_start`; the worker finish still closes
them from `queued`, so no permanent queued leftover is possible.

## Stall rule

`--timeout` ages `running` jobs from `running_at` only. Queued jobs never
report `stalled` and never emit `wake_stall`, however long they wait for
the guard. Dead workers (missing/dead session incl. stale daemons) still
fail closed with `worker gone` regardless of state.

## Launch errors (bounded, no env/secrets)

- Missing project dir: `missing project directory: <dir> for agent <name>
  (not executing; ag agents list)`. The backend binary never runs.
- Missing executable: `backend binary missing: <argv0> (ag agents doctor)`.
- Backend nonzero: `backend exit <code>: <stderr/reply excerpt ≤160c>`.
  Failed jobs keep the exit code plus the excerpt, so remote
  `wakes --wait` callers can act without reading logs.
- Spawn daemons: a bad session cwd never falls back. The child writes
  `ag: cannot chdir to <cwd> ... (not executing <cmd> in fallback
  directory)` to its tracked output and exits 127, so `snap` shows the
  error and `wait`/polling terminates instead of hanging.
