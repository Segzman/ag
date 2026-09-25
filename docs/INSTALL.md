# Installing ag

## Requirements

- POSIX OS: macOS or Linux (no Windows support).
- Python 3.9+ (`ag` uses `os.waitstatus_to_exitcode`, added in 3.9).
- `git`, for cloning and updating.
- No Python packages — `ag` is one stdlib-only file.

Check: `python3 --version`.

## Install

```sh
git clone https://github.com/Segzman/ag.git
cd ag
./install.sh                  # installs standalone ag to ~/.local/bin/ag
```

Variants:

```sh
./install.sh --prefix DIR     # installs to DIR/bin/ag
./install.sh --force          # replace an existing installed file
```

The installer copies only the single `ag` file. It does not use sudo,
does not fetch anything from the network, does not set up backends,
and does not edit shell rc files. Running from the checkout (`./ag`)
keeps working with or without installing.

## PATH

The install dir must be on `PATH`. For the current bash/zsh session:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

For a custom prefix, export that instead:

```sh
export PATH="DIR/bin:$PATH"
```

Then `ag --version` (or `ag selfcheck`) should run from any directory.
To persist, add the same `export` line to your shell rc file yourself —
the installer never does this.

## Verify

```sh
STATE="$(mktemp -d)"                          # fresh state dir, no collisions
ag --dir "$STATE" selfcheck                   # built-in regression checks
ag --dir "$STATE" agents doctor               # which backend CLIs are found
```

No-key end-to-end check with the local `echo` backend (agent state
stays inside `$STATE`):

```sh
ag --dir "$STATE" agents add smoke --backend echo --role sub
ag --dir "$STATE" chat send smoke "hi"
JOB="$(ag --dir "$STATE" --json wake smoke "summarize this" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["job"])')"
ag --dir "$STATE" wakes --wait "$JOB" --timeout 30  # wake is async: wait for done
ag --dir "$STATE" chat log smoke
```

No cleanup step: the daemon may still finalize the session after the
job reports done, so leave the temp dir to the OS.

Coding-CLI backends (`claude`, `opencode`, `codex`, `gemini`, `cursor`)
are optional: install and authenticate each one separately, then
re-run `ag agents doctor`. Skills (`.claude/skills/`) and docs stay in
the clone — a single-file install does not move them.

Note: when `opencode` is not on `PATH`, `ag` also looks in
`~/.opencode/bin/opencode`, `~/.local/bin/opencode`,
`~/.bun/bin/opencode`, `/opt/homebrew/bin/opencode`, and
`/usr/local/bin/opencode` before giving up.

## Update

```sh
cd ag
git pull --ff-only
./install.sh --force
```

## Remote use over SSH

`ag` works over non-interactive SSH. Only `wake` (and `spawn`) detach:
`doctor`, `chat send`, and `wakes --wait` are synchronous — the SSH
session stays open until they return. Key auth with `BatchMode` is
enough — `ag` itself needs no ssh-agent or forwarding.

Generic client config (placeholders throughout; `ProxyCommand` only if
you already use a jump script):

```
Host ag-mac
  HostName MAC_HOST_OR_IP
  User MAC_USER
  IdentityFile ~/.ssh/AG_KEY
  ProxyCommand /absolute/path/to/existing-proxy.sh %h %p
  BatchMode yes
  UserKnownHostsFile ~/.ssh/AG_KNOWN_HOSTS
```

Call the remote executable by absolute path — the remote `PATH` is
separate and usually minimal over non-interactive SSH:

```sh
ssh -T ag-mac '"$HOME/.local/bin/ag" --dir "$HOME/.agent" agents doctor'
ssh -T ag-mac '"$HOME/.local/bin/ag" --dir "$HOME/.agent" agents add worker --backend opencode --dir "$HOME/project"'
ssh -T ag-mac '"$HOME/.local/bin/ag" --dir "$HOME/.agent" chat send worker "task"'
ssh -T ag-mac '"$HOME/.local/bin/ag" --dir "$HOME/.agent" --json wake worker "long task"'
```

`--json wake` returns at once with the job id and worker session, and
the connection can close. Poll for completion with that job id, then
read the reply:

```sh
ssh -T ag-mac '"$HOME/.local/bin/ag" --dir "$HOME/.agent" --json wakes --wait JOB --timeout 300'
ssh -T ag-mac '"$HOME/.local/bin/ag" --dir "$HOME/.agent" chat log worker'
```

Notes:

- `--dir "$HOME/.agent"` before the subcommand is the global state
  dir; `--dir "$HOME/project"` after `agents add` is that agent's
  project dir. Prefer an absolute project dir: relative agent dirs
  resolve against the invocation cwd (preserved across the wake fix),
  which is easy to get wrong over SSH.
- The backend (e.g. `opencode`) must be installed and authenticated on
  the Mac; check `agents doctor` there first.

## Uninstall

Remove only the installed file:

```sh
rm ~/.local/bin/ag        # or DIR/bin/ag for a custom prefix
```

State is retained: uninstalling never touches `./.agent/` (or any
`--dir` / `$AGENT_CLI_DIR` state dir) or the clone.
