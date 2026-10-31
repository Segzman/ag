# Reliability, rate limits, usage limits

## Retries and resume

Stored session id unknown to backend ("Session not found" etc.) -> ag clears it, replays recent history into a fresh session, relaunches once, logs `[resume] ... started fresh`. Overload/503/529/connection reset/opencode free-tier 403 retried 2x (2s, 6s) only if the attempt produced no output (provider error events do not count). `AG_RETRY=0` disables. Logged-out backend fails fast (probe). codex headless resumes via `exec --json`. gemini is stateless.

## Rate limits

Rate limits (429 / `Rate limit exceeded` / `Endpoint is unavailable` / stall) use shared cooldown + backoff with per-model slots (measured per-model free limits: muse/space-bunny/mimo/big-pickle 1, nemotron 3, longcat/step-5 2; `AG_MAX_FREE_TOTAL` caps all free turns at 10; default fallback chains reroute free agents on 429/endpoint-unavailable; `AG_MAX_CONCURRENT`, `AG_STALL_S`); `agents add|set --fallback MODEL` (repeatable, `""` clears) keeps work moving after 2 hits.

```sh
{{AG}} --dir <state> ratelimit [--json]          # slots in use (pid, agent) + cooldowns
{{AG}} --dir <state> ratelimit clear [KEY]       # drop one/all cooldowns
```

## Usage and spend limits

Usage/spend limits (claude `hit your ... spend limit ... resets 7:40pm (TZ)`, codex `hit your usage limit`, gemini quota) are not retried: result gets `usage_limit: true` + `reset_at`, chat row `[limit] claude usage limit (resets 19:40 TZ)`, shared cooldown per backend (+ `claude_config_dir`) fails later turns fast until reset (`ag ratelimit` shows, `ag ratelimit clear` drops).

Failover:

```sh
{{AG}} --dir <state> agents set w --fallback codex/default      # or BACKEND:MODEL
```

`--fallback BACKEND/MODEL` (or `BACKEND:MODEL`, other backend, e.g. `codex/default`) fails the turn over to that backend with recent history replayed (`[fallback] claude usage limit -> codex/default`); privacy/access/budget re-checked; roster unchanged, primary retried after reset.

## Stall watchdog

Default 240 opencode, 0 others. `agents add|set NAME --stall-s N` (0 = off, `""` clears), per turn `wake|chat send --stall-s N`; kill reports `raise it: ag agents set NAME --stall-s 900`.

## Lost wakes

Worker died: `wakes --reap [--dry-run] [--include-running] [--agent A] [--max-attempts 3]` relaunches; default only jobs lost while queued; `--include-running` redoes mid-turn losses (msg prefixed `[reap]`; verify state first).
