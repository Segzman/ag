# AGENT_CONTEXT — scoped guidance + persistent memory

Custom Markdown guidance and persistent scoped agent memory for `ag`.
New agents get up to speed on their assigned part: project `custom.md`
chain + explicit `.md` files + shared/scope/per-agent memory, injected
into every `run_turn` prompt (fresh and resumed, `chat send`/`delegate`/`wake`).

## Stable read-only helper (for UI integration)

```python
get_agent_context(sdir, agent_name) -> {
  "text": str,        # assembled labeled context, "" when unassigned/nothing found
  "sources": [        # one entry per attempted source, in assembly order
    {"path": str, "kind": str, "status": str, "bytes": int, "truncated": bool}
    # kind: custom-md | file | shared | handoff | brief | checkpoint
    # status: ok | missing | unreadable | oversized | skipped-symlink | outside-root | bad-type
  ],
  "warnings": [str],  # human-readable lines for every non-ok source + truncation notes
  "assignment": dict, # {} when unassigned, else {project, scope, files, task, acceptance, updated}
}
```

Read-only: never writes. Bounded: `CTX_PER_FILE=6000` chars per source
body, `CTX_TOTAL=18000` chars total **including labels**. Reads are
byte-bounded (at most ~4x the char cap is ever pulled from disk).
Guidance (custom.md chain + explicit files) is capped to
`CTX_TOTAL-CTX_RESERVE` (`CTX_RESERVE=4000`); the assignment block
(task/acceptance) plus memory always keep their reserve, so long guidance
can never evict them. Over-cap sources truncate (flagged) and assembly
stops at the cap (flagged). Used by `run_turn` and `ag context show`.
UI owners: call this, do not reimplement assembly.

## CLI

```sh
ag context init                                        # non-destructive scaffold (state dirs + empty records)
ag context assign NAME --scope S --task T --acceptance AC \
  --file f.md --brief-file b.md [--project-root P]     # project root defaults to agent workdir
ag context assign NAME --scope S --brief "..." --task T
ag context assign NAME --scope S --brief "" --task ""   # explicit "" clears brief/task
ag context show NAME [--json]                          # assembled text + sources/warnings
ag context checkpoint NAME --file ckpt.md              # curated summary -> per-agent + shared scope handoff
ag context checkpoint NAME "goal ... done ... next ..."
agents add NAME --backend echo --instructions a.md --instructions b.md   # repeatable, stored on agent
agents set NAME --instructions a.md                    # replace list; --instructions "" clears
```

`assign` records task/scope/files/acceptance; the brief is per-agent.
Task/acceptance are assembled into every prompt as `[context: assignment]`,
even when no brief file exists. Unassigned agents still receive root
`custom.md` + their explicit `instructions` under their workdir (scope
`""`), flagged with an `unassigned` warning. A new agent assigned the same
project+scope receives the prior shared scope handoff plus its own brief.
`checkpoint` writes the per-agent `CHECKPOINT.md` and refreshes the shared
scope `HANDOFF.md` (attributed). Checkpoints are curated summaries, never
transcript dumps.

## Reassign + lifecycle

Re-assigning the same agent to the same project+scope without flags keeps
the current values (task, acceptance, files, brief all preserved). Passing
a flag sets it; passing it explicitly empty clears it:

```sh
ag context assign ui-a --scope ui --task "panel" --brief "cover chat"
ag context assign ui-a --scope ui            # no-op: everything preserved
ag context assign ui-a --scope ui --task ""  # clears task only
ag context assign ui-a --scope ui --brief "" # deletes this scope's BRIEF.md
```

Moving to a new project or scope starts fresh: the old task/brief are
never carried over (the new scope gets the shared handoff, if any, plus
whatever flags are passed). `context init` is non-destructive and holds
the same state lock as `assign`, so concurrent scaffolding can't clobber
records. `agents rm NAME` removes the agent's assignment record (under
lock) but keeps shared project/scope memory (`SHARED.md`, `HANDOFF.md`),
so a later agent reusing the name starts unassigned and can never inherit
stale task/scope from the removed agent.

## Workflow (copy-paste)

```sh
export AGENT_CLI_DIR=/tmp/demo-state
ag context init
ag agents add ui-a --backend echo --dir /tmp/demo-proj
mkdir -p /tmp/demo-proj/ui && echo "# UI rules" > /tmp/demo-proj/custom.md
ag context assign ui-a --scope ui --task "own chat panel" --acceptance "tests pass" --brief-file brief-a.md
ag context checkpoint ui-a --file ckpt-a.md
ag agents add ui-b --backend echo --dir /tmp/demo-proj
ag context assign ui-b --scope ui --task "continue chat panel" --brief-file brief-b.md
ag context show ui-b        # contains A's handoff + B's brief, not sibling scopes
ag chat send ui-b "status"  # echo backend replays prompt incl. refreshed guidance
```

## State paths (under explicit sdir, never home/global)

- `context/assignments.json` — `{agent: {project, scope, files, task, acceptance, updated}}`
- `context/projects.json` — `{projkey: canonical-project-root}`
- `context/p/<projkey>/SHARED.md` — project shared notes (manual, future)
- `context/p/<projkey>/scope/<scope-path>/HANDOFF.md` — shared scope handoff (scope-path omitted at root)
- `context/p/<projkey>/scope/<scope-path>/agents/<agentkey>/BRIEF.md`
- `context/p/<projkey>/scope/<scope-path>/.../agents/<agentkey>/CHECKPOINT.md`

