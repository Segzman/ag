---
name: ag-cli
description: Use when driving the `ag` CLI itself (not delegating to agents) — state dir/--dir rules, non-blocking PTY sessions (spawn/snap/send/wait/kill), logged `run` + approvals, secret prompts (sudo/ssh passphrase/OTP), notes/todos, `ag models`, `ag setup`, `ag update`, the global schedule tick (`schedule install`), or when a command creates a stray .agent/ dir or a session seems to hang.
---

# ag CLI (scope: {{SCOPE}})

Single-file Python CLI, stdlib only, POSIX. Binary: `{{AG}}`. Delegation to other agents: skill `ag-agents`.

**One invocation per shell call.** No `;`/`&&` chains, no shell var for the path: chaining resets cwd and later calls hit the wrong `.agent/`. All commands take `--json`.

## State dir — read first

Resolution, no upward search: `--dir` -> `$AGENT_CLI_DIR` -> `$PWD/.agent`. Wrong cwd = fresh empty roster (looks wiped, isn't). **Always pass `--dir`.** Tool calls are fresh shells, so `export` does not persist.

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

ag auto-checks GitHub once a day in a detached process; applies on next run. Git checkout: `fetch` + `merge --ff-only`, only if clean, tracking, not ahead. Copy install: download `master`, compile check, atomic swap.

```sh
{{AG}} update                  # run now
{{AG}} update --status         # state
{{AG}} update --auto off|on    # toggle (~/.config/ag/config.json)
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

## Schedule tick (mechanics)

Scheduled wakes live in `ag-agents`. One global launchd job drives all state dirs:

```sh
{{AG}} schedule install                 # print launchd plist (label org.ag.schedule)
{{AG}} schedule install --write         # write ~/Library/LaunchAgents + bootstrap
{{AG}} schedule install --cron          # print crontab line instead
{{AG}} schedule tick --all --reap       # what the job runs every 60s; --dry-run, --now ISO
```

Registry `$AG_CONFIG_HOME/schedule-dirs.json`. Concurrent ticks are lock-safe; corrupt `schedules.json` refuses the tick.

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

## Sanity

`{{AG}} selfcheck` (regression checks, own temp dir) · `{{AG}} keys` (TUI cheat-sheet) · `{{AG}} agents doctor` (backends + rtk).

Deeper: `README.md`, `docs/INSTALL.md` in the ag repo.
