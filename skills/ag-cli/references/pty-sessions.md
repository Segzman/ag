# PTY sessions (spawn, snap, send, wait, kill)

```sh
{{AG}} --dir <state> spawn [--name N] [--cols C] [--rows R] [--cwd D] [--env K=V] [--on-exit CMD] -- CMD...
{{AG}} --dir <state> snap <id> [--lines N] [--since OFFSET] [--clean]   # many readers OK
{{AG}} --dir <state> send <id> "cmd" [--key enter|tab|up|down|left|right|esc|ctrl-c|ctrl-d] [--no-enter] [--secret]
{{AG}} --dir <state> wait <id> [--timeout S]                            # only blocking cmd; always bound it
{{AG}} --dir <state> kill <id> [--force] | forget <id> | sessions
```

`spawn` returns instantly; poll with `snap` (`--since <offset>` incremental; offset comes from prior output). `send` appends Enter unless `--no-enter`; `--key` for control keys; `--secret` pops the masked dialog and types the value (never logged). `forget <id>` wipes logs (auth traces).

A daemon `kill -9` leaves the session `stale` (`exit=-1`); its pending wake fires once on next reconcile (`sessions`/`snap`/`wait`/`kill`).

`events --follow` tails new lines (0.5 s poll) and terminal wake-job changes (`wake_done`/`wake_fail`/`wake_cancel`); `--exit-on-match` exits 0 on the first match, `--timeout S` exits 124 if none, `--agent A` / `--type T1,T2` filter, `--json` = one event object per line.

`spawn --wake "<msg>"` = follow-up wake when the session ends: see `ag-agents` (`references/delegation.md`, `references/wakes-cohorts.md`).
