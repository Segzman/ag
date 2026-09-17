# STREAMING — honest incremental coding-agent timeline (backend contract)

Live backend events reach the UI **before process exit** through ordered
`on_event(name, "timeline", row)` callbacks, persisted with the same
`event_id` so the live view equals the reloaded view. No invented deltas,
no fake status, no replay contamination.

Skill files read (applied decisions): `SKILL.md` (state/update separated
from rendering via `on_event` UPSERT contract; no blocking I/O in the
render path — backend persists then emits, UI only upserts),
`references/ecosystem-python.md` (stdlib only: `subprocess` line
iteration, `threading` stderr drain, `difflib` for diffs; no new deps),
`references/visual-patterns.md` (status signals are data rows with
text+status, never color-only; omission labels are explicit text),
`references/interaction-patterns.md` (no reserved-key/global-pid
machinery added; no cancellation API — turn ownership stays in
`run_turn`, kill paths untouched).

## Shared callback contract (backend + UI/slash-v2)

```python
on_event(name, "timeline", row)   # row: dict, UPSERT by event_id
# row = {"ts": iso, "event_id": str, "role": str, "text": str,
#        "tool_name": str?, "tool_id": str?, "status": str?,
#        "path": str?, "timeline_summary": bool?}
```

- **UPSERT, never concatenate.** UI replaces the stored snapshot for
  `event_id` and renders the latest text. Deltas are concatenated
  backend-side (raw, no inserted newlines mid-token); each callback
  carries the full snapshot text so far.
- **Roles.** Stream-visible: `assistant` (provider text segments),
  `reasoning`, `tool`, `toolout`, `diff`, `error`. Pre-existing rows
  (`user`, `agent`, `tool`, `toolout`, `delegate`, `summary`) keep
  working. Legacy `on_event` kinds (`text`, `tool`, `toolout`, `done`,
  `exit`) are still fired unchanged.
- **User row.** Persisted first with a stable `event_id`, then emitted
  as a `timeline` row (`role=user`). UI should upsert it by `event_id`
  (dedups against any optimistic append); this is the documented
  mechanism — no separate optimistic path needed.
- **Aggregate reply.** The final `role=agent` row is still appended for
  replay/compaction/CLI. When assistant timeline rows were emitted this
  turn it carries `timeline_summary=true`: UI **hides** it (duplicate).
  With no assistant segments (legacy/failures) the flag is absent and
  the aggregate still displays.
- **Exit reload is source of truth.** Every timeline emission is
  persisted *before* the callback; at turn end final snapshots are
  re-emitted, so live view == `load_timeline()` view.
- **Reload dedup.** The JSONL log is append-only; one `event_id` may
  have several snapshot rows. `load_timeline(sdir, name)` returns rows
  in first-seen order with last-wins text. UI log ingestion must do the
  same (replace by `event_id`, keep position of first sighting).
- **Replay/compaction exclusion.** `recent_history()` and all
  compaction accounting select only `user`/`agent`/`summary`
  (unchanged filters): display-only `assistant`/`reasoning`/`tool`/
  `toolout`/`diff`/`error` rows are never resent to backends and never
  duplicated into compaction prompts. Summary pinning unchanged.
- **No cancellation API added.** `run_turn` owns the child process
  locally; no global pid registry, no cross-process signals. TUI kill
  paths untouched.

## Row semantics

- `tool`: one row per provider tool invocation, keyed by provider
  `tool_id` when supplied (state updates rewrite the same `event_id`;
  no duplicate rows). `status` is set **only when the provider
  supplies it** (`completed`/`failed`/`error`; opencode JSON emits
  tools only at `completed`/`error`). No `pending`/`running`
  invention: a bare invocation has no `status` key.
- `toolout`: result body, separate row, linked by `tool_id` when
  known. A failed result (`is_error` / `status=error`) also yields a
  `tool` status of `failed` plus an `error` row — failures are
  recorded even when prose already streamed; success is never faked.
- `diff`: only from provider-structured sources, never a worktree scan
  (stdlib `difflib.unified_diff` for input-derived diffs):
  1. provider-confirmed metadata first — opencode `state.metadata.diff` /
     `state.metadata.filediff{file,patch}` (`edit.ts`) and
     `metadata.files[{relativePath,patch}]` (`apply_patch.ts`), rendered
     verbatim as `applied (provider-confirmed)`;
  2. input fallback — edit `oldString`/`newString`/`filePath` (camelCase
     per `edit.ts` Parameters; snake_case aliases accepted) and
     `apply_patch` `patchText`, rendered as
     `proposal (input-derived, unconfirmed)` until the provider reports
     success, then transitioned to `applied (provider-confirmed)` —
     or `failed (provider-reported, unapplied)` on error results.
  Status transitions **upsert the same `event_id`** (one diff row per
  tool in the reloaded view); snapshot rewrites share the id. When a
  `toolout` repeats the same patch span, the span is replaced with
  `[patch shown in diff row]`.
- `error`: provider/tool failures and turn failures (non-zero exit
  tail). Opencode `state.status=error` with `state.error` and no
  `output` surfaces the error body as the tool result (plus an error
  row) instead of silence. Unknown event JSON is **dropped** — never
  rendered as giant assistant prose and never echoed into the
  canonical reply.
