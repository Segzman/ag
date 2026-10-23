# ag relay — schedules made on one machine, run on another (spec, 2026-10-23)

Use case: schedules are created on the Mac (`ag schedule add ... --on muse`) and run on the Muse VM's ag; results come
back to the Mac. The VM accepts no inbound connections, so the transport is a **mailbox both sides can reach**:
a private git repo (here `https://github.com/Segzman/ag-relay`) or a plain folder the user syncs themselves.
Each node only ever writes its own files, so git rebases never conflict.

Stdlib only, POSIX, single file `ag`. Every new flag/subcommand goes in README.md and `skills/ag-cli/SKILL.md` or
`skills/ag-agents/SKILL.md` (`tests/test_ag_doc_coverage.py`). Tests offline: dir transport + a local bare git repo
(`git init --bare` in a tempdir, URL = that path).

## Config — `$AG_CONFIG_HOME/relay.json` (`ag_config_home()`)

```json
{"node": "mac", "transport": "git", "url": "https://github.com/Segzman/ag-relay",
 "checkout": "~/.cache/ag/relay/ag-relay", "state": "~/.agent-relay", "workdir": "~/ag-relay-work",
 "max_mode": "edits"}
```
- `node`: this machine's name (default: short hostname, lowercased, `[a-z0-9-]`).
- `transport`: `git` (clone of `url` at `checkout`) or `dir` (`url` = a folder path used directly as the mailbox).
- `state`: ag state dir where **incoming** schedules are materialised on a target (registered in the schedule registry).
- `workdir`: parent of per-origin agent workdirs on a target (`<workdir>/<origin>`).
- `max_mode`: highest `--mode` a target accepts from the mailbox (`ro` < `edits` < `auto` < `full`; default `edits`).

## Mailbox layout (relative to the checkout / dir)

```
nodes/<node>.json                         heartbeat, written by <node> each sync
schedules/<target>/<origin>--<id>.json    a schedule <origin> wants run on <target>; written/deleted only by <origin>
results/<origin>/<target>--<id>--<due>.json  one run's result; written only by <target>
```
- heartbeat: `{"node", "ag_version", "ag_commit" (git rev-parse --short HEAD of the ag checkout or null), "backends"
  (usable backends from the cached provider probe, list of names), "last_seen" (epoch), "schedules_in" (count),
  "results_pending" (count)}`.
- schedule file: `{"origin", "target", "id", "agent", "agent_spec": {"backend", "model", "effort", "mode", "data",
  "stall_s"}, "message", "every_s" | ("at", "days"), "catch_up", "overlap", "max_runtime_s", "job", "enabled",
  "updated" (epoch)}`.
- result file: `{"origin", "target", "id", "due", "job", "status" (done|failed|...), "exit", "reply" (first 20000
  chars), "error", "verify" (if any), "started", "finished", "reported" (epoch)}`.

## Part 1 — relay core (owner: `glm-relay`)

Functions (module-level, names fixed — part 2 calls them):
- `relay_cfg()` → dict with `~` expanded paths, or `None` when `relay.json` is missing.
- `relay_root(cfg)` → Path of the mailbox (checkout for git, `url` for dir).
- `relay_publish_schedule(cfg, row, agent_spec)` → writes `schedules/<row["on"]>/<node>--<row["id"]>.json` from a local
  schedule row (fields above). No git ops.
- `relay_unpublish_schedule(cfg, target, sid)` → deletes that file if present. No git ops.
- `relay_sync(cfg, now=None)` → summary dict `{"pulled": bool, "materialized": [ids], "removed": [ids],
  "reported": [result files], "ingested": [result files], "pushed": bool, "error": str|None}`. Never raises.
  1. git: `git -C checkout pull --rebase --quiet` (clone first if missing: `git clone --quiet url checkout`). dir: nothing.
  2. **Target side**: for each `schedules/<node>/*.json` → upsert a row in `cfg.state`'s `schedules.json` with
     id `<origin>--<id>`, agent `relay-<origin>-<agent>`, copying message/timing/overlap/catch_up/max_runtime_s/job/enabled;
     recompute `next_due` only when timing changed or the row is new; keep runs/last_* counters. Create the agent if
     missing (`--backend/--model/--effort/--data/--stall-s` from agent_spec, `--dir <workdir>/<origin>` mkdir'd,
     mode = agent_spec.mode clamped to `max_mode`) using the same internals `agents add` uses; privacy guard applies
     (a refused model = row with `last_status: "error: ..."`, not a crash). Rows with `relay_origin` set whose file is
     gone → removed. Use `_sched_flock`/`_sched_load`/`_sched_save`; register the state dir (`_sched_reg_set`).
     Mark materialised rows `relay_origin: <origin>`, `relay_id: <id>`.
  3. **Target side, report**: for rows with `relay_origin` whose `last_job` is finished (`_wake_read_job` status not
     queued/running) and `relay_reported != last_job` → write the result file (reply from the job / chat log last agent
     reply), set `relay_reported = last_job`.
  4. **Origin side, ingest**: for each `results/<node>/*.json` not yet in `<ag_cache_home()>/relay-ingested.json`
     → copy to `<origin state dir>/relay/results/` (origin state dir = the `sdir` recorded on the local schedule row,
     see part 2, falling back to `ag_config_home()/relay-results/`), `_event(sdir, "relay_result", None, id=..., status=...)`,
     update the local row (`runs`, `last_due`, `last_status = "remote " + status`), mark ingested. Delete ingested result
     files from the mailbox after 7 days (origin owns cleanup of `results/<node>/`).
  5. Write own heartbeat. git: `add -A`, commit (author `ag-relay <node>@ag-relay`, message `relay: <node> sync`) only
     if something changed, `push`; on rejection `pull --rebase` + push, up to 3 tries. dir: nothing.
