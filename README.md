# ag — CLI helper for autonomous agents

`ag` drives coding-CLI subagents from the terminal: headless turns
(`chat send`, `delegate`), background wake-ups (`wake`), live PTY
sessions (`spawn`/`snap`/`send`), and shared skills/MCP/memory via
harness profiles. Single Python file, stdlib only. State lives in
`./.agent/` (override with `--dir` or `$AGENT_CLI_DIR`). All commands
accept `--json` for machine-readable output.

## Contents

- [Install](#install)
- [Setup for coding agents](#setup-for-coding-agents)
- [Getting started](#getting-started)
- [TUI controls](#tui-controls)
- [Backends](#backends)
- [Native slash commands](#native-slash-commands)
- [Model selection](#model-selection)
- [Native limits](#native-limits-verified-from---help-no-remote-calls)
- [Harness profiles](#harness-profiles-shared-named-backendsskillsmemorymcp)
- [Wake](#wake-background-agents-wake-agents-on-completion)
- [Docs](#docs)
- [Limitations](#limitations-honest)

## Install

Requirements: Python 3.9+, POSIX (macOS/Linux; no Windows), git.
No Python packages. Full guide: [docs/INSTALL.md](docs/INSTALL.md).

```sh
git clone https://github.com/Segzman/ag.git
cd ag
./install.sh                  # installs standalone ag to ~/.local/bin/ag
```

Alternatives: `./install.sh --prefix DIR` installs to `DIR/bin/ag`;
`./install.sh --force` updates an existing install.

The installer copies only the single `ag` file: no sudo, no network or
backend setup, no shell-rc edits. Put the install dir on `PATH` for the
session (`export PATH="$HOME/.local/bin:$PATH"`) — details, custom
prefixes, updates, remote use over SSH, and uninstall in [docs/INSTALL.md](docs/INSTALL.md).

Backends are optional and installed/authenticated separately —
`ag agents doctor` reports what is present. The local `echo` backend
always works (no keys) for plumbing tests. Running from the checkout
(`./ag`) keeps working with or without installing; skills
(`.claude/skills/`) and docs stay in the clone.

Verify with a no-key smoke test (fresh state dir; agent state stays
inside it, while `selfcheck` uses its own temp dir):

```sh
STATE="$(mktemp -d)"                                        # fresh state dir, no collisions
./ag --dir "$STATE" selfcheck                               # built-in regression checks
./ag --dir "$STATE" agents add smoke --backend echo --role sub
./ag --dir "$STATE" chat send smoke "hi"                    # one headless turn, no keys
JOB="$(./ag --dir "$STATE" --json wake smoke "summarize this" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["job"])')"
./ag --dir "$STATE" wakes --wait "$JOB" --timeout 30        # wake is async: wait for done
./ag --dir "$STATE" chat log smoke                          # follow-up reply lands here
```

No cleanup step: the daemon may still finalize the session after the
job reports done, so leave the temp dir to the OS.

State dir: every `ag` invocation resolves state as `--dir` → `$AGENT_CLI_DIR` → `$PWD/.agent` (no upward search). Running from the wrong cwd silently creates a fresh roster that looks wiped but isn't. **Always pass `--dir` explicitly** (or export `AGENT_CLI_DIR` once per session); examples below use `./ag` from the checkout — add `--dir <state>` (or the export) to each command. Never commit state contents (`.agent/`, `approvals.json`, `history.jsonl`, `notes.jsonl`, `todos.json` are git-ignored).

Skills for coding agents live in `.claude/skills/`:

- `using-ag` — full command reference + recipes (spawn/snap/send, wake, harness profiles, context/compact). Load it before driving `ag`.
- `ag-delegate` — "have another agent do X": headless turns, fan-out, background wakes, session follow-ups, context handoffs. No external queue, no Linear dependency.

Share one setup across agents without touching home/global config:

```sh
./ag harness profile add docs --instructions ./AGENTS.md \
  --memory ./MEMORY.md --memory ./notes/ \
  --skills ./.claude/skills --mcp ./.mcp.json
./ag harness profile set oc --profile docs
```

## Setup for coding agents

1. Invoke via absolute path to this clone (`<repo>/ag`), one invocation per shell call. No `;`/`&&` chains around it.
2. Always pass `--dir <state>` (or per-session `AGENT_CLI_DIR`). Confirm the roster before writing (`agents list`).
3. Load `.claude/skills/using-ag/SKILL.md` before running commands; load `.claude/skills/ag-delegate/SKILL.md` before assigning work to another agent.
4. Never run TTY commands from agent context: `tui`, `attach`, `shell`, `handoff --exec`. Use `spawn` + `snap` instead; tell the user to run TUI commands themselves.
5. Delegate via `ag` (`chat send`, `delegate`, `wake`, `spawn --wake`, `context assign/show/checkpoint`) — see `ag-delegate`. Root repo guidance is in `AGENTS.md` (+ `custom.md` scope chain); scoped memory details in `docs/AGENT_CONTEXT.md`.

## Getting started

```sh
./ag agents doctor          # check backends (claude, opencode, gemini, codex, cursor, echo)
./ag agents list            # roster (default: claude orchestrator + oc sub)
./ag chat send claude "hi"  # one headless turn
./ag tui                    # landing dashboard (projects, chats, configurator)
./ag selfcheck              # built-in regression checks
```

Sessions (live PTY programs) are non-blocking; pings land in `events.jsonl`:

```sh
./ag spawn -- bash
./ag snap <id> [--clean]    # many readers OK
./ag send <id> "ls"         # or --key ctrl-c|esc|up|enter|tab
./ag attach <id>            # full terminal (Ctrl-] detaches, keeps running)
./ag kill [--force] <id>    # wait blocks only if you ask: ./ag wait <id>
```

Memory: `note`/`notes`, `todo add|list|done|clear`, `history`, `events`.
Approvals: dangerous `run` commands block → `approvals` → `approve <id>` (10 min) or re-run with `--force`.
Sharing: `harness show|export|import|link --to DIR` (secrets redacted, `~/.claude.json`/credentials never exported).

## TUI controls

Composer-first, OpenCode-style: just type — no insert mode, no `i`
key. Home is the initial screen: centered `ag` wordmark, prompt-led
charcoal composer (blue left rule) with `Build · <model> ·
<agent>`, projects grouped by agent workdir with two-line rows (status,
friendly model, turn count, last reply), quiet `cwd · status` bottom line.
`Enter` sends the draft to the selected agent and opens its chat; `/` at
the start opens the slash popup; `Esc` selects (`q` quits, arrows move,
any text returns to the composer). Session: type, `Enter` sends, `Esc`
back home (draft kept). Mouse clicks rows/popup/composer and the wheel
scrolls; keyboard does everything.

| Key | Home | Session |
|---|---|---|
| type | message (Enter sends + opens chat) | message (Enter sends) |
| `/` | command popup (filter · `↑↓` · `Tab` complete · `Enter` run · `Esc` keep draft) | same |
| `Enter` / `Esc` | open chat / select (`q` quits) | send / back home (draft kept) |
| `↑↓`/`PgUp`/`PgDn`/`tab` / `1-9` | select agent | scroll / switch target agent |
| `ctrl+p` | searchable command palette (everywhere; drafts kept) | same |
| `c` | configurator: provider/model/profile/behavior/length/memory/appearance | same |
| `m` / `M` | model picker / custom model string | same |
| `b` | backend picker (switch coding CLI, persists) | same |
| `P` | profile picker (shared harness profile, persists, clears session) | same |
| `R` | role (persona) picker | same |
| `H` | — | native handoff: suspends UI, execs backend TUI with profile env, restores |
| `/cmd` | `/projects /agents /config /model /backend /profile /new /delegate /context /compact /compact-now /theme /handoff /approve /help /quit` (`/harness` = legacy `/backend`) | same; `/cmd args` typed forms run verbatim |
| `n` | new agent (name, then backend; draft kept) | same |
| `d` | delegate (agent name, then task; draft kept) | same |
| `!cmd` | (via composer submit) | run shell now (e.g. `!git status`) |
| `a` | approve oldest pending dangerous command | same |
| `t` | cycle theme (`abyss`/`mono`/`ember`) | same |
| `?` | full key list | same |

Conversation length (`c` → Conversation length, or `ctrl+p` →
Conversation length, or `/compact`): first screen shows On/Off per
scope — Orchestrator, Subagents, This agent (with `Using … settings`
vs `Custom settings`) — plus `Summarize this chat now` and Back.
Enter a scope to change it: Automatic summaries (On/Off), When to
summarize (named presets with exact limits: Standard matches the role
defaults — orchestrators every 30 user turns or ~20,000 tokens keeping
10, subagents every 20 turns or ~20,000 tokens keeping 5; More often
is 15/~10k/keep 5 (orch) and 10/~10k/keep 3 (sub); Less often is
60/~40k/keep 15 (orch) and 40/~40k/keep 10 (sub); choosing never flips
On/Off), Advanced
settings (exact limits, kept turns, time limit; typing replaces the
shown value, `Enter` saves, `Esc` cancels), Back. A per-agent scope
always offers `Use … settings` to return to the shared role values.
`Esc` steps one level back; from the overview it returns to the
configurator (same row) or composer, drafts kept. `Summarize this chat
now` keeps the newest messages and reports busy/failure or `No older
messages to summarize yet. The newest N turns are kept.` Context
(`c` → Guidance & memory, or `ctrl+p` → Context for NAME) is the
read-only `get_agent_context` view: assignment, sources, warnings,
content.

Layout adapts: content max ~84 cols centered; logo/padding shrink at
short heights; single pane below 110 cols (sidebar only on wide);
40×16 stays usable (popup drops below the composer when the composer
sits at the top). Below 40×16 it asks for a resize instead of drawing
garbage. Live turns render as a rich timeline (reasoning as Thinking,
tool cards with honest status words — Running only when the backend
reports it, Done/Failed — diffs with `+-` kept, errors stay
visible). Previews: `docs/ui-previews/` (actual renderer captures, see
`docs/UI_DESIGN.md`). Launch with `scripts/ag-tui.command` (daily
`~/.agent` state); `./ag --dir … tui` for an explicit state dir.


Keys arrive via `get_wch` when available: arrows/keys stay keys (never typed as text), non-ASCII text stays text.

## Backends

`claude` orchestrates; subs via `opencode`/`gemini`/`codex`/`cursor`. `echo` is a local plumbing-test backend. Stateless backends replay recent history; `claude`/`opencode`/`cursor` resume by session id. Each backend CLI is installed and authenticated separately — `ag` never does that; `agents doctor` only reports what it finds. Roles (`roles list/add/set/show/rm`) set system prompts; per-agent `--persona` or `--system` (`@file` loads text) overrides.

## Native slash commands

Work in the TUI composer and headless chat (`./ag chat send claude "/help"`). Leading `\` is an alias (`\model`); other backslash text (paths) stays a normal message. Slash never reaches the model as prose: it runs locally and logs as tool lines (invisible to history replay). Unknown `/word` errors with help + handoff hint. A `/` glued to a known word (`/config/quit`) errors locally; absolute paths (`/Users/…`) stay messages.

```sh
./ag chat send claude "/help"               # list
./ag chat send claude "/harness list"       # backends (* = current)
./ag chat send claude "/harness use opencode"  # switch (alias: /backend ...)
./ag chat send claude "/model"                  # list models for claude's backend
./ag chat send claude "/handoff"            # interactive native session
./ag chat send claude "/profile list"       # shared profiles (* = this agent)
./ag chat send claude "/profile show docs"  # sources + effective files + unsupported
./ag chat send claude "/profile use docs"   # select (clears session); --none clears
# in TUI: type / for the popup (Tab completes, Enter runs, Esc keeps draft):
# /projects /agents /config /model [name] /backend [name] /profile [...] /
# /new /delegate /context /compact /compact-now /theme /approve /quit.
# b = backend picker, P = profile picker, H = exec handoff (suspend/restore),
# c = configurator (home + session), ctrl+p = command palette (optional alias).
# Backslash alias: \help \model \harness \handoff \profile
# (other backslash text like C:\path stays a normal message).
```

Switching preserves name/workdir/role/persona/system, remembers one model and one native session id per backend (never resumes a foreign SID or carries a custom model across backends), and refuses while a turn is running. `harness show|export|import|link` subcommands are unchanged.

## Model selection

```sh
./ag agents list                       # roster with backend/model/role
./ag agents set oc --model default        # reset to the ag-level default
./ag chat send oc "/model"             # list models for oc's backend
# in TUI: m = picker, M = custom model string, R = role picker
```

Ag-level default for OpenCode: an opencode agent whose model is `default`
(or missing, e.g. older rosters) runs as
`opencode/muse-spark-1.3-contributor-free` everywhere OpenCode runs through
ag — headless `chat send`/`delegate`/wake turns (`opencode run -m …`),
native handoff (`opencode -m …`, verified root flag), new agents
(`agents add`, TUI `n`), backend switches onto opencode, the `m` picker and
`/model list`. Explicit custom models always win (stored and passed
through); each backend remembers its own last model (custom IDs included)
and restores it on return (details: [docs/HARNESS_ROLES.md](docs/HARNESS_ROLES.md)). Other backends are untouched:
`default` still means "no `-m` flag".

## Native limits (verified from `--help`, no remote calls)

Bare `/foo` as a headless message is prose to every backend, so ag never
forwards it. The one verified headless native-command flag is opencode's:

```sh
./ag chat send oc --command review "path/to/file"  # opencode-only; args sent bare
```

`claude -p`, `gemini -p`, `codex exec`, `agent -p` expose no slash/command
flag (slash is interactive-TUI surface: `--disable-slash-commands` for claude,
in-TUI pickers elsewhere), so other backends reject `--command` instead of
faking it. Headless `/handoff` prints the interactive argv instead: `claude
[--resume SID]` and `agent [--resume SID]` preserve the session flag;
`opencode`/`gemini`/`codex` launch bare in the agent workdir (resume from the
in-TUI picker; ag keeps gemini turns stateless, codex headless turns resume via `exec --json`). In the TUI, `/handoff`
executes like `H` (below) instead of printing. Fallback anywhere
without a TTY: `ag spawn -- <argv>` then `ag attach <id>`. Handoff argv never
carries auto-approve/bypass flags. Unknown `/word` is never prose: it errors
with an explicit real route (`/handoff`, `H`, or `ag handoff <agent>`), naming
the backend TUI where that command actually runs.

Executable handoff (real path, not just printed argv):

```sh
./ag handoff claude              # print native argv + scoped profile env + attach guidance
./ag handoff oc opencode         # same for another agent/backend
./ag handoff claude --spawn      # tracked pty session (non-TTY callers get id + attach)
./ag handoff claude --exec       # exec native TUI now (TTY only, replaces ag process)
# in TUI: H or /handoff suspends curses on the main thread, execs the backend
# TUI with the selected profile env/config, always restores curses (finally),
# then logs the outcome as tool lines. Busy agents refuse. Slash text is
# never sent to the model: H and /handoff run locally.
```

One shared per-agent guard (cross-process): a backend turn (`chat send`,
`delegate`, wake worker) holds one flock guard for that agent; wake workers
take no second lock (no double-lock deadlock). Backend/profile/model switches
(`switch_backend`, `profile_set_agent`, `/model`, `agents set --backend/--model`)
probe the same guard plus live-handoff markers and refuse while the agent runs
in any process (`<agent> is busy ... retry when idle`). Tracked (`--spawn`)
and exec/TUI handoffs record markers (session liveness / pid) so switches are
refused for the whole native session; stale markers self-clean on next probe.

## Harness profiles (shared named backends/skills/memory/MCP)

Opt-in named sources shared across agents; selected per agent. Existing
`harness show|export|import|link` unchanged. All runtime files live under
`.agent/profiles_effective/<name>/`; no `~/.claude`, `~/.codex`, `~/.config`
writes, no global config mutation.

```sh
./ag harness profile add docs --instructions ./AGENTS.md \
  --memory ./MEMORY.md --memory ./notes/ \
  --skills ./.claude/skills --mcp ./.mcp.json
./ag harness profile list                    # profiles + per-agent selection
./ag harness profile show docs               # sources + effective files + unsupported notes
./ag harness profile validate docs           # ok/errors/warnings/unsupported (names only)
./ag harness profile status                  # validity + agents per profile
./ag harness profile set oc --profile docs   # select (keeps workdir/role/persona, clears session)
./ag harness profile set oc --none           # clear
./ag harness profile rm docs [--force]       # refuses while agents use it
```

`.claude` -> OpenCode example (skills discovered natively):

```sh
./ag harness profile add frontend --skills ./.claude/skills --mcp ./.mcp.json
./ag harness profile set oc --profile frontend
# opencode gets: OPENCODE_CONFIG=<state>/profiles_effective/frontend/opencode.json
# (translated mcp + instructions:[.../INSTRUCTIONS.md]) and
# OPENCODE_CONFIG_DIR=<state>/profiles_effective/frontend/opencode-config-dir
# with skills/*/SKILL.md copied from .claude/skills (native discovery path).
```

Custom Codex config example (scoped home, MCP TOML):

```sh
./ag harness profile add coder --mcp ./.mcp.json --codex-config ./codex-extra.toml
./ag harness profile set cx --profile coder
# codex gets: CODEX_HOME=<state>/profiles_effective/coder/codex-home
# (config.toml = [mcp_servers.*] translation + codex-extra.toml passthrough;
# auth.json symlinked when present, never copied to reports; profile + user
# skills mirrored under codex-home/skills so the scoped home keeps access).
```

Shared named profile across agents (headless + wake + native):

```sh
./ag harness profile set claude --profile docs
./ag harness profile set oc --profile docs   # N agents -> 1 profile
./ag chat send oc "hi"                       # headless turn uses docs (instructions/memory/MCP)
./ag wake oc "summarize"                     # wake worker runs run_turn -> same profile env/argv
./ag handoff oc                              # native TUI argv + docs env/config
```

What each backend actually gets (verified flags only):

| backend | instructions+memory | skills | MCP | scoped config |
|---|---|---|---|---|
| claude | `--append-system-prompt` | `--plugin-dir <eff>/claude-plugin` (+ prompt fallback) | `--mcp-config <eff>/claude_mcp.json` | argv only, no global writes |
| opencode | `instructions: [<eff>/INSTRUCTIONS.md]` in generated config | `OPENCODE_CONFIG_DIR` with `skills/*/` (incl. `.claude/skills`) | translated `mcp` in generated config | `OPENCODE_CONFIG=<eff>/opencode.json` |
| codex | prompt lead block | profile + user skills mirrored under scoped `CODEX_HOME/skills` (+ prompt context) | `[mcp_servers.*]` in `<eff>/codex-config.toml` via scoped `CODEX_HOME`: stdio `command/args/env`, remote `url` + `http_headers` (literals) + `env_http_headers` (`{env:V}`/`${V}`/`$V` refs) | `CODEX_HOME=<eff>/codex-home` (auth symlinked, custom `codex_config` appended) |
| gemini/cursor/echo | prompt lead block | prompt context only (reported unsupported) | reported unsupported, never silently dropped | — |

MCP source accepts Claude-style `{"mcpServers": ...}`, opencode-style
`{"mcp": ...}`, or bare `{name: entry}`; stdio (`command`+`args`+`env`) and
remote (`url`+`headers`) preserved verbatim, incl. `{env:X}` / `${X}` refs
(which OpenCode itself expands). Codex mapping verified against
`developers.openai.com/codex/mcp` + `codex mcp add --help` + `codex-rs/mcp_cmd`
(`http_headers`/`env_http_headers`/`bearer_token_env_var`): only a header value
that embeds an env ref inside a larger string (e.g. `Bearer {env:X}`) has no
mapping -> reported (header names only, values never printed).
`show`/`validate`/`status` print paths/names/counts, never env values; tests
use fake fixture credentials.

## Wake: background agents wake agents on completion

```sh
./ag agents add w --backend echo --role sub
./ag wake w "summarize this"           # one background chat send, returns now
./ag wakes                             # queued|running|done|failed + worker link
./ag spawn --wake w --wake-message "summarize this" -- ./long-job.sh
# on session end (exit, kill, kill --force) at most one follow-up is launched:
#   [wake] session <id> (<cmd>) exited <code>. <message>
./ag chat log w                        # follow-up lands here
./ag events --limit 20                 # wake / wake_done / wake_fail trail
./ag kill <worker-sid>                 # stop a wake worker (TERM marks job failed)
```

Each wake is a durable job (`wake/jobs/<id>.json`) run by a tracked worker session (`__wake-<agent>-<id>`, visible in `sessions`/`status --json`). Turns on the same agent (`chat send`, `delegate`, wake workers) are mutually excluded via one shared per-agent flock guard held inside `run_turn` (no FIFO or arrival-order guarantee; the wake worker takes no second lock); different agents run in parallel. Backend/profile/model switches probe the same guard and refuse while the agent runs anywhere. `spawn --wake` launches at most once (atomic claim, not guaranteed completion or delivery); worker completion never re-fires (no callback loops). Argv is shell-free, so `; touch evil` in messages stays inert text. `--on-exit` still runs first and is unchanged.

## Git worktree per agent (safe parallel fan-out)

Agents sharing a `dir` clobber each other's files. Give each its own worktree:

```bash
ag agents add w1 --backend claude --dir ~/proj --worktree [--base main]
# branch ag/w1 from --base (default HEAD), worktree at ~/proj.ag-wt/w1 (outside the repo); agent dir is set to it
ag agents set existing --worktree        # move an idle agent into one (refused while busy)
ag agents list                           # worktree agents show [ag/<name>] after the dir; --json has `worktree`
ag agents rm w1                          # removes the worktree if clean, keeps branch ag/w1
ag agents rm w1 --force                  # dirty: drops worktree + uncommitted work, still keeps the branch
```

`add` prints the branch so an orchestrator can merge it (`git merge ag/w1`); ag has no merge command. An existing identical worktree is reused; any other clash on branch or path errors with a hint.

## Docs

- [Harness selection + behavior presets](docs/HARNESS_ROLES.md): `harness use/current`, per-backend models, editable/reset roles.
- [Wake control: timeout, cancel, kill](docs/WAKE_CONTROL.md): `chat send`/`delegate --timeout`, wake `--max-runtime`, per-job flock, CLI 124.
- [Multi-host portable snapshots](docs/MULTI_HOST.md): `hosts`, `sync pull/_export` over outbound SSH, no automatic failover.
- [Muse feedback on brief 1–8](docs/MUSE_FEEDBACK.md): what is covered vs deferred (queue/dependencies, ephemeral agents, post-turn hooks; no concurrency cap).

## Limitations (honest)

- Wake is at-most-once *launch*, not completion: a crash between claim and enqueue, or a `kill -9` of a worker, can lose/stall a wake. Check `events` for a missing `wake` after `exit`, `wakes` for jobs whose session is dead. No retry scheduler.
- Daemon crash (`kill -9` daemon): session is marked `stale` (`exit=-1`, one `stale` event, prompt `wait`/`snap` return) and the pending wake does **not** fire — `wake_fired:false` stays visible in `status`. Re-wake manually if needed.
- `chat send`/`delegate --timeout` (default 1800, `0` = unlimited) bounds backend runtime; expiry kills the backend group and exits 124. Wake jobs bound it separately via `--max-runtime` (budget starts at guard acquisition). While a turn runs, the agent's guard is held: backend/profile/model switches (any process) refuse, and other turns on the same agent block behind it.
- No queue fairness/priority/FIFO; concurrent turns on one agent are mutually excluded with no arrival-order guarantee.
- Message cap 4000 chars (truncated). `wake/jobs` history is never pruned by `forget`; delete files manually.
- POSIX only for `spawn`/workers (`wake` enqueue works anywhere, workers need the pty daemon). TUI needs POSIX curses; no Windows support.
- `ag run echo --json` (no `--`) still treats `--json` as global; use `ag run -- echo --json` to pass flags to the child. Multi-token commands are now shell-quoted (`shlex.join`); old approvals for such commands re-prompt once.
- State writes are atomic only for `status.json`/wake jobs; other JSON files can corrupt on crash (agents/todos fall back to defaults — back up `.agent/` before risky ops). Logs (`output.log`, `events.jsonl`) are never rotated.
- Wrapping is cell-based everywhere (CJK wide = 2, combining/ZWJ/skin-tone glued, never split); below the 40x16 floor a clean resize message shows. Side panels hide below 110 cols by design. `NO_COLOR=1` zeroes color pairs (selection → reverse, errors → `!`, diffs → `+-`, tools → status words); `AG_ASCII=1` swaps UI glyphs to ASCII. Mouse is best-effort with full keyboard parity.
