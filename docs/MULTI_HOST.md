# Multi-host transfer (outbound SSH only)

Continue Mac tasks on an always-on Muse Linux host. Only direction is
outbound SSH from Muse to Mac. There is no inbound port 22 on Muse
(verified refused), so the Mac never dials back — Muse polls and pulls.

No automatic failover. Every resume is an explicit, acknowledged handoff:
stop/fence the source first, then resume into a fresh local session.

## Setup (on Muse)

Run from an `ag` checkout on the Muse host:

```sh
./scripts/setup-muse.sh        # ag + OpenCode binary per official installers
./scripts/setup-muse.sh --skip-opencode   # ag only
```

No sudo, no global writes, no shell-rc edits (OpenCode installer runs
with `--no-modify-path`), no credentials, no SSH config changes. The
script installs the OpenCode binary only; authenticate backends
(API keys, opencode auth) separately by you.

## Register the Mac (on Muse)

`--ssh-json` is a local argv array: executable, options, destination.
Key auth with `BatchMode`; `ProxyCommand` only if you already use one.
Placeholders throughout — no real hostnames, users, or IPs here.

```sh
ag --dir "$HOME/.agent" hosts add mac \
  --ssh-json '["ssh","-o","BatchMode=yes","-i","/home/MUSE_USER/.ssh/AG_KEY","MUSE_SSH_ALIAS_OR_MAC_USER@MAC_HOST"]' \
  --remote-ag "/Users/MAC_USER/.local/bin/ag" \
  --remote-state "/Users/MAC_USER/.agent" \
  --remote-project "/Users/MAC_USER/project" \
  --local-project "$HOME/project"
ag --dir "$HOME/.agent" hosts list
ag --dir "$HOME/.agent" hosts check mac
```

Remote `--remote-*` paths are Mac-absolute (`/Users/MAC_USER/...` —
never Muse `$HOME` expansion). Inside single-quoted `--ssh-json`,
`$HOME` would not expand, so write the Muse key path explicitly
(`/home/MUSE_USER/.ssh/AG_KEY`) or use an SSH alias from `~/.ssh/config`
(argv `["ssh","-T","ag-mac"]`). `--local-project` is the only
Muse-local path.

Call the remote executable by absolute path (remote non-interactive
`PATH` is minimal). `hosts check` runs `sync _probe` remotely and
reports reachability plus project/state identity.

## Pull a snapshot (on Muse)

```sh
ag --dir "$HOME/.agent" sync pull mac worker --file code.txt
ag --dir "$HOME/.agent" sync pull mac worker --file a.txt --file b.txt --message "optional override"
ag --dir "$HOME/.agent" sync pull mac worker --watch 60   # outbound poll loop, >=1s
ag --dir "$HOME/.agent" sync list
```

What crosses the wire: agent backend/model/role (no SIDs, no PIDs, no
locks, no profiles), assignment scope/task/acceptance, context text,
BRIEF.md/CHECKPOINT.md, latest source request, and only the
`--file`-selected project files. Custom persona, system prompt, and
instruction files do NOT transfer: the resumed agent keeps the source's
topology `role` only, with empty persona/system/instructions — re-apply
behavior with `agents set --persona/--system/--instructions` (or a
harness profile) if the source relied on them. Runtime credential/config stores are
excluded even when explicitly selected (see bounds below), but arbitrary
user text (task, messages, context, checkpoints) transfers as-is — do
not paste secrets there. Deselected files never transfer.

Snapshots are content-addressed (`sha256` digest filename) and
deduplicated: pulling identical content returns the existing snapshot.
Writes are atomic (temp file + fsync + rename); an interrupted pull
keeps the last good snapshot and never leaves a partial file.

Identity binding: the remote echoes the requested `--project` string and
pull requires it to equal the configured `remote_project` exactly; a
second remote `_probe` must report the same resolved project/state
strings as the export. Remote paths are compared as remote strings only
and never resolved on the local filesystem (per-OS symlinks such as
`/var` vs `/private/var` differ across hosts). Resume additionally
requires the host mapping to be byte-identical to pull time
(`host_hash` plus the recorded request strings).

Transferable agents must be registered with an absolute `--dir` on the
source Mac (relative dirs resolve against the server's working directory,
which sshd sets to `$HOME`, so they can never match over SSH). On the Mac:

