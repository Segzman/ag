# Schedule tick, relay tick, ratelimit

Scheduled wakes (`schedule add`) are in `ag-agents`. One global job drives all state dirs.

## Schedule tick

```sh
{{AG}} schedule install                 # print launchd plist (label org.ag.schedule)
{{AG}} schedule install --write         # write ~/Library/LaunchAgents + bootstrap
{{AG}} schedule install --cron          # print crontab line instead
{{AG}} schedule install --systemd       # print user service + timer (Linux)
{{AG}} schedule install --systemd --write  # write units + enable timer
{{AG}} schedule install --write         # auto backend: launchd (macOS) | systemd user timer | cron | --loop (no scheduler)
{{AG}} schedule tick --all --reap       # what the job runs every 60s; --dry-run, --now ISO
{{AG}} schedule health [--fix]          # exit 1 = tick missing/stale/hung; --fix kills, reinstalls, ticks now
```

Use `schedule health` (not a harness-specific guard) to monitor the tick from any agent or cron; `--fix` repairs it. Each global tick has a hard deadline (`AG_TICK_MAX_S`, default 600s) and relay git calls die as a process group on timeout, so a stuck `git pull` cannot wedge the scheduler. Heartbeat: `$AG_CONFIG_HOME/tick.json`.

Registry `$AG_CONFIG_HOME/schedule-dirs.json`. Every tick also relaunches `parked` usage-limit wake jobs whose `resume_at` passed, once (old job `resumed_as`, new `parked_from`; `--dry-run` reports `would-resume`). Concurrent ticks are lock-safe; corrupt `schedules.json` refuses the tick. The systemd service is `Type=oneshot` with `KillMode=process`, so a stopped tick does not kill the wake workers it launched; it passes HOME, PATH, and present proxy variables.

## Relay schedule tick

`ag relay init --git URL --node NAME` configures a node against a shared private git mailbox. Run it on both machines with distinct node names; the VM needs credentials to pull and push that repository. Install the global tick on both with `ag schedule install --write`. `schedule tick --all --reap` runs the daily update check, then syncs before ticking (to pull remote schedules) and after (to report results); a relay error is included in `relay_error` and does not abort local schedules. Rows created with `schedule add ... --on NODE` are skipped by the origin's local tick. Prompts and replies are stored in the mailbox, so keep its access limited to people allowed to see that data. The target's `--max-mode` defaults to `edits`.

Relay admin ops run remotely without a shell: `{{AG}} --dir <state> relay admin NODE update|notify-dir PATH|status [--wait S]` publishes `admin/<target>/<origin>--<id>.json`; the target runs it once and returns `results/<origin>/<target>--<id>--admin.json`. `relay.json` `admin_ops` limits them (default all three; `[]` disables). `ag push` inside the ag checkout pushes, publishes `update` to every peer in `nodes/` except itself, and syncs; peers update on their next sync and report the new commit in their heartbeat.

Relay alias (remote chat): `ag relay alias add NAME --backend claude --dir DIR [--resume latest|SID|none] [--mode ro|edits|auto] [--from NODE,..]` (human-gated: agents get a pending `ag grant ID` request) makes a peer's `relay say --name NAME` run a headless turn here on the next tick (header `[remote chat from NODE (agent origin) via relay alias NAME]`), unless a live interactive session holds the name claim (then inbox + notification). `alias list | rm NAME`; replies come back via `relay inbox --name NAME`; audit in `<state>/relay/alias-audit.jsonl`.

Delivery: `wake --on NODE` warns when the peer heartbeat lists no usable backends; `relay say` warns when the peer has no claimed mailbox (`names`) or not the addressed one. Both still send and report `delivery_warning`; `relay status` lists one-shot mail older than 10 min without a result as `UNDELIVERED` with the reason. Git pushes retry `pull --rebase` with jittered backoff (5 attempts; `cannot lock ref`, `fetch first`), hold a checkout lock, and continue/abort a stale rebase before pulling.

Secrets: `{{AG}} --dir <state> relay secret put NODE NAME` reads stdin or the hidden `ui ask --secret` dialog, encrypts with `age -r` to the node's deploy ssh key (cached as `nodes/NODE.json` `age_recipient`, else `gh api repos/OWNER/REPO/keys`), and commits `secrets/NODE/NAME.age`. `relay secret get NAME` decrypts on that node with its relay ssh key (`AG_RELAY_SSH_KEY` / `relay.json` `ssh_key` / `~/.ssh/ag_relay`) to stdout only; `relay secret rm NODE NAME` removes and commits. Plaintext never lands on disk or in logs; a missing `age` binary is a clear error.

## ratelimit

```sh
{{AG}} --dir <state> ratelimit [--json]          # slots in use (pid, agent) + cooldowns
{{AG}} --dir <state> ratelimit clear [KEY]       # drop one/all cooldowns
```
