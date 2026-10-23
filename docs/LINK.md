# ag link — a VM with no inbound access shows its sites and long-running jobs on the Mac (spec, 2026-10-23)

The relay (docs/RELAY.md, GitHub mailbox) stays the primary path for scheduled jobs. `ag link` is the live view: the VM
opens an **outbound** SSH connection to the Mac and reverse-forwards ports, so VM sites and a watch page appear on the
Mac as `http://localhost:<port>`. The Mac needs only its normal sshd (Remote Login); the VM needs a key the Mac accepts.
Stdlib only, POSIX, single-file `ag`; docs in README + `skills/ag-cli/SKILL.md`; tests offline.

## Config — `$AG_CONFIG_HOME/link.json`
```json
{"to": "sekun@100.88.16.70", "key": "~/.ssh/ag_link", "ports": "auto", "exclude": [22], "base": 41000,
 "watch_port": 7790, "map": {}}
```
- `ports`: `"auto"` = every TCP port the VM listens on for 127.0.0.1/0.0.0.0/:: (Linux `ss -Hltn`, macOS
  `lsof -nP -iTCP -sTCP:LISTEN -Fn`), minus `exclude`, minus the ssh port; or an explicit list `[3000, 8080]`.
- Mac-side port for VM port P = `map[P]` if set, else `base + (P % 1000)`; collisions → next free number above it.

## VM side
- `ag link init --to USER@HOST [--key PATH] [--ports auto|3000,8080] [--base N] [--watch-port N]` → writes link.json;
  creates `--key` with `ssh-keygen -t ed25519 -N "" -C ag-link@<hostname>` if missing and prints the public key plus the
  restricted `authorized_keys` line for the Mac:
  `restrict,port-forwarding,permitlisten="localhost:*",command="/bin/echo ag-link only" ssh-ed25519 AAAA... ag-link@vm`
- `ag link up [--foreground]` → supervisor: builds `ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -i KEY -R localhost:<mac>:localhost:<vm> ... TO`, plus the watch
  page forward; restarts on exit with backoff 2,4,8..60 s; every 30 s re-scans `auto` ports and restarts ssh when the
  set changed. Without `--foreground` it runs detached (double-fork like `ag` daemons, pidfile
  `$AG_CACHE_HOME/link.pid`, log `$AG_CACHE_HOME/link.log`). Writes `$AG_CACHE_HOME/link-status.json`:
  `{"pid", "ssh_pid", "up" (bool), "since", "restarts", "last_error", "forwards": [{"vm": P, "mac": M, "title"}]}`.
- `ag link down` → stop supervisor + ssh. `ag link status [--json]` → that file + whether pid is alive.
- `ag link serve` (started automatically by `up` unless `--no-watch`) → stdlib `ThreadingHTTPServer` on
  `127.0.0.1:<watch_port>`, read-only, Host header must be `localhost`/`127.0.0.1` (DNS-rebinding guard):
  - `GET /` → one HTML page (no external assets): ag spawn sessions (all state dirs in the schedule registry +
    `~/.agent`), running/recent wakes, forwarded ports as links (`http://localhost:<mac>`), auto-refresh.
  - `GET /api/sessions` → `[{dir, id, name, cmd, running, exit, idle_s}]`; `GET /api/tail?dir=D&id=ID&since=N` →
    `{"text", "next"}` (ANSI stripped, like `snap --clean`), polled by the page for a live log of long tests.
  - `GET /api/status` → link-status.json. Nothing writes; no kill/send endpoints in v1.
- `ag link run [--name N] -- CMD...` → `spawn` the command in the default state dir and print the watch URL
  (`http://localhost:<watch_port mac side>/#<id>`), so long tests are one command.

## Mac side
- Nothing to run: forwards are plain localhost listeners (DevPulse lists them under the sshd process).
- `ag link mac-key PUBKEY_FILE` → prints (does not write) the restricted authorized_keys line; the user adds it.

## Tests — `tests/test_ag_link.py`
Port-map math + collisions, `auto` port parsing for `ss -Hltn` and lsof `-F` fixtures, ssh argv built from config,
authorized_keys line, supervisor restart/backoff with a fake `ssh` script on PATH that exits, watch server: Host guard
403, `/api/sessions` + `/api/tail` against a real `ag spawn -- sh -c 'echo hi; sleep 1'` session in a temp state dir.
