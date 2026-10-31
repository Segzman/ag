# Secrets, ui dialogs, notifications

## Secrets (passwords, passphrases, OTP)

ag pops a native macOS dialog (masked) and hands the value to the program; the model never sees it.

```sh
{{AG}} --dir <state> spawn -- sudo make install   # prompt detected -> dialog -> typed into PTY
{{AG}} --dir <state> send <id> --secret           # value from dialog; never logged
{{AG}} --dir <state> spawn --no-secret-popup -- ...   # per session; AG_SECRET_POPUP=0 globally
```

Backend turns and spawned sessions get `SUDO_ASKPASS`/`SSH_ASKPASS`/`GIT_ASKPASS` -> `<state>/bin/ag-askpass` (+ a `sudo` shim adding `-A`). `ag askpass` only answers when its parent is sudo/ssh/git; never call it yourself. Secret never hits disk: `input.log` records `[secret input redacted]`, events log outcome only. Never request secrets in chat or put them in argv. Cancel in dialog = nothing sent. macOS only; elsewhere detection is off. A program that echoes its own input can still leak to `output.log`.

## Talk to the user (`ag ui`)

Prefer a silent notification; pop a dialog only when you need input. Exit 3 off macOS; `--json` supported; events log the outcome only.

```sh
{{AG}} --dir <state> ui notify "build done" [--title T] [--sound]
{{AG}} --dir <state> ui confirm "Deploy?" [--yes L] [--no L] [--timeout S]   # yes|no|timeout, exit 0/1/2
{{AG}} --dir <state> ui choose "Which?" a b c [--multi]                      # one per line, exit 2 on cancel
{{AG}} --dir <state> ui ask "Name?" [--default D]                            # prints the answer
{{AG}} --dir <state> ui ask "Token?" --secret --to-env VAR -- CMD ...        # CMD runs with VAR set; you never see it
```

`ui ask --secret` without `--to-env` refuses (exit 2) unless `--stdout` is passed and stdout is piped, not a TTY. Never route secrets into your own context; always use `--to-env`.

## Notifications (`ag notify`)

```sh
{{AG}} notify TITLE BODY [--dir D] [--json]
{{AG}} notify --set-dir ~/.notify-muse
```

Muse polls `~/.notify-muse/` every 15 seconds and archives valid `{"title","body"}` files after showing them. `notify` writes atomically; inbox priority is `--dir` > `$AG_NOTIFY_DIR` > `notify_dir` in `$AG_CONFIG_HOME/notify.json`. Title limit 120 characters, body 2,000; excess ends in `…`. `wake`, `chat send`, `delegate`, and `schedule add` accept `--notify-done`; for a remote wake, that flag travels to the target.
