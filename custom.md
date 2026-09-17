# custom.md — agent-cli repo guidance (root scope)

Single-file Python CLI (`ag`, stdlib only, no install) for agent
orchestration. State in `./.agent/` (`--dir` / `$AGENT_CLI_DIR` override).
Always pass `--dir` explicitly: running from another cwd silently starts a
fresh roster. See `docs/AGENT_CONTEXT.md` for scoped context details.

## Architecture (truthful)

- `ag` is one ~4300-line Python file: argparse CLI + JSON state files +
  subprocess/pty backends. No services, no deps, POSIX only.
- Backends are headless CLIs (`claude -p`, `opencode run`, `codex exec`,
  `gemini -p`, `agent -p`, local `echo` stub). Stateless ones replay recent
  history; `claude`/`opencode`/`cursor` resume by session id.
- One shared per-agent flock guard (`run_turn` owns it) excludes concurrent
  turns; backend/profile/model switches probe it and refuse while busy.
- Harness profiles share skills/MCP/memory across agents via scoped files
  under `.agent/profiles_effective/` — never home/global writes.
- Scoped agent context (`docs/AGENT_CONTEXT.md`): `custom.md` chain +
  explicit `.md` files + file-backed memory under `.agent/context/`,
  injected into `run_turn` prompts. Native TUI sessions pull it manually
  via `ag context show NAME`.

## Delegation + model preference

- Delegate via `ag`: `chat send` (headless turn), `delegate` (orchestrator
  logs to its chat), `wake` (background, at-most-once launch).
- Ag-level OpenCode default model is `opencode/muse-spark-1.3-contributor-free`
  (stored `default` on opencode agents resolves to it). Explicit custom
  models always win. Prefer `ag` delegation + Muse Spark for sub work.

## Commands (heads-up)

```sh
./ag agents doctor && ./ag agents list
./ag chat send <name> "task"        # headless turn (context auto-injected)
./ag context show <name>            # what this agent was given + warnings
./ag context checkpoint <name> --file ckpt.md   # curated summary, shared handoff
./ag compact show <name>               # auto-compaction settings/stats (default off)
./ag status && ./ag sessions && ./ag events --limit 20
python3 tests/test_ag_context.py    # scoped-context acceptance (echo only)
```

## Ownership (concurrent work)

- UI owner: `ag` themes/render/TUI/`KEYS_HELP`, `tests/test_ag_ui.py`,
  README TUI sections — do not touch.
- Context owner: helpers near `eff_system`, `run_turn` hook, CLI
  parser/`do_context` dispatch, `tests/test_ag_context.py`,
  `docs/AGENT_CONTEXT.md`, this file, scoped examples.
