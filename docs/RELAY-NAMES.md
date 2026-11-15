# Relay names: one conversation per session

Problem: every local session (Claude Code, Codex, ...) sees every inbound relay message from a
peer (e.g. Muse on the VM) and they all talk to the peer through the same agent, so sessions
step on each other. A **relay name** gives one session a private mailbox on the relay.

Protocol (no wire change): the relay already routes by agent name. A session that claims
name `coop` sends with local echo agent `coop` (`ag wake coop --on muse`), and the peer replies
by addressing agent `coop` (or `chat-coop`) on this node. Inbound messages are the pending
files `schedules/<this node>/<peer>--<id>.json` in the relay checkout (fields: `origin`,
`target`, `id`, `agent`, `message`, `at_epoch`).

## Commands

- `ag relay name claim NAME [--session ID] [--default] [--force]`
  - NAME matches `^[a-z0-9][a-z0-9-]{1,39}$`.
  - State: `ag_cache_home()/relay/names.json` =
    `{"names": {NAME: {"session": ID or "", "claimed_at": epoch, "default": bool}}, "cursors": {NAME: [ids]}}`
    (atomic write).
  - Held by another non-empty session → error (exit 1) unless `--force`. Re-claim by the same
    session is fine. One session holds at most one name: claiming a new one releases its old one.
  - `--default`: this name also receives *unaddressed* messages (agent not matching any claimed
    name). At most one default; setting it moves the flag.
  - Creates local echo agent NAME in `--dir` state if missing, so `ag wake NAME --on PEER` works.
- `ag relay name release [NAME] [--session ID]` (NAME defaults to the session's claim).
- `ag relay name list` → table (json: the `names` dict).
- `ag relay inbox [--name NAME | --session ID] [--peek] [--hook claude-inject] [--all] [--since HOURS]`
  - Resolve NAME from `--name`, else from the session's claim. No name → print nothing, exit 0
    (if unaddressed messages exist and no default owner, print one line saying how many and
    `ag relay name claim NAME --default`).
  - Addressed to NAME: `agent == NAME` or `agent == "chat-" + NAME`. Unaddressed messages go
    to the default owner only, and only those with `at_epoch >= claimed_at`.
  - Unseen = id not in `cursors[NAME]` (keep the last 500 ids). `--peek` does not advance.
  - Persistent log: each sync appends inbound messages to `ag_cache_home()/relay/inbox/<name|_unaddressed>.jsonl`
    (rows `{"id", "origin", "agent", "message", "at_epoch", "seen": false}`, dedupe by id, capped at the
    last 5000 rows; appends under the names lock). The inbox reads pending files *and* the log (this
    name's bucket plus every bucket no claimed name owns), so a message whose pending file was deleted
    still shows. Consuming the inbox marks the matching log rows `seen: true`; `--all` includes seen
    rows, `--since HOURS` drops messages older than that.
  - Text output: one header line, then per message `- [agent] id: <first 600 chars, whitespace collapsed>`
    and a last line `reply: ag relay say "<text>"`. `--json`: list of full message dicts.
  - `--hook claude-inject`: print `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":<text>}}`
    when there is something, else nothing.
- `ag relay say MESSAGE [--name NAME | --session ID] [--peer PEER]`
  - Same as `ag wake NAME --on PEER --notify-done "[from NAME] MESSAGE"`.
  - PEER default: the single other node under `nodes/` in the relay root, else error asking for `--peer`.

## Done means

- Code in `ag` next to the other relay commands, help text on every new subcommand/flag.
- `tests/test_ag_relay_names.py` (reuse the `Env` helpers in `tests/test_ag_relay.py`): claim
  conflict + `--force`, one name per session, default routing of unaddressed messages,
  addressed filtering (`NAME` and `chat-NAME`), cursor advance vs `--peek`, hook JSON shape,
  `say` publishes a schedule for agent NAME to the peer.
- README relay section and `skills/ag-agents/references/schedule-relay.md` document the commands
  (`tests/test_ag_doc_coverage.py` must pass).
- `python3 -m pytest -q tests/test_ag_relay_names.py tests/test_ag_relay.py tests/test_ag_doc_coverage.py` passes.
