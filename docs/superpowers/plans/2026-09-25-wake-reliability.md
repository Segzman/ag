# Wake reliability implementation plan

**Goal:** Make background agent work reliable to start, inspect, stop, and bound over remote CLI sessions.

**Scope:** Three independent Muse Spark 1.3 implementation tracks, integrated after review. This is not a global concurrency-cap feature: the user requested parallel implementation workers.

**Constraints:** Single-file Python CLI; Python 3.9+; stdlib only; POSIX; explicit state directories; preserve existing commands and the default unlimited runtime. No implicit retries, dropped/coalesced user requests, or changes to backend permissions.

## 1. Job-state transitions

Files: `ag`, `tests/test_ag_wake_state.py`.

- [ ] Reproduce a fast worker completing before the launcher publishes its session link.
- [ ] Publish startup metadata before the worker can change job state; never overwrite terminal state with a stale queued snapshot.
- [ ] Test concurrent completion attempts; serialize terminal transitions if needed so terminal events/hooks occur once.
- [ ] Keep job locks short, crash-released, and outside backend execution.

## 2. Waiting status and launch errors

Files: `ag`, `tests/test_ag_wake_status.py`.

- [ ] Keep a wake queued while waiting for its agent's turn lock.
- [ ] Add an optional `run_turn` start callback invoked after admission; set `running_at` when execution actually starts.
- [ ] Distinguish missing working directories from missing executables and preserve bounded backend failure detail.
- [ ] A tracked session whose working directory cannot be entered must fail instead of running elsewhere.
- [ ] Preserve completion of slash, validation, and removed-agent paths.

## 3. Cancellation and hard runtime limits

Files: `ag`, `tests/test_ag_wake_control.py`.

- [ ] Add `wakes --cancel JOB`; cancel only that job and its worker, without launching completion follow-ups.
- [ ] Add `wake --max-runtime SECONDS`; default zero means unlimited. Reject negative and nonfinite values before launch.
- [ ] Start the runtime clock after the per-agent guard is acquired, not while waiting.
- [ ] Stop the backend process group on expiry, including TERM-ignoring children; release the guard.
- [ ] Retain terminal statuses `done`/`failed`, with cancellation/timeout metadata and actionable reasons.
- [ ] Keep `wake --timeout` as a stall warning and `wakes --wait --timeout` as a caller wait budget.

## Integration contract

The combined runner signature is `run_turn(sdir, name, text, on_event=None, command=None, on_start=None, timeout_s=0)`. Existing callers must remain valid. Integrate job-state changes first, status changes second, control changes last. Review callback/terminal-update races, cancellation while queued, cancellation concurrent with completion, and lock ordering explicitly.

## Verification and delivery

- [ ] Each track demonstrates a failing regression before its fix and passes relevant existing tests.
- [ ] Review each isolated commit; integrate without reverting another track's behavior.
- [ ] Run all `tests/test_ag_*.py`, `tests/test_install.py`, and `ag selfcheck` against the integrated result.
- [ ] Update the main command reference with the final tested semantics.
- [ ] Commit and push the completed update; preserve unrelated local files and worktrees.

## Research basis

T3 Code's subagent lifecycle and drainable-worker model informed honest waiting/completion states. OpenCode's explicit interruption and background-task lifecycle informed cancellation. Limits on nested delegation and model steps are separate future work; they are not presented as limits on concurrent `ag` workers.
