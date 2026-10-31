# Harness profiles and multi-host

## Harness profiles (shared skills/MCP/memory, no global writes)

```sh
{{AG}} --dir <state> harness profile add docs --instructions ./AGENTS.md --memory ./MEMORY.md --skills ./.claude/skills --mcp ./.mcp.json
{{AG}} --dir <state> harness profile set oc --profile docs    # --none clears; clears session
```

`harness profile list|show|validate|status|rm`. `harness show|export|import|link` share profiles (secrets redacted). Files land in `<state>/profiles_effective/<name>/`. gemini/cursor: skills+MCP unsupported (prompt only, reported). Never write home/global config. ag picks the calling harness's profile (`AG_HARNESS` override > parent processes > env); `{{AG}} route [--profile P]` prints `harness: <name> (via ...)`.

## Multi-host

```sh
{{AG}} --dir <state> hosts add mac --ssh-json '[...]' --remote-ag /abs/ag --remote-state /abs --remote-project /abs --local-project ~/p
{{AG}} --dir <state> hosts list | check mac
{{AG}} --dir <state> sync pull mac worker --file f.txt | sync list
{{AG}} --dir <state> sync resume SNAPSHOT --name N --project P --source-stopped [--run]
```

Outbound SSH only, explicit handoff, no auto failover. Details: `docs/MULTI_HOST.md`.