```sh
ag --dir "$HOME/.agent" agents set worker --dir /Users/MAC_USER/project
```

Bounds: 64 files max, 1 MiB per file, 4 MiB total, 8 MiB wire cap,
24k chars per text field. Rejected even when explicitly selected:
`.git`, `.agent`, `.ssh`, `.aws`, `.azure`, `.config`, `.codex`,
`.claude`, `.opencode`, `.gnupg`, `node_modules`, `.env*`, `*.pem`,
`*.key`, `auth.json`, `credentials.json`, key filenames, dot segments,
absolute paths, and anything escaping the project root. Symlinked
project entries are refused; state files are never portable.

## Resume (on Muse)

```sh
ag --dir "$HOME/.agent" sync resume SNAPSHOT --name linux --project "$HOME/project" --source-stopped
ag --dir "$HOME/.agent" sync resume SNAPSHOT --name linux --project "$HOME/project" --source-stopped --run
```

Rules:

- `--source-stopped` is required: you assert the source is stopped/fenced
  and will not restart. The flag is your acknowledgement, not a proof.
- Fresh local session always: destination `--name` must not exist
  (roster, chat log, or meta). `--backend/--model` optionally override
  the snapshot's backend/model; default reuses them (`echo` works keyless).
  A `--backend` override to a different harness resets the model to that
  backend's default (stored `default`; Muse Spark on opencode) instead of
  carrying a foreign provider model ID; explicit `--model` always wins.
- Source known-busy refuses: a pre-resume `_probe` that reports busy (or
  unknown status) aborts the resume. Stop the source and check terminal
  state first.
- Source unreachable is accepted only with the explicit acknowledgement,
  and the result reports `source_verified: false` (unverified). An SSH
  failure never proves the source is dead — it may still be running.
- No claim of safe automatic failover: a timeout or unreachable host is
  never treated as permission to take over. The user fences the source.
- Existing destination files are never silently clobbered: any unsafe or
  already-existing extraction target aborts before anything is written
  (exclusive-create; preflight + `xb` open). A tampered snapshot (hash or
  schema mismatch, unexpected fields, bad encoding) is rejected before
  any write.
- Destination project must equal the host's `--local-project`, and the
  snapshot's remote project/state must still match the host mapping
  (symlink-tolerant compare, e.g. `/var` vs `/private/var`).

`--run` sends one turn in the fresh session after preparing it; the reply
combines the transferred context, checkpoint, and latest source request.
The turn is bounded by `--max-runtime` (default 1800s, `0` = unlimited;
budget starts at guard acquisition). A timed-out or backend-nonzero turn
fails the resume nonzero with the actual exit code (`agent prepared, but
turn failed (exit N)`), while the prepared agent and files stay in place
for inspection. The turn waits on the per-agent guard like any other turn,
so a held guard delays it (fresh resume names are normally uncontended).
Verify files and external effects before repeating work — the snapshot
may be stale.

Recovery: a failed `--run` does NOT roll back — the prepared agent and
extracted files are valid and kept. Do not rerun `sync resume` (the name
is taken) and do not delete data blindly. The error names the prepared
agent and the saved message file; retry with e.g.
`ag chat send linux "$(cat <message_path>)"`. To start over instead,
remove the extracted files, `agents rm` the prepared agent, and resume
the same snapshot under a fresh name.

## Outbound polling recipe (on Muse)

`sync pull --watch` is the poll loop (Muse dials out; nothing listens).
For cron instead of a long-lived loop:

```sh
# crontab -e (paths illustrative; user key/proxy configured in ~/.ssh/config)
*/5 * * * * "$HOME/.local/bin/ag" --dir "$HOME/.agent" --json sync pull mac worker --file code.txt >>"$HOME/.agent/sync/poll.log" 2>&1
```

Suggested `~/.ssh/config` (placeholders; your key, your proxy):

```
Host ag-mac
  HostName MAC_HOST_OR_IP
  User MAC_USER
  IdentityFile ~/.ssh/AG_KEY
  ProxyCommand /absolute/path/to/existing-proxy.sh %h %p
  BatchMode yes
```

Then `hosts add` uses `"ssh","-T","ag-mac"` as the argv. Keep the Mac's
`ag` and project paths Mac-absolute (`/Users/MAC_USER/...`). Poll, pull, verify `sync list`, then
resume with `--source-stopped` only after fencing the source.
