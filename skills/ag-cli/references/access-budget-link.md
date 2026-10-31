# Access, usage, budgets, link

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
