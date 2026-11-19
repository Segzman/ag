# Kit: token-saving toolkit (ag kit)

`ag kit` wires codegraph + context-mode + rtk + the caveman/ponytail/principles style **per agent**, through ag's scoped configs — never global CLI config writes, so backends that miss toksave's global wiring still get everything.

```sh
{{AG}} --dir <state> kit list                     # tools, install state, delivery per backend
{{AG}} --dir <state> kit status                   # config default + per-agent effective kit
{{AG}} --dir <state> kit on [--tools codegraph,ctx,rtk,style]   # default (config kit.default)
{{AG}} --dir <state> kit off                      # default none; per-agent --kit still wins
{{AG}} --dir <state> agents set NAME --kit codegraph,ctx        # list | all | none ("" = default)
{{AG}} --dir <state> kit doctor [--agent NAME]    # what actually reaches the model, per tool
```

## How delivery works

- claude: `--mcp-config` JSON; opencode: `mcp` in the generated `OPENCODE_CONFIG`; codex: `[mcp_servers.*]` in the scoped `CODEX_HOME/config.toml`; gemini/cursor: no verified scoped MCP hook (TODO, skipped + reported; style + rtk hint still reach them via the prompt).
- toksave `runmcp` proxy when installed, direct binaries otherwise. Missing tools are skipped and reported, never silently dropped.
- Profile + kit MCP servers merge into one scoped file (one flag/env var), kit wins name clashes.
- Style block: toksave section of `~/.claude/AGENTS.md` (TOKSAVE markers) or a built-in default, injected into the composed system prompt; skipped when the backend already loads it (opencode global AGENTS.md, project AGENTS.md/CLAUDE.md with the markers).

## Pitfalls

- Worktree seeding runs detached: `cp -a .codegraph <wt> && codegraph sync <wt>` or `codegraph init` (repo under 50k files). It never blocks launch; check with `events` type `kit_wt`. `AG_KIT_WT=fg` forces foreground (tests/debug).
- `kit doctor` reads the generated configs and composed prompt — trust it over model self-reports.
- Turns are tagged in the ledger (`kit=on|off`, `kit_tools`) so token savings are comparable later; per-agent `--kit none` is the A/B control.