`projkey = sha1(canonical project root)[:12]`.
`agentkey = sanitized-stem[:40] + "-" + sha1(agent name)[:10]`, so distinct
names never collide (truncation can't merge them). Root scope (`""`)
uses the project dir itself (no `scope/` segment).

`assignments.json`/`projects.json` read-modify-write holds an flock state
lock (`context/.lock`, POSIX; best-effort elsewhere), so concurrent
workers can't lose each other's records. `assign` writes the brief first:
a failed brief write commits no assignment (no partial state).

## Guidance sources (read-only, project tree only)

1. `custom.md` chain: `<root>/custom.md`, then each ancestor prefix of
   scope down to `<root>/<scope>/custom.md`. Missing files skipped.
2. Explicit files: agent `instructions` (via `agents add/set
   --instructions`, repeatable) + assignment `files` (via
   `context assign --file`, repeatable). Must be `.md`, inside project
   root, not symlinks. Nothing else is ingested: never every-Markdown,
   never home/global/private data. No secret scanning, no shell
   execution from files.
3. Memory (labeled fallible notes, may be stale): `SHARED.md`,
   `HANDOFF.md` (prior agents' shared handoff), own `BRIEF.md`, own
   `CHECKPOINT.md`. Other agents' briefs/checkpoints are never included.

`run_turn` prepends the assembled block (labeled sources, memory marked
fallible, current request delimited last and prominent). Native
`--command` payloads are sent bare (context skipped). Slash turns are
local (no context). Assembled context is never appended to chat history
(raw user text only).

## Validation / safety

- Scope: `""` (root) or `a/b` segments of `[A-Za-z0-9_.-]+`; no `..`,
  no `.`, no empty segments, no backslash. Leading `/` is rejected
  (absolute scopes are never silently relativized). Agent names: existing
  roster entry.
- Reads: per-file + total caps; unreadable/missing/oversized reported in
  `show` sources/warnings, never fatal. Symlink leaf **or any symlink
  ancestor up to the project root / state dir** is refused
  (`skipped-symlink`); resolved targets escaping the root are refused
  (`outside-root`). Non-`.md` explicit files rejected.
- Writes (`assign` brief, `checkpoint`): atomic (tmp+rename); symlink
  leaf/ancestors refused; resolved escapes from the state dir refused.

## Native TUI limitation (honest)

Headless turns (`chat send`, `delegate`, wake workers) all run through
`run_turn` and receive context. Native TUI sessions (`handoff --exec`,
TUI `H`, `/handoff`) exec the backend directly with scoped profile env
only: assembled context is NOT injected there and no global config is
rewritten. In a native session, run `ag context show NAME` (or read the
scope files above) to pull context manually.

## Limits

- Memory is file-backed Markdown, no pruning/rotation; `wake/jobs`
  unaffected. History cap (4000 chars) unchanged.
- Resumed stateful turns (`claude`/`opencode`/`cursor` with SID) still
  get fresh context prepended to the new user message only; old turns
  are not rewritten.
- Concurrent `checkpoint` on one scope: last write wins for `HANDOFF.md`
  (per-agent files never clash).
- Auto compaction (`docs/AUTO_COMPACTION.md`, default off) summarizes old
  turns into a pinned `summary` row; guidance, task/acceptance, and memory
  above are re-injected every turn and never compacted away.

## Implementation brief (persistent)

Goal: scoped guidance + memory per spec above, small exact patches in
`ag` (context helpers near `eff_system`, `run_turn` hook, parser +
`do_context`), plus `tests/test_ag_context.py`, this doc, root
`custom.md`, scoped examples under `docs/examples/` and templates under
`docs/templates/`. Constraints: preserve uncommitted work, no commits;
never touch ui-builder blocks (themes/render/TUI/`KEYS_HELP`,
`tests/test_ag_ui.py`, README TUI sections). Caps: 6000/file, 18000
total. No paid models in tests (echo backend + module import).

## Final handoff (persistent)

Done: `get_agent_context` + `context init|assign|show|checkpoint` +
`agents add/set --instructions` + `run_turn` injection (fresh+resumed,
`--command` bare, history unpolluted) + atomic symlink-safe writes +
`tests/test_ag_context.py` (same-scope inheritance, sibling/project
isolation, resumed refresh, command untouched, nesting, explicit files,
invalid paths/symlinks, truncation, init preservation, persistence).
See `docs/examples/` + `docs/templates/`. Limitation: native TUI pulls
manually via `context show`; no global writes. UI: use
`get_agent_context(sdir, name)` read-only.

## Review fixes (parent findings, applied)

1. Task/acceptance are assembled as `[context: assignment]` on every turn,
   brief or not.
2. Unassigned agents get root `custom.md` + explicit `instructions` under
   the workdir by default (warning flags the missing assignment).
3. Symlink ancestors confined on reads and writes; absolute scopes
   rejected; agent state keys hash-suffixed against collisions.
4. Byte-bounded reads; label costs counted; guidance capped to
   total-reserve so task/handoff survive.
5. State lock around assignment/project writes; brief written before the
   record commits, so failures leave no partial assign.
