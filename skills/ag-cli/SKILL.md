---
name: ag-cli
description: Use when driving the `ag` CLI itself (not delegating to agents) — state dir/--dir rules, non-blocking PTY sessions (spawn/snap/send/wait/kill), logged `run` + approvals, secret prompts (sudo/ssh passphrase/OTP), notes/todos, `ag models`, `ag setup`, `ag update`, the global schedule tick (`schedule install`), or when a command creates a stray .agent/ dir or a session seems to hang.
---

# ag CLI (scope: {{SCOPE}})

Single-file Python CLI, stdlib only, POSIX. Binary: `{{AG}}`. Delegation to other agents: skill `ag-agents`.

**One invocation per shell call.** No `;`/`&&` chains, no shell var for the path: chaining resets cwd and later calls hit the wrong `.agent/`. All commands take `--json`.

## State dir — read first

Resolution, no upward search: `--dir` -> `$AGENT_CLI_DIR` -> `$PWD/.agent`. Wrong cwd = fresh empty roster (looks wiped, isn't). **Always pass `--dir`.** Tool calls are fresh shells, so `export` does not persist. `--dir` (and `--json`) also work after the subcommand (`{{AG}} agents rm w --dir <state>`); after `agents add|set` `--dir` means the agent workdir (state dir not supported there).

```sh
{{AG}} --dir <state> agents list
```

Never commit `<state>/`.

## Never from agent context

Need a real TTY, hang the tool call: `tui`, `attach`, `shell`, `handoff --exec`. Want an interactive program? `spawn` + `snap`. Tell the user to run TUI commands.

## PTY sessions (non-blocking)

```sh
{{AG}} --dir <state> spawn -- bash
{{AG}} --dir <state> snap <id> --clean            # many readers OK; --since <offset> incremental
{{AG}} --dir <state> send <id> "ls"               # Enter appended; --no-enter; --key ctrl-c|ctrl-d|esc|tab|enter|up|down|left|right
{{AG}} --dir <state> wait <id> --timeout 30       # only blocking cmd; always bound it
{{AG}} --dir <state> kill [--force] <id>
{{AG}} --dir <state> sessions | status | events --limit 20
```

`spawn` returns instantly; poll with `snap` (offset comes from prior output). `spawn --name N --cwd D --env K=V --on-exit CMD`. Daemon `kill -9` -> session `stale` (`exit=-1`); its pending wake fires once on next reconcile (`sessions`/`snap`/`wait`/`kill`). `forget <id>` wipes logs (auth traces). `spawn --wake` = follow-up when session ends: see `ag-agents`.

## run, approvals, audit

```sh
{{AG}} --dir <state> run -- git status           # logged to history.jsonl; `--` required if child takes flags
{{AG}} --dir <state> approvals
{{AG}} --dir <state> approve <id>                # dangerous cmd blocked; approval lives 10 min
{{AG}} --dir <state> history
```

Dangerous `run` blocks (never executes) until `approve` or re-run with `--force`. Multi-token commands are shell-quoted; old approvals re-prompt once. Prefer native file tools; use `run` for the audit trail. `read|write|edit|ls|grep` exist as small file ops.

**rtk:** if `rtk` is on PATH, `run` rewrites the command to its token-compact form (result JSON has `rtk`). `run --raw` skips it for one call; `AG_RTK=0` disables globally. Agents on codex/gemini/cursor get a prompt hint to use `rtk`; claude/opencode use their own rtk hooks. `agents doctor` reports rtk coverage.

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

## Keys and providers

```sh
{{AG}} --dir <state> keys set NAME [--from-env VAR]     # Keychain (service ag) via `security -i` on STDIN; macOS only
{{AG}} --dir <state> keys list                          # names + created/updated, never values
{{AG}} --dir <state> keys rm NAME
{{AG}} --dir <state> keys test NAME --provider P        # GET provider /models, prints ok/401, never the key
{{AG}} --dir <state> providers add ID --base-url URL --key-ref keychain:NAME [--npm @ai-sdk/openai-compatible] [--privacy zdr|retain|train] [--max-concurrent N] [--rpd N] [--model ID ...]
{{AG}} --dir <state> providers list | show ID | rm ID
{{AG}} --dir <state> providers add groq --key-ref keychain:NAME   # presets: groq|cerebras|openrouter|cloudflare|gemini|mistral|sambanova
```

`${keychain:NAME}` works wherever `${VAR}` refs work (agent/profile env); resolved at spawn into child env only, redacted in `agents env`. Providers live in `$AG_CONFIG_HOME/providers.json` (refs only); ag merges an opencode fragment (`"apiKey": "{env:AG_KEY_<ID>}"`) into the scoped `OPENCODE_CONFIG` and injects `AG_KEY_<ID>` into the child env only (never global `~/.config/opencode/opencode.json`). Privacy feeds the work-guard; max_concurrent feeds ratelimit slots for `<provider>/*`.

```sh
{{AG}} --dir <state> agents add v --backend api --model PROVIDER/MODEL   # direct OpenAI-compat chat, no tools (e.g. groq/llama-3.3-70b-versatile); history AG_API_BUDGET tokens (default 2000)
```

`api` is for small-limit providers (Groq free 8K TPM, below opencode's first request): bounded history, streamed SSE reply, usage to the ledger, same slots/cooldowns/privacy/access/budgets as other backends. Opencode `ContextOverflowError` (`Request too large ... Limit 8000, Requested N`) kills the turn non-retryably with `use a backend=api agent`.

## Notes / todos

`ag note "..."` / `ag notes` · `ag todo add|list|done|clear` · `ag history` · `ag events`.

## Update

ag auto-checks GitHub once a day in a detached process; applies on next run. `schedule tick` runs the same daily check inline (same stamp), so a timer-only node still updates. Git checkout: `fetch` + `merge --ff-only`, only if clean, tracking, not ahead. Copy install: cheap `git ls-remote` sha compare (GitHub API fallback), download the pinned sha from raw, compile check, atomic swap, record in `~/.cache/ag/update-commit` (also the relay heartbeat commit).

```sh
{{AG}} update                  # run now
{{AG}} update --status         # mode, interval, installed commit, last check/result
{{AG}} update --auto off|on    # toggle (~/.config/ag/config.json)
{{AG}} push [GIT_PUSH_ARGS…]   # git push; signal every relay peer to update; sync
```

`AG_AUTO_UPDATE=0` disables per shell (use it in scripts/tests). Log `~/.cache/ag/update.log`.

## Models

```sh
{{AG}} models [--backend B] [--refresh] [--rank] [--sort intel|coding|speed|popular] [--tier small|balanced|big] [--free] [--zdr] [--auto on|off] [--status] [--json]
```

Live list per backend (opencode, codex, cursor discovered; claude/gemini static aliases). Cache `~/.cache/ag/models.json`, TTL 24h, auto-refresh when stale; `--refresh` forces. A failing source keeps its last good list and records `error`. Use it to pick valid model ids before `agents add --model`. `--rank` shows Artificial Analysis scores (intelligence/coding/agentic) + tok/s + latency from openrouter.ai/rankings, plus ctx, price/`free`, release date, `new`/`fast` badges. Tier is derived from the intelligence score (big >= 40, balanced 20-40, small < 20); `tier~` = no score, guessed from the name. Sorted by intelligence per backend; `--sort intel|coding|speed|popular`; `--tier T`/`--free` filter; `--json` has every field + `sources` fetch times. Source: "scores: Artificial Analysis via openrouter.ai/rankings". `AG_AA_KEY` adds the official AA API as an override. Stale caches refresh in a detached background run (never blocks); `{{AG}} models --auto on|off` toggles it (also the daily update's catalog refresh), `--status` shows last fetch per source. Small-fast vs big-smart: `models --backend opencode --free --tier big`, `--sort speed`. `--rank` shows a `data` column (zdr/retain/train); `--zdr` filters zero-retention only. `--backend openrouter` lists tools-support OpenRouter models with scores + endpoint `data` class + endpoint count (privacy from each model page's per-endpoint `dataPolicy`, 24h cache, free + top-60 intel only, <=4 parallel; `AG_OR_MODELS_URL`/`AG_OR_SITE_URL` override). Work agents on openrouter run via `--backend api` (`--model openrouter/<id>` sends `{"provider": {"data_collection": "deny", "zdr": true}}`); opencode backend refuses them (no provider-routing passthrough). `:free` takes 2 ratelimit slots; `/api/v1/key` tier (50/day free, 1000 after $10) feeds `usage` rpd. `{{AG}} route [JOB] [--profile P] [--check] [--json]` shows the detected harness + effective job routing for its profile; `--check` = per-project setup questions (see `ag-agents`).

## Data privacy

Rule: work code only to zdr models. `agents add|set --data work|public` (default from `routing.json` `privacy.default`, default `work`); project setup asks it (`route --check` data question, `setup --answers '{"data":"work"}'`, web form selector). `work` refuses `train`/`unknown` at add/set and at turn time (exit 2, never sent). `AG_PRIVACY=off` disables (unsafe). Opencode `default` = `opencode/space-bunny-free` (zdr); free catalog picks rank zdr first. Live table: opencode.ai/docs/zen Privacy (`AG_ZEN_URL`, 24h cache); unknown free opencode = `train`.

| data | models |
|---|---|
| zdr | `opencode/space-bunny-free`, `opencode/longcat-2.5-preview-free`, `opencode/step-5-preview-free`; paid Mistral/Jev |
| retain | paid OpenAI/Anthropic (30d); direct `claude`/`codex` (own subscription); `gemini`/`cursor` |
| train | `opencode/muse-spark-1.3-contributor-free`, `big-pickle`, `exo-free`, `mimo-v2.6-flash-free`, `ling-3.1-flash-free`, `ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free` |
| zdr/retain/train per endpoint | `openrouter/<id>` from the endpoint dataPolicy (unknown = train; work only via `--backend api`) |

## Setup

```sh
{{AG}} setup [--scope global|project] [--harness claude,opencode,codex|all] [--preset cost-first|balanced|quality-first] [--set JOB=claude:ALIAS] [--set JOB=ag:BACKEND/MODEL[@EFFORT]] [--set backend.B.enabled=true|false] [--set backend.B.model=ID] [--set backend.B.mode=ro|edits|auto|full|unset] [--set backend.B.plan=true|false] [--set backend.B.effort=LEVEL|default] [--claude-md|--no-claude-md] [--profile claude|opencode|codex|default] [--answers JSON] [--yes] [--dry-run] [--ui web|cli|plain]
```

Ask the user to run it bare (interactive). Agent context: pass flags + `--yes` (or `--dry-run` first). `--ui` / `AG_SETUP_UI`: `web` (alias `mac`; auto on a local macOS desktop) = local browser form on 127.0.0.1 (random port + one-time token URL; `AG_SETUP_NO_OPEN=1` prints the URL instead of opening; Jobs per profile with grouped model dropdowns + effort select per job (per-model variants when known, else low|medium|high|xhigh|max, plus `default`/`highest`) + capability panel, Backends (with default effort), ranked Models, Skills; Save/Cancel, 15 min idle exits writing nothing), `cli` = curses (jobs table + Backends table, needs 60x24), `plain` = prompts. Writes `routing.json` (jobs + per-backend defaults that feed `agents add`), sparsely: only jobs differing from the inherited profile are stored (explicit `--set JOB=...` always stored), renders `ag-cli`/`ag-agents` skills into harness skill dirs, routing block in `~/.claude/CLAUDE.md` (claude/global). Atomic, never deletes. Job routed to a disabled backend: error, no save.

## Notifications

```sh
{{AG}} notify TITLE BODY [--dir D] [--json]
{{AG}} notify --set-dir ~/.notify-muse
```

Muse polls `~/.notify-muse/` every 15 seconds and archives valid `{"title","body"}` files after showing them. `notify` writes atomically; inbox priority is `--dir` > `$AG_NOTIFY_DIR` > `notify_dir` in `$AG_CONFIG_HOME/notify.json`. Title limit 120 characters, body 2,000; excess ends in `…`. `wake`, `chat send`, `delegate`, and `schedule add` accept `--notify-done`; for a remote wake, that flag travels to the target.

## Schedule tick (mechanics)

Scheduled wakes live in `ag-agents`. One global launchd job drives all state dirs:

```sh
{{AG}} schedule install                 # print launchd plist (label org.ag.schedule)
{{AG}} schedule install --write         # write ~/Library/LaunchAgents + bootstrap
{{AG}} schedule install --cron          # print crontab line instead
{{AG}} schedule install --systemd       # print user service + timer (Linux)
{{AG}} schedule install --systemd --write  # write units + enable timer
{{AG}} schedule tick --all --reap       # what the job runs every 60s; --dry-run, --now ISO
```

Registry `$AG_CONFIG_HOME/schedule-dirs.json`. Concurrent ticks are lock-safe; corrupt `schedules.json` refuses the tick. The systemd service is `Type=oneshot` with `KillMode=process`, so a stopped tick does not kill the wake workers it launched; it passes HOME, PATH, and present proxy variables.

### Relay schedule tick

`ag relay init --git URL --node NAME` configures a node against a shared private git mailbox. Run it on both machines with distinct node names; the VM needs credentials to pull and push that repository. Install the global tick on both with `ag schedule install --write`. `schedule tick --all --reap` runs the daily update check, then syncs before ticking (to pull remote schedules) and after (to report results); a relay error is included in `relay_error` and does not abort local schedules. Rows created with `schedule add ... --on NODE` are skipped by the origin's local tick. Prompts and replies are stored in the mailbox, so keep its access limited to people allowed to see that data. The target's `--max-mode` defaults to `edits`.

Relay admin ops run remotely without a shell: `{{AG}} --dir <state> relay admin NODE update|notify-dir PATH|status [--wait S]` publishes `admin/<target>/<origin>--<id>.json`; the target runs it once and returns `results/<origin>/<target>--<id>--admin.json`. `relay.json` `admin_ops` limits them (default all three; `[]` disables). `ag push` inside the ag checkout pushes, publishes `update` to every peer in `nodes/` except itself, and syncs; peers update on their next sync and report the new commit in their heartbeat.

```sh
{{AG}} --dir <state> ratelimit [--json]          # slots in use (pid, agent) + cooldowns
{{AG}} --dir <state> ratelimit clear [KEY]       # drop one/all cooldowns
```

## Access, usage, budgets

```sh
{{AG}} --dir <state> access show [--scope global|project] [--data work|public]
{{AG}} --dir <state> access allow GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> access deny GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> access rm GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> usage [--by provider|model|agent|project|day] [--since 1d|7d|30d] [--json]
{{AG}} --dir <state> budget show
{{AG}} --dir <state> budget set [--daily USD] [--monthly USD] [--provider P --rpd N]
```

`access` = `routing.json` allow/deny globs over `backend/model` + `provider/*` (deny wins; per-`--data work|public`). Ledger `$AG_CACHE_HOME/usage.jsonl`. `budget` refuses turns (exit 2) unless `--force`; 80% warns `[budget]`.

## Link (VM -> Mac)

VM shows its sites + long jobs on the Mac over outbound reverse SSH (docs/LINK.md). Mac needs only sshd + the printed restricted `authorized_keys` line (`link mac-key PUBKEY_FILE` prints it, never writes).

```sh
{{AG}} link init --to USER@HOST [--key K] [--ports auto|3000,8080] [--base 41000] [--watch-port 7790]
{{AG}} link up [--foreground] [--no-watch]   # detached supervisor; backoff 2,4,8..60s; auto ports re-scanned every 30s
{{AG}} link down | status [--json]           # stop / state in $AG_CACHE_HOME/link-status.json (+ pid alive)
{{AG}} link serve [--port N]                 # watch page alone (127.0.0.1, Host localhost/127.0.0.1 only)
{{AG}} link run [--name N] -- CMD...         # spawn + print watch URL /#<id> for long tests
{{AG}} link mac-key PUBKEY_FILE              # Mac side: restricted authorized_keys line
```

Watch page: sessions across schedule-registry dirs + `~/.agent`, wakes, forwards as links; `/api/sessions|status|tail?dir&id&since` read-only (tail = `snap --clean`-style polling). `ports: auto` scans VM listeners (`ss -Hltn` Linux / `lsof -Fn` macOS) minus exclude+22; mac port = `map[P]` or `base+(P%1000)`, collisions bump.

## Sanity

`{{AG}} selfcheck` (regression checks, own temp dir) · `{{AG}} keys` (TUI cheat-sheet) · `{{AG}} agents doctor` (backends + rtk).

Deeper: `README.md`, `docs/INSTALL.md` in the ag repo.