- **Bounds (central `tl_bound`, never silent).** Tool result bodies
  cap at 600 chars, persisted rows at 8000 chars (same as existing
  rows), in-memory segments at 20000 chars; every cut appends an
  explicit `…[truncated: +N chars omitted]` / `…[output capped at N
  chars]` label. Diff signs/paths are preserved inside the kept span.
- **Cadence.** First nonempty delta persists+emits immediately
  (pre-exit visibility); later snapshots persist throttled — every
  2000 new chars **or** ~80ms since the last emit — plus on
  `content_block_stop` / message boundaries, segment close, and turn
  end. Short responses are never buffered until exit. Segment ids are
  scoped by provider message id (or a per-turn counter fallback), so
  index reuse across messages never merges unrelated segments.
  Parser message scope is per-turn state owned by `TimelineBuffer`
  and threaded through `parse_timeline(..., scope=...)` — no shared
  parser globals, so concurrent turns in one process never corrupt
  each other. Full content ops carry the exact provider message scope
  plus content-block index; the buffer adopts that exact pending
  segment (text updated in place on the stable row, pending segment
  then closed/removed). Identity always wins over content matching,
  so identical prefixes or repeated message text can never merge; a
  `message_start` without an id clears any stale id. Without identity
  (other providers, legacy callers) the text joins the run segment,
  with a lone-open-delta prefix adoption as fallback.
- **Persistence honesty.** Every emission persists before the
  callback; a disk failure sets an in-memory `persist_failed` flag
  (rows marked `persisted:false`) and the turn returns
  `persist_error:true` with a best-effort timeline note — never an
  apparent durable success. The in-memory reply is preserved. Guards
  (`mark_running`, turn lock) clean up on all paths. Segments
  finalize **before** the canonical reply is built from them.
  `load_timeline` dedups by `event_id` (last-wins, first-seen order)
  and **preserves legacy rows** without ids; compaction/replay
  accounting dedups before windowing, so snapshot rewrites never
  inflate turn counts while the raw log retains full history.

## Backend fidelity (verified, not invented)

| Backend | Stream granularity | Reasoning | Tool status |
|---|---|---|---|
| opencode (`run --format json --thinking`) | block: `text`/`reasoning` per finished part, `tool_use` at completed/error | yes (`reasoning` event; needs `--thinking`, off by default) | `completed`/`failed` only; **no running** |
| claude (`-p --verbose --output-format stream-json --include-partial-messages`) | token deltas (`stream_event`/`content_block_delta`) + full blocks; `content_block_stop`/message markers flush pending deltas | yes (`thinking` blocks + `thinking_delta`) | invocation (no status key) → `tool_result` outcome (`completed`/`failed`); parallel tools tracked per index, full blocks linked by tool id |
| cursor (`-p --output-format stream-json --stream-partial-output`) | partial text deltas + blocks (shape not pinned: generic walk) | provider-dependent, generic | generic |
| codex (`exec`, no `--json`) | line-level text → assistant rows | no (honest degrade) | generic walk |
| gemini (`-p -o stream-json`) | JSON lines via generic walk | provider-dependent, generic | generic |
| echo / plain text | one assistant row per line | no | n/a |

Verified sources (2026-09-15): installed `opencode 1.18.23`
`run --help` (`--thinking`, `--format json`); `run.ts` JSON loop
(`text`/`reasoning` on `part.time.end`, `tool_use` only at
`completed`/`error`, no idle/end marker — exit is the end);
installed `claude --help` (`--include-partial-messages`,
`--output-format stream-json`); installed `agent --help`
(`--stream-partial-output`); `gemini --help` (`-o stream-json`);
`codex exec --help` (`--json` exists but schema unpinned, so not
used). Prior note claiming "no partial-message flag" for Claude was
stale — the installed CLI has it and it is used. Nothing above is
assumed: unknown JSON shapes are ignored, never displayed.

`--command` (opencode native) turns stay bare: no context/history
transform, no timeline beyond the legacy path (unchanged).

## Backend argv changes

- opencode: `+ --thinking` (reasoning otherwise never emitted).
- claude: `+ --include-partial-messages` (token deltas; full blocks
  still parsed, so turning it off degrades to block streaming).
- cursor: `+ --stream-partial-output`.
- Model/profile/session-id/resume handling unchanged; fresh and
  resumed builders keep existing contracts.

## Test results

`tests/test_ag_streaming.py` (94 checks; fake NDJSON executable with delays;
no paid models): pre-exit callback visibility, ordered
tool/assistant interleaving, delta concatenation without mid-token
newlines, short-delta pre-exit visibility (5-char delta while the child
still sleeps), tool update dedup by `event_id`, Claude parallel tools
with repeated block indices across messages, concurrent/interleaved
Claude streams with per-turn scopes (plus threaded racing turns),
identity-based full-block adoption (identical prefixes, repeated
message text, stale-id clearing, reasoning+assistant same message,
out-of-order cross-turn full blocks), real opencode schemas
(camelCase inputs, confirmed metadata diffs, `apply_patch` patchText /
metadata.files, `state.error` bodies), diff proposal→applied/failed
transitions upserting one row, error-after-partial-text, reload
equivalence (`load_timeline` == final snapshots), bounded
long-output omission labels (600 toolout / 8000 row), unknown-JSON drop
from replies, legacy row preservation, persistence-failure honesty,
echo coalescing with stable turn accounting, legacy `parse_events`/
callback compat, `recent_history`/compaction exclusion. Run with
`python3 tests/test_ag_streaming.py` plus context/compaction/native
suites for regressions.
