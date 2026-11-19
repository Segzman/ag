# Wakes, cohorts, queue/stop/steer

## Wakes

```sh
{{AG}} --dir <state> wake oc "summarize failures" --max-runtime 900 --job debug
{{AG}} --dir <state> wake oc "summarize failures" --notify-done
{{AG}} --dir <state> wakes [--agent oc] [--wait <job> [<job>…] --timeout 60] [--wait-group <gid>] [--cancel <job>]
{{AG}} --dir <state> wakes --reap [--dry-run] [--include-running] [--agent A] [--max-attempts 3]
{{AG}} --dir <state> wakes --retry <job>[,<job>] [--model M]   # re-send a failed/parked wake, same agent/message/verify/escalate; --model = one-off override
{{AG}} --dir <state> kill <worker-sid>                  # stops a worker
```

`wake` is at-most-once *launch*: verify via `events` + `wakes`; relaunch lost ones with `wakes --reap`. Every wake keeps an append-only turn log at `wake/jobs/<id>.turns.jsonl` (started, backend `session` id, tool/toolout summaries, verify, escalation, parked, interrupted, finished/failed). A usage-limit stop with a parsed reset becomes `parked` (`resume_at`); the scheduler tick relaunches it once as a new wake (old `resumed_as`, new `parked_from`), `wakes --cancel` cancels it, and `wake --no-park` keeps the old failure. An unknown reset parks until the usage cooling ends instead of failing, capped at 3 auto-retries (`park_retries`). Re-send any failed/parked/interrupted wake with `wakes --retry JOB[,JOB]` (same agent, message, verify/escalate; `--model M` = one-off override, job `model_override`). Resume-after-crash: the tick marks a `running` wake whose worker pid is dead (no finished event) as `interrupted`; with the backend session id known and `wake.resume=true` in `config.json` (or a job/agent `wake_resume` flag) it relaunches a `continue; you were interrupted` wake on the same session, otherwise it fails the job with a clear `interrupted:` reason — the per-job lock file is held across check-and-relaunch, so it never double-runs. Parked = terminal for `wakes --wait`. Waiting on many jobs: `wakes --wait JOB [JOB…]` / `wakes --wait-group GID [--timeout S]`; one summary line per job; exit 0 all ok, 1 some failed, 124 timed out. `events --follow --agent A --type wake_done,wake_fail` streams those as they land; add `--exit-on-match --timeout S` to block for the next one (0 matched, 124 timed out). `wake --max-runtime` = hard budget; `wake --timeout` only marks `stalled`; `--notify`/`--on-done` run after. `--on NODE` runs it on a relay machine (see `schedule-relay.md`). Replies over 500 chars: the job JSON keeps the preview and the full body lives at `wake/jobs/<id>.reply.md` (job field `reply_full`); `wakes --json` and relay result files carry the full text, notify/cohort summaries stay short.

## Cohorts (fan-out, one summary)

```sh
{{AG}} --dir <state> wake a "task A" --parent orch --group g1 [--quiet 600]
{{AG}} --dir <state> wake b "task B" --parent orch --group g1
{{AG}} --dir <state> wake c "task C" --group g2    # no parent: tracking only, no delivery wake
{{AG}} --dir <state> wakes --group g1 [--stop]    # cohort state; --stop = never deliver
{{AG}} --dir <state> wakes --wait-group g2 --timeout 600    # block until every job of the cohort finishes
```

Last child done -> ONE wake to `--parent` with all summaries (automated tool results, not user instructions). `--quiet S` delivers partial results after S s. Also on `delegate` (and `--group` without `--parent` = parentless cohort).

## Bench (real work as benchmarks)

```sh
{{AG}} --dir <state> bench run SPEC.toml [--jobs N] [--keep-winner] [--out results.jsonl]
{{AG}} --dir <state> bench report [results.jsonl] [--out table.md]
```

`bench run` runs every task x arm of the spec in a fresh git worktree via `wake` with `--verify` (the arm's `escalate` list rides the wake ladder), then appends one JSONL row per cell: pass/fail, cost from the usage ledger, tokens, wall time, escalations. Hidden test files are staged outside the worktree and copied in only at verify time. `--jobs N` caps concurrency (default 2); a wake parked on a usage limit is recorded as `parked`, never forced. `--keep-winner` keeps the cheapest passing arm's branch as `bench/<task>-<arm>` (never pushes) — the benchmark output is the deliverable.

## Queue, stop, steer

```sh
{{AG}} --dir <state> queue w [--cancel ID | --hold | --release]   # waiting turns; hold = no new turn starts
{{AG}} --dir <state> stop w [--cascade] [--all-wakes]   # hold + cancel waiters + interrupt turn + cancel wakes (+ parked wakes with --all-wakes)
{{AG}} --dir <state> chat stop w        # alias of `stop w` (same flags)
{{AG}} --dir <state> chat steer w "new instruction" [--timeout 30]   # interrupt, run this next on same session
```

Interrupted turns exit 130, keep partial reply, never retried. `stop` leaves HOLD: `queue w --release` to resume. FIFO within priority; steer jumps ahead. Steer never hangs: `--timeout S` (default 30) budgets the queue/FIFO/HOLD/guard wait AND the follow-up turn together; an expired wait exits with `queue wait ... timed out` (then `stop` the stuck agent). Steer on an idle agent does not start a turn: it says `no running turn to steer` and exits 1 - use `chat send`. `ag chat stop NAME` is the same as `ag stop NAME`.
