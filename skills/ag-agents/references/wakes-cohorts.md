# Wakes, cohorts, queue/stop/steer

## Wakes

```sh
{{AG}} --dir <state> wake oc "summarize failures" --max-runtime 900 --job debug
{{AG}} --dir <state> wake oc "summarize failures" --notify-done
{{AG}} --dir <state> wakes [--agent oc] [--wait <job> [<job>…] --timeout 60] [--wait-group <gid>] [--cancel <job>]
{{AG}} --dir <state> wakes --reap [--dry-run] [--include-running] [--agent A] [--max-attempts 3]
{{AG}} --dir <state> kill <worker-sid>                  # stops a worker
```

`wake` is at-most-once *launch*: verify via `events` + `wakes`; relaunch lost ones with `wakes --reap`. A usage-limit stop with a parsed reset becomes `parked` (`resume_at`); the scheduler tick relaunches it once as a new wake (old `resumed_as`, new `parked_from`), `wakes --cancel` cancels it, and `wake --no-park` keeps the old failure. Parked = terminal for `wakes --wait`. Waiting on many jobs: `wakes --wait JOB [JOB…]` / `wakes --wait-group GID [--timeout S]`; one summary line per job; exit 0 all ok, 1 some failed, 124 timed out. `events --follow --agent A --type wake_done,wake_fail` streams those as they land; add `--exit-on-match --timeout S` to block for the next one (0 matched, 124 timed out). `wake --max-runtime` = hard budget; `wake --timeout` only marks `stalled`; `--notify`/`--on-done` run after. `--on NODE` runs it on a relay machine (see `schedule-relay.md`). Replies over 500 chars: the job JSON keeps the preview and the full body lives at `wake/jobs/<id>.reply.md` (job field `reply_full`); `wakes --json` and relay result files carry the full text, notify/cohort summaries stay short.

## Cohorts (fan-out, one summary)

```sh
{{AG}} --dir <state> wake a "task A" --parent orch --group g1 [--quiet 600]
{{AG}} --dir <state> wake b "task B" --parent orch --group g1
{{AG}} --dir <state> wake c "task C" --group g2    # no parent: tracking only, no delivery wake
{{AG}} --dir <state> wakes --group g1 [--stop]    # cohort state; --stop = never deliver
{{AG}} --dir <state> wakes --wait-group g2 --timeout 600    # block until every job of the cohort finishes
```

Last child done -> ONE wake to `--parent` with all summaries (automated tool results, not user instructions). `--quiet S` delivers partial results after S s. Also on `delegate` (and `--group` without `--parent` = parentless cohort).

## Queue, stop, steer

```sh
{{AG}} --dir <state> queue w [--cancel ID | --hold | --release]   # waiting turns; hold = no new turn starts
{{AG}} --dir <state> stop w [--cascade]       # hold + cancel waiters + interrupt turn + cancel wakes (+ children)
{{AG}} --dir <state> chat steer w "new instruction"   # interrupt, run this next on same session
```

Interrupted turns exit 130, keep partial reply, never retried. `stop` leaves HOLD: `queue w --release` to resume. FIFO within priority; steer jumps ahead.
