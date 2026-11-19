# Scheduled wakes and relay

## Contents
- Scheduled wakes
- Relay: schedules on another machine
- Remote schedule and one-shot cloud run

## Scheduled wakes

```sh
{{AG}} --dir <state> schedule add w --every 15m "msg" [--job J] [--overlap skip|queue] [--max-runtime S] [--id NAME] [--on NODE] [--notify-done]
{{AG}} --dir <state> schedule add w --at 09:00 --days mon-fri [--tz America/Toronto|+HH:MM] "msg" [--catch-up] [--disabled] [--notify X] [--notify-done] [--on-done CMD]
{{AG}} --dir <state> schedule list [--all] | pause ID | resume ID | run ID | rm ID
```

Needs the global tick installed once (`schedule install --write`, see `ag-cli` `references/schedule-relay-tick.md`); else nothing fires. Local clock (or `--tz` IANA zone / fixed `+HH:MM` offset; `--at --on NODE` carries the origin's zone), min 60s. Missed fixed-time >10min late skipped unless `--catch-up`. Default `--overlap skip`. Double ticks never double-fire.

## Relay: schedules on another machine

Schedules made here can run on another machine (even one accepting no inbound connections). One `relay init` per machine, all pointing at the same mailbox - a **private** git repo, or a folder you sync yourself - then run `relay sync` on every machine (global tick can drive it):

```sh
{{AG}} --dir <state> relay init --git URL | --dir MAILBOX [--node NAME] [--state DIR] [--workdir DIR] [--max-mode ro|edits|auto|full]
{{AG}} --dir <state> relay sync | status        # heartbeat write on init; sync = pull, materialise/report, ingest, push
{{AG}} --dir <state> relay results [--id ID] [--limit N] [--last]
{{AG}} --dir <state> relay admin NODE update|notify-dir PATH|status [--wait S]
{{AG}} --dir <state> relay name claim NAME [--session ID] [--default] [--force] [--pid PID]  # private mailbox per session
{{AG}} --dir <state> relay name release [NAME] [--session ID] | name list
{{AG}} --dir <state> relay inbox [--name NAME | --session ID] [--peek] [--hook claude-inject] [--all] [--since HOURS]
{{AG}} --dir <state> relay say "text" [--name NAME | --session ID] [--peer NODE]
{{AG}} relay alias add NAME --backend claude --dir DIR [--model M] [--mode ro|edits|auto] [--from NODE,NODE] [--resume latest|SID|none] | alias list | alias rm NAME
{{AG}} --dir <state> relay secret put NODE NAME | get NAME | rm NODE NAME  # age-encrypted secrets per node
{{AG}} push [GIT_PUSH_ARGS…]            # git push; signal every relay peer to update; sync
```

Relay names keep local sessions from sharing a mailbox: `relay name claim coop --session ID` stores the claim in `~/.cache/ag/relay/names.json` and creates a local echo agent `coop`, so `wake coop --on PEER` works. Inbound messages addressed to `coop` or `chat-coop` go to that name; `--default` also takes messages whose agent matches no claimed name (at most one default, only from its `claimed_at` onward). One session holds one name; claiming another releases the old one, and another session's name needs `--force`. Every sync appends inbound messages to `~/.cache/ag/relay/inbox/<name|_unaddressed>.jsonl` (dedupe by id, cap 5000 rows), so a session that misses the pending-file window still sees them: `relay inbox` reads the log, marks consumed rows seen, `--all` includes seen rows, `--since HOURS` filters by age (`--peek` leaves everything). `relay say "text"` is `wake NAME --on PEER --notify-done "[from NAME] text"` with `--peer` defaulting to the single other node under `nodes/`.

Relay aliases (human-only `relay alias add`) let a peer start a real chat: `relay say --peer NODE --name NAME "text"` from a node listed in the alias `--from` runs a headless turn on the target (agent `alias-NAME`, header `[remote chat from NODE (agent origin) via relay alias NAME]`, `--resume latest` continues the newest Claude Code session for `--dir`, mode capped by relay max_mode, never `full`) unless a live interactive session holds the `relay name claim NAME` claim - then the message just lands in its inbox plus a macOS notification. The reply returns as mail (`relay inbox --name NAME` on the sender). Other senders are rejected and audited to `<state>/relay/alias-audit.jsonl`; `relay status` shows aliases and the last remote chat.

Mailbox layout: `nodes/<node>.json` heartbeats, `schedules/<target>/<origin>--<id>.json`, `results/<origin>/<target>--<id>--<due>.json`, `admin/<target>/<origin>--<id>.json`, `secrets/<node>/<NAME>.age` - each node writes only its own files, so rebases never conflict. `relay secret put NODE NAME` reads stdin or a hidden dialog and age-encrypts to that node's deploy ssh key (heartbeat `age_recipient` cache, else `gh api` keys); `relay secret get NAME` decrypts on the node to stdout with its relay ssh key (`AG_RELAY_SSH_KEY` / `relay.json` `ssh_key`); `relay secret rm NODE NAME` removes it. Plaintext never hits disk or git. Admin ops (`update`, `notify-dir PATH`, `status`) run once on the target; `relay.json` `admin_ops` limits them (default all three, `[]` disables). Target materialises inbound schedules into its `--state` (rows `<origin>--<id>`, agents `relay-<origin>-<name>`, workdirs `<workdir>/<origin>`; `agents add` guards and `--max-mode` clamp apply). Results land back in the origin row's state dir `relay/results/` and `relay results --last` prints the newest reply. Prompts and replies live in the mailbox - use a private repo and give the VM credentials. `status` alerts on a peer's differing `ag_commit` or silence > 2x its shortest schedule interval (or 1h). `--dir` on `init` is the mailbox, not the state dir. Dir transport nodes must be synced by their own means (ag does no copy).

## Remote schedule and one-shot cloud run

Configure both machines with `ag relay init` pointed at the same private git mailbox, using a different `--node` on each. Run `schedule install --write` on both. Then `schedule add w --every 1h --on muse "msg"` publishes work for `muse`; the origin tick never fires it. Pause/resume publish enabled state, `rm` unpublishes, and `run ID` explains that a remote row runs on its target at the next due time. The target's `--max-mode` defaults to `edits`. Prompts and replies are stored in the mailbox, so keep it private and match its access to the data being relayed.

One-shot cloud run: `{{AG}} --dir <state> wake w "task" --on muse` publishes an immediate run. Add `--notify-done` to write the completion notification on the target, where Muse watches its inbox. The target fires it on its next tick and reports once; the origin sync ingests the reply, removes the mailbox request, and clears the pending `remote@muse` row from `wakes`. Use the returned ID with `relay results --id ID`. Git mailboxes need credentials on both machines; dir mailboxes need a separate sync mechanism.
