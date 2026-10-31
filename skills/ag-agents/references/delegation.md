# Delegation

## Contents
- Which command
- Examples
- Budgets and failure behavior
- Verify and escalate

## Which command

| Need | Command |
|---|---|
| One headless turn | `chat send <agent> "<task>"` then `chat log <agent>` |
| Orchestrator assigns, logged in its chat | `delegate <agent> "<task>"` |
| Second opinion: same task to 2+ agents, compared | `crosscheck <a> <b> "<task>" [--more <c>] [--judge <j>] [--timeout S]` |
| Background, returns now | `wake <agent> "<task>"` then `wakes`, `events --limit 20`, `chat log` |
| Follow-up when a session ends | `spawn --wake <agent> --wake-message "<task>" -- <cmd>` (once) |
| opencode native command | `chat send oc --command review "path"` (opencode only) |
| Check the work, not the self-report | `chat send|delegate|wake <agent> "<task>" --verify "<cmd>" [--verify-timeout S]` |
| Cheap first, escalate on failed check | `chat send <agent> "<task>" --verify "<cmd>" --escalate <m2>,<m3>` |
| Notify when a turn finishes | Add `--notify-done` to `chat send`, `delegate`, or `wake`; `schedule add` can notify after every run |

## Examples

```sh
{{AG}} --dir <state> chat send oc "implement X per <spec>; report files changed" --job implement
{{AG}} --dir <state> chat log oc                        # last rows (role: text); use --json for rows
{{AG}} --dir <state> chat log oc --last                 # final agent reply of the last turn, raw text (no role prefix)
{{AG}} --dir <state> chat log oc --tail 50 --no-tools   # --tail N (overrides --limit); --no-tools hides tool/toolout/diff/reasoning
{{AG}} --dir <state> chat send oc "..." --request-id K     # idempotent (also wake/delegate); same payload replays result
{{AG}} --dir <state> receipts gc [--days 7]
```

## Budgets and failure behavior

Tasks must be self-contained: what to do, what done looks like. Fan-out = different agents; two chunks to one agent serialize. `chat send`/`delegate --timeout` (default 1800, 0 = none) is a hard backend budget starting after guard acquired (lock wait unbounded); expiry exits 124 and the chat error row ends `(raise --max-runtime or split the task)`. Prefer `wake` for slow work. `wake --max-runtime` same budget; `wake --timeout` only marks `stalled`; `--notify`/`--on-done` run after. `wake`/`delegate` to an unknown agent fail at call time (`error: no such agent: X`, hint with close names + `ag agents list`); no job file, no spawn. Use `--request-id` on retried calls to avoid double work. Slash text (`/model`, `/profile use docs`, `/help`) runs locally, never reaches the model. `delegate`/`wake`/`chat send --job` only record the job; they never switch an agent.

`delegate` also takes `--parent`, `--group`, `--quiet` (cohorts, see `wakes-cohorts.md`), `--verify`, `--request-id`, `--force`.

## Verify and escalate

Agent self-reports lie: `--verify "<cmd>"` (`chat send`/`delegate`/`wake`, `--verify-timeout S` default 600) makes ag run your trusted command via `sh -c` in the agent's workdir after the turn and record `verify: {cmd, exit, tail (last 40 lines)}` in the turn result / wake job (`--json`), a `[verify] exit=N` chat-log row, and `[verify exit=N]` in the cohort summary. The verify exit is separate from the backend exit; timeout = exit 124. The command comes from the orchestrator, never from the agent's reply. Prefer real checks: `--verify "python3 -m unittest tests.test_x"`, `--verify "node --check x.js"`.

`chat send ... --verify CMD --escalate m2,m3` retries a failed attempt on the same session with each next model (one-turn override, roster unchanged) until verify passes; `--json` adds `escalation: [{model, exit, verify_exit}]`. Needs `--verify`. Same on background wakes: `wake <agent> "<task>" --verify "<cmd>" --escalate <m2>,<m3>` (job gets `escalation` + last `verify`); not for `delegate` yet.
