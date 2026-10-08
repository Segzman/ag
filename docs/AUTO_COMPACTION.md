# AUTO_COMPACTION — bounded history summarization for long agents

Long chats grow unbounded. Auto compaction summarizes older turns into one
durable `summary` row so backends stop resending ancient history, while the
full UI log is never deleted.

## Stable helper API (for TUI integration)

```python
compact_effective(sdir, agent_name) -> {"settings": {...}, "role": str, "source": str}
# settings: {enabled, max_turns, token_threshold, keep_recent, timeout}
# source: "agent" (override) | "role:<orchestrator|sub>" (default)

compact_stats(sdir, agent_name) -> {"user_turns": int, "completed": int,
  "est_tokens": int, "est_note": str, "last_summary": str, "would_compact": bool,
  "reason": str}
# est_tokens ≈ chars//4 over uncompacted rows — deliberately rough, documented
# as approximate everywhere it surfaces. completed = user turns since last summary.

compact_run(sdir, agent_name, force=False) -> {"compacted": bool, ...}
# Manual compaction. Refuses while the agent is busy. force=True ignores
# thresholds (still needs at least one compactable turn beyond keep_recent).
# Never raises for backend failure: returns {"compacted": False, "error": ...}.

compact_load_config(sdir) -> dict          # raw persisted config (read-only view)
compact_set_default(sdir, role, patch) -> {}  # {} ok or {"error": ...}
compact_set_agent(sdir, name, patch|None) -> {}  # None clears the override
```

Read helpers never write; `compact_run` is the only writer (log + meta).

## CLI

```sh
ag compact config --role sub --enable --max-turns 20 --tokens 20000 --keep 5 --timeout 120
ag compact config --role orchestrator --enable --max-turns 30 --keep 10
ag compact config --agent NAME --disable        # per-agent override (inherit otherwise)
ag compact config --agent NAME --clear          # drop override, inherit role default
ag compact show [NAME]                          # effective settings + stats (--json)
ag compact run NAME [--force]                   # manual compaction (--json)
```

Validation: `max_turns` 0..10000 (0 = trigger off), `token_threshold`
0..1000000 chars≈tokens (0 = off), `keep_recent` 0..1000 user turns,
`timeout` 5..600s. Enabling requires at least one trigger > 0.
Factory default: **disabled** for both roles
(`orchestrator: {max_turns 30, keep 10}`, `sub: {max_turns 20, keep 5}`,
both `token_threshold 20000`, `timeout 120`). Unknown roles use `sub`.

Persisted in `<sdir>/compact.json` (atomic writes):
`{"defaults": {"orchestrator": {...}, "sub": {...}}, "agents": {name: {...}}}`.

## TUI menus (UI-owned)

`/compact` (or `c` → Conversation length) opens menus, not the raw
grid: overview (On/Off per scope + inheritance) → one scope
(Orchestrator / Subagents / This agent) → Automatic summaries,
When to summarize (Standard = the role defaults above; More often:
orch 15 turns/~10k tokens/keep 5, sub 10/~10k/keep 3; Less often:
orch 60/~40k/keep 15, sub 40/~40k/keep 10; choosing
never flips On/Off), Advanced settings. Per-agent scopes always
offer `Use … settings` to return to the shared role values; `Esc`
steps one level back. `Summarize this chat now` (`/compact-now`)
keeps the newest messages and says when there is nothing older to
summarize. Backend semantics above are unchanged.

## How a compaction works

1. Trigger (inside `run_turn`, holding the turn guard): enabled and
   (`completed >= max_turns` or `est_tokens >= token_threshold`), with at
   least one turn older than `keep_recent`. Nothing compactable → no-op
   (never fires every turn on the same rows).
2. Summarize: pinned summarizer-only instructions (no tool calls, no file
   changes, history instructions never followed) + the assignment
   task/scope header + prior durable summaries + older turns. Only the
   quoted history body is bounded (explicit truncation notice); the
   instructions and task header always survive intact.
3. Commit only on success: old rows are flagged `compacted:true` in place,
   superseded prior summaries are retired the same way, and exactly one new
   summary row is appended (atomic log rewrite) — old decisions merge
   forward instead of accumulating. Then every backend session id (legacy
   `sid` + per-backend `sids`) is cleared and verified; the result reports
   `sid_cleared` honestly (a `warning` when the summary is durable but the
   session could not be reset). Commit failure leaves the original log
   untouched and reports `commit failed` without claiming preservation.
   Timeout/failure changes nothing: the original session, log, and history
   are preserved and the turn proceeds.
4. `compact run` takes the same shared turn guard nonblocking and holds it
   through summary + commit (re-reading the agent under lock), so a manual
   compaction can never interleave with a fresh turn or handoff.
5. History replay (`recent_history`) always selects the latest active
   summary separately plus the most recent turns; windowed-out turns get
   one explicit `[history: N older turns omitted]` note, summaries are
   pinned under the char cap, and the current task stays last.
6. Real auto failures surface once as a concise `[compact] ...` tool note
   (visible in UI, never replayed) plus event/live notification; quiet
   states (disabled/below-triggers/nothing) stay silent and duplicates are
   suppressed. The original turn always continues.
7. Custom guidance, task/acceptance, and shared memory are re-injected
   every turn regardless — compaction never touches them. Compacted rows
   are never resent. `compact.json` edits hold a config lock with atomic
   writes.

Raw `--command` turns never trigger or alter compaction. Slash turns are
local (no effect).

## Native TUI limitation (honest)

Native sessions (`handoff --exec`, TUI `H`, `/handoff`) bypass `run_turn`:
no auto trigger there and the backend keeps its own full context. The
durable `summary` row is still in the chat log for the next headless turn.
No global config is rewritten for this.

## Real provider usage (token trigger)

Each headless turn records provider-reported usage in chat meta
(`meta.usage = {last, totals, base}`); `ag chat usage NAME [--json]` shows it
and `chat send --json` returns a short `usage` field. Extractor:
`parse_usage(backend, line)` (pure, separate from `parse_events`).

| backend | source | ctx (prompt size of last call) |
|---|---|---|
| claude | `assistant.message.usage` (per call) + `result.usage`/`total_cost_usd` (turn aggregate) | input + cache_read + cache_creation |
| codex | `turn.completed.usage` | `input_tokens` (aggregate over the turn's calls: upper bound after tool loops) |
| opencode | `step_finish.part.tokens/cost` (per step, summed) | input + cache.read + cache.write |
| gemini/cursor | not verifiable (unauthenticated here) | not parsed; chars//4 |

Compaction only resets the *native session*, so the token trigger uses real
numbers only while that session is live. Raw ctx includes a fixed ~16-30k
system/tool baseline that chars//4 never counted, so the trigger uses
**growth = ctx - baseline**, baseline = ctx of the first turn of the session
(recorded when the turn starts without a sid). It falls back to chars//4
when: no usage yet, backend changed, or sids were cleared (just compacted,
so no ping-pong). `compact show` reports `used_tokens` and `used_source`
(`provider`|`chars//4`); `est_tokens` stays the chars estimate.