CLI (`ag relay ...`, all `--json`):
- `relay init --git URL | --dir PATH [--node NAME] [--state DIR] [--workdir DIR] [--max-mode M]` → writes relay.json,
  clones (git), writes the first heartbeat (no push needed for dir).
- `relay sync` → runs `relay_sync`, prints the summary.
- `relay status` → this node + every `nodes/*.json` (age of last_seen, ag commit, backends), schedules sent
  (`schedules/*/<node>--*`), schedules received (`schedules/<node>/*`), last 5 ingested results. This is the "is it in
  sync?" view: flag a peer whose `ag_commit` differs from ours, and a peer not seen for > 2x its schedule interval or 1 h.
- `relay results [--id ID] [--limit N] [--last]` → ingested results, newest first; `--last` = reply text of the newest.
Tests: `tests/test_ag_relay.py` — two fake nodes (separate AG_CONFIG_HOME/AG_HOME/AG_CACHE_HOME) sharing a dir mailbox
and a bare git repo; publish → sync on target materialises row + agent (echo backend) → fire via `ag schedule tick`
→ sync reports → sync on origin ingests; unpublish removes the row; max_mode clamp; bad JSON file skipped with error.

## Part 2 — schedule integration + docs (owner: `codex-relay`)

- `schedule add AGENT ... --on NODE`: needs `relay_cfg()` (else error `relay not set up: ag relay init --git URL`);
  NODE != own node. Row stored locally with `on: NODE`, `sdir` recorded, `agent_spec` from the local agent (backend,
  model, effort, mode, data, stall_s); then `relay_publish_schedule`. Local tick **never fires** rows with `on`.
- `schedule rm|pause|resume ID` on an `on` row → unpublish / republish (enabled false/true). `schedule run ID` on an
  `on` row → error `runs on NODE; use its schedule or wait for the next due` (v1).
- `schedule list` shows `@NODE` in the spec column and `remote <status>` from ingested results.
- `schedule tick --all`: if `relay_cfg()` exists, call `relay_sync` before ticking (pull new work) and again after
  (report results + push). Failures go into the tick output's `relay_error`, never abort the tick.
- Docs: README section "Relay: run schedules on another machine" (setup on both machines, private repo, VM needs git
  credentials for it, `max_mode`, data note: prompts and replies are stored in that repo) + skill templates.
- Tests: `tests/test_ag_relay_sched.py` with stub `relay_*` functions monkeypatched where part 1 isn't merged yet, plus
  doc coverage. Keep part 1's function signatures exactly.

## Part 3 — one-shot cloud runs + stale-session fix (2026-10-23, owner `codex-ag`)
- Bug: `agents rm NAME` leaves `chats/NAME.meta.json` (opencode `sid`/`sids`); re-adding NAME resumes the old session.
  Fix: `agents rm` removes the session ids from the meta (keep the chat log file unless `--purge`), and `agents add`
  never inherits a stale sid. Add `agents reset NAME` = drop session ids (fresh session next turn), keep config/log.
- `wake AGENT "msg" --on NODE`: one-shot remote run via the relay: publish a schedule file with `"once": true` and
  `"at_epoch": now`; the target materialises it, fires it on its next tick, reports the result, and removes the row
  after reporting; the origin removes the mailbox file after ingest. `wakes` shows it as `remote@NODE` until the result
  arrives; `relay results --id` shows the reply. Tests with the dir mailbox (two fake nodes) as in test_ag_relay.py.
- Docs: README + skills (ag-agents: "run an agent in the cloud: `wake A \"…\" --on muse`").
