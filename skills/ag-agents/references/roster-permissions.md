# Roster, permissions, env, ACP, roles

## Contents
- Roster and backends
- Caps and permission modes
- Per-agent env and keys
- ACP transport
- Roles

## Roster and backends

```sh
{{AG}} --dir <state> agents doctor [--refresh]   # installed, version, compat, auth probe (+ rtk)
{{AG}} --dir <state> agents list
{{AG}} --dir <state> agents ps [--tree]          # live turns/wakes: pids, CPU, RSS, elapsed
{{AG}} --dir <state> agents add w --backend opencode --role sub [--model ID] [--data work|public] [--job mechanical]
{{AG}} --dir <state> agents set w --model ID | --backend B | --persona planner | --worktree
{{AG}} --dir <state> agents rm w [--force] [--purge]
{{AG}} --dir <state> agents reset w               # clear stored session ids; keep config and chat log
{{AG}} --dir <state> agents create "a careful reviewer for X that never edits files" [--name N] [--using AGENT] [--yes] [--dry-run]
{{AG}} --dir <state> agents fork src new [--backend B] [--model ID] [--effort E] [--worktree]   # clone config + history, fresh session; list shows `<- src`; refused while src busy
{{AG}} --dir <state> harness use w codex [--budget N]   # switch backend; next turn replays old history bounded by N tokens (default 4000, AG_HANDOFF_BUDGET); harness current w shows it
{{AG}} --dir <state> models --backend opencode   # valid model ids
```

`agents create`: a model drafts name/job/mode/instructions from the description, ag clamps to safe values and routes it; without `--yes` it prints the proposal + the exact `agents add` command only (`--dry-run` same, no write).

`agents add` without `--model/--mode/--plan/--effort` takes backend defaults from routing (set via `ag setup`). `--job JOB` fills backend+model+effort from routing when `--backend` absent (`ag route` shows `backend/model@effort`). `agents rm` clears stored session ids but keeps chat history; `--purge` also deletes chat log and metadata. Re-adding the name starts a fresh session. `agents reset` clears every backend session id while keeping config and chat history. Switches refuse while busy. `default` model = no `-m`. `echo` = keyless test backend. `doctor` probe: logged-out backend fails turns fast (`not logged in to <backend>`), unknown proceeds; cache 60s; `AG_PROBE=0` disables. Local keyless models: `agents add l --backend api --model ollama/qwen3:8b` (presets ollama|lmstudio, zdr, cost 0).

## Caps and permission modes

```sh
{{AG}} --dir <state> agents caps [BACKEND]       # per-backend capability table (modes, plan, resume, interrupt)
{{AG}} --dir <state> agents add w --backend codex --mode edits [--plan]
{{AG}} --dir <state> agents set w --mode ro|edits|auto|full | --mode "" | --plan | --no-plan
{{AG}} --dir <state> agents set w --allow-cmd '.venv/bin/python -m unittest*' --allow-cmd 'node --check*'   # extra edits-shell prefixes (opencode allowlist; claude --allowedTools Bash(P*)) (repeatable; "" clears)
{{AG}} --dir <state> agents set w --effort high           # reasoning effort, checked per model
{{AG}} --dir <state> agents set w --stall-s 900           # per-agent stall watchdog seconds
```

Effort: opencode `--variant` (Muse Spark top = xhigh, no max), claude `--effort`, codex `model_reasoning_effort`; `highest|lowest` resolve, `""` clears, unset = routing/backend default; stale stored effort falls back to the model default with an `[effort]` row. Stall: 0 = off, `""` clears -> `AG_STALL_S`/backend default; shown in `agents list --json`; `wake|chat send --stall-s N` overrides one turn (per-turn > agent > `AG_STALL_S` > backend default).

Modes: `ro` read-only, `edits` workspace writes, `auto`, `full` unrestricted. opencode: `ro` = no shell; `edits` = shell allowlist (git/tests/typecheck/lint, no push; `config.json opencode_bash_allow.edits`) - an accident guard, not a sandbox (chained commands bypass it); background runs that need arbitrary shell use `auto`. `--allow-cmd PATTERN` adds per-agent prefixes on top of the edits allowlist, and every edits-mode opencode turn's prompt gets one line ("Shell is restricted; allowed command prefixes: ...") so the agent stops burning turns on denied shell. Unset = backend default. Check `agents caps` first: a mode the backend can't enforce (echo, gemini `auto`) prints a `[mode]` warning and runs default. Prefer `ro` for review/plan jobs. Never `full` unless asked. Dispatch ceiling: inside a turn, `agents add/set --mode` above the parent's mode fails (`mode X exceeds parent ceiling Y`; plan/ro < edits < auto < full; unset parent = `edits`); over-privileged children run clamped with a `[mode] clamped to Y by parent <name>` tool row. `AG_MODE_CEILING=off` disables (unsafe).

## Per-agent env and keys

```sh
{{AG}} --dir <state> agents set w --env K=V --env-unset K --env-clear        # also on `agents add`
{{AG}} --dir <state> agents set w --claude-config-dir ~/.claude-work --subscription-only on   # 2nd Claude acct; strips API-key vars
{{AG}} --dir <state> agents env w                                            # effective env diff, secrets REDACTED
{{AG}} --dir <state> harness profile add P --instructions F --env K=V --env-unset K   # shared by profile users
```

Secret-looking keys (`*KEY*|*TOKEN*|*SECRET*|*PASSWORD*`) must be refs: `--env OPENAI_API_KEY='${MY_KEY}'`; literals rejected. `HOME`/`PATH` refused (use `--env PATH+=dir`). Keychain refs work too: `--env MY_TOKEN='${keychain:MYKEY}'` (Keychain service `ag`; resolved at spawn, REDACTED in `agents env`).

Route to an added provider (opencode backend, model `<provider>/<model>`; privacy + slots come from the registry; full provider/keys docs in skill `ag-cli` `references/keys-providers.md`):

```sh
{{AG}} --dir <state> providers add groq --key-ref keychain:MYKEY   # presets: groq|cerebras|openrouter|cloudflare|gemini|mistral|sambanova
{{AG}} --dir <state> providers list | show groq | rm groq
{{AG}} --dir <state> keys set MYKEY --from-env GROQ_API_KEY
{{AG}} --dir <state> keys test MYKEY --provider groq
{{AG}} --dir <state> agents add w --backend opencode --model groq/gpt-oss-120b --data work
```

Custom/local: `providers add ID --base-url URL --no-key [--zdr]`; list models: `models --backend api --provider ollama`.

## ACP transport (opt-in)

```sh
{{AG}} --dir <state> agents set w --transport acp         # gemini/cursor/opencode; --transport cli reverts
{{AG}} --dir <state> agents add w --backend acp --acp-cmd "my-agent --acp"
```

Pre-prompt failure -> `[acp] fallback to cli: <reason>` and one CLI rerun. Permissions answered from agent mode. `AG_ACP=0` disables. Verified: opencode; cursor/gemini need login.

## Roles

`roles list|show|add|set|rm|reset`. Presets: `planner` (assumptions, verifiable steps), `implementer` (minimal diffs), `reviewer` (terse bug/security). Per call `--persona`/`--system @file`; persist via `agents set --persona`.
