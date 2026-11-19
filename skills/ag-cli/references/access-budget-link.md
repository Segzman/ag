# Access, usage, budgets, link

## Access, usage, budgets

```sh
{{AG}} --dir <state> access show [--scope global|project] [--data work|public]
{{AG}} --dir <state> access allow GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> access deny GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> access rm GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> usage [--by provider|model|agent|project|day] [--since 1d|7d|30d] [--json]
{{AG}} --dir <state> agents set NAME --budget-ctx on|off
{{AG}} --dir <state> budget show
{{AG}} --dir <state> budget set [--daily USD] [--monthly USD] [--provider P --rpd N]
```

`access` = `routing.json` allow/deny globs over `backend/model` + `provider/*` (deny wins; per-`--data work|public`). Ledger `$AG_CACHE_HOME/usage.jsonl`. `budget` refuses turns (exit 2) unless `--force`; 80% warns `[budget]`.

Quota awareness (all opt-in, in `routing.json` `usage`): `usage --json` adds `windows` (today / rolling 5h / 7d / 30d per backend + model), `limits` (% of `usage.limits.<backend|provider|model-prefix>` = `{rolling_5h|weekly|monthly: tokens | {tokens|requests|cost}}`), `limit_hits` (recorded plan-limit hits + reset times), and `opencode_stats` (parsed `opencode stats --days 30 --models --cost` when installed). `usage.inject` (default OFF; per-agent `agents set NAME --budget-ctx on|off` wins) appends one `budget: ...` line to wake/chat prompts. `usage.adapt` (default OFF, `usage.adapt_threshold` 85, `usage.adapt_effort` on) makes turns + `route` prefer the agent's `--fallback` or the cheapest same-backend model, effort one notch down, when a backend is >= threshold % or parked; adaptations log as `[usage-adapt]` + a `usage_adapt` event.

## Policy compiler

```sh
{{AG}} policy check --file ag-policy.json                       # validate schema; nonzero exit on bad key/value
{{AG}} policy targets [--file ag-policy.json] [--json]          # coverage: target x field -> native/approx/none (+ notes for this policy)
{{AG}} policy render --file ag-policy.json [--target NAME|all] [--out DIR] [--dry-run]
{{AG}} policy compile [--file .agent/policy.toml] [--backend claude|opencode|codex] [--out DIR] [--check]  # compile the project policy for one backend; --check = diff + nonzero exit on drift
{{AG}} policy log [--limit 20]                                  # policy events from the ledger (denials, compiles, checks)
```

Policy JSON: `{version:1, permission_floor:"ro|edits|auto", allowed_models:[], denied_models:[], mcp_allow:[], mcp_identity:{}, secret_read_ban:[globs], network:"on|off"}`. Compile-era keys (same schema, JSON or TOML): `allow_cmds:[]`, `deny_cmds:[]`, `deny_paths:[globs]` (merged into `secret_read_ban`), `max_mode:"ro|edits|auto|full"` (ceiling; the rendered floor is capped at it), `models_allow:[globs]`, `models_deny:[globs]`, `data_class:{work:{models_allow:[], models_deny:[]}, public:{...}}` (per-class globs override the top level; work with no list keeps the existing `--data work` gating). Policy file discovery for `policy compile`, wake/chat and `run`: `--file` / `$AG_POLICY_FILE`, then `<state dir>/policy.toml|json` (i.e. `.agent/policy.toml`), then `./.agent/policy.toml|json`, then `./ag-policy.json`. Wake/chat auto-compiles the agent's backend before launch (opencode via the `OPENCODE_CONFIG` overlay, claude into `CLAUDE_CONFIG_DIR/settings.json` or `<workdir>/.claude/settings.json`, codex defaults into `CODEX_HOME/config.toml` when unset) and refuses models/modes the policy blocks; `run` refuses commands hit by `deny_cmds`/`allow_cmds`. Every ag-enforced denial lands in the ledger (`events.jsonl`, type `policy`) — read it with `policy log`. Unverified backend keys are never guessed: Codex gets `# TODO(policy)` comments for command/model/data rules it cannot express. `--target` takes any name from `policy targets` (claude, codex, codex-config, gemini, gemini-policy, qwen, cursor, copilot, amp, goose, aider, aider-ignore, cline, roo, roo-modes, kilo, continue, windsurf, crush, augment, factory, grok, kiro, zed, opencode) or `all`. `--out DIR` writes `DIR/<target>/<file>`; `--dry-run` prints a unified diff without writing. Before relying on a file, check `policy targets`: `none` = not rendered for that agent, `approx` = partial. Vendor field names are as of 2026-11; re-check each release.

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
