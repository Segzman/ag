# Schedule tick, relay tick, ratelimit

Scheduled wakes (`schedule add`) are in `ag-agents`. One global job drives all state dirs.

## Schedule tick

```sh
{{AG}} schedule install                 # print launchd plist (label org.ag.schedule)
{{AG}} schedule install --write         # write ~/Library/LaunchAgents + bootstrap
{{AG}} schedule install --cron          # print crontab line instead
{{AG}} schedule install --systemd       # print user service + timer (Linux)
{{AG}} schedule install --systemd --write  # write units + enable timer
{{AG}} schedule tick --all --reap       # what the job runs every 60s; --dry-run, --now ISO
```

Registry `$AG_CONFIG_HOME/schedule-dirs.json`. Concurrent ticks are lock-safe; corrupt `schedules.json` refuses the tick. The systemd service is `Type=oneshot` with `KillMode=process`, so a stopped tick does not kill the wake workers it launched; it passes HOME, PATH, and present proxy variables.

## Relay schedule tick

`ag relay init --git URL --node NAME` configures a node against a shared private git mailbox. Run it on both machines with distinct node names; the VM needs credentials to pull and push that repository. Install the global tick on both with `ag schedule install --write`. `schedule tick --all --reap` runs the daily update check, then syncs before ticking (to pull remote schedules) and after (to report results); a relay error is included in `relay_error` and does not abort local schedules. Rows created with `schedule add ... --on NODE` are skipped by the origin's local tick. Prompts and replies are stored in the mailbox, so keep its access limited to people allowed to see that data. The target's `--max-mode` defaults to `edits`.

Relay admin ops run remotely without a shell: `{{AG}} --dir <state> relay admin NODE update|notify-dir PATH|status [--wait S]` publishes `admin/<target>/<origin>--<id>.json`; the target runs it once and returns `results/<origin>/<target>--<id>--admin.json`. `relay.json` `admin_ops` limits them (default all three; `[]` disables). `ag push` inside the ag checkout pushes, publishes `update` to every peer in `nodes/` except itself, and syncs; peers update on their next sync and report the new commit in their heartbeat.

## ratelimit

```sh
{{AG}} --dir <state> ratelimit [--json]          # slots in use (pid, agent) + cooldowns
{{AG}} --dir <state> ratelimit clear [KEY]       # drop one/all cooldowns
```
