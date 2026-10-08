# ag — CLI + agent orchestrator

`ag` is a single-file Python CLI (stdlib only, POSIX) with two halves:

- **CLI** — logged commands with approvals, non-blocking PTY sessions, secret prompts, notes/todos, live model discovery, a TUI.
- **Agents** — drives coding-CLI backends (`claude`, `opencode`, `codex`, `gemini`, `cursor`, plus a keyless `echo`): headless turns, background wakes, per-job model routing, worktree isolation, per-turn checkpoints, scoped context handoff.

State lives in `./.agent/` (override: `--dir`, `$AGENT_CLI_DIR`). Every command takes `--json`.

## Contents

- [Quickstart](#quickstart) · [Capability matrix](#capability-matrix)
- [Part 1 — CLI](#part-1--cli): [Install](#install) · [State dir](#state-dir) · [Sessions](#sessions-pty) · [run / approvals / rtk](#run-approvals-rtk) · [Secrets](#secrets) · [Notes / todos](#notes-and-todos) · [Models](#models) · [Setup](#setup) · [TUI](#tui)
- [Part 2 — Agents](#part-2--agents): [Roster](#roster-and-backends) · [Routing](#routing) · [Delegation / wake](#delegation-and-wake) · [Reliability](#reliability) · [Isolation](#isolation-worktree-per-agent) · [Checkpoints](#checkpoints) · [Usage](#usage) · [Context](#context-and-handoff) · [Harness profiles](#harness-profiles) · [Roles](#roles)
- [Docs](#docs) · [Limitations](#limitations-honest)

## Quickstart

```sh
git clone https://github.com/Segzman/ag.git && cd ag
./install.sh                      # -> ~/.local/bin/ag (single file; no sudo, no network)
ag setup                          # models -> scope -> harness skills -> routing preset
ag --dir "$PWD/.agent" agents doctor     # which backends are installed
ag --dir "$PWD/.agent" agents list
ag --dir "$PWD/.agent" chat send claude "hi"
```

No-key smoke test (own state dir; `selfcheck` uses a temp dir):

```sh
STATE="$(mktemp -d)"
./ag --dir "$STATE" selfcheck
./ag --dir "$STATE" agents add smoke --backend echo --role sub
./ag --dir "$STATE" chat send smoke "hi"
JOB="$(./ag --dir "$STATE" --json wake smoke "summarize this" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["job"])')"
./ag --dir "$STATE" wakes --wait "$JOB" --timeout 30
./ag --dir "$STATE" chat log smoke
```

## Capability matrix

| backend | resume | rtk | skills | MCP | profile config |
|---|---|---|---|---|---|
| claude | session id | own hook | `--plugin-dir` | `--mcp-config` | argv only |
| opencode | session id | own plugin | `OPENCODE_CONFIG_DIR/skills` | translated `mcp` | `OPENCODE_CONFIG` |
| codex | `exec --json` resume | prompt hint | mirrored in scoped `CODEX_HOME/skills` | `[mcp_servers.*]` | `CODEX_HOME` |
| cursor | session id | prompt hint | prompt only (reported) | reported unsupported | — |
| gemini | stateless (history replay) | prompt hint | prompt only (reported) | reported unsupported | — |
| echo | stateless | — | — | — | — |

Native command flag (`chat send --command`) is opencode-only; others reject it. Backend CLIs are installed/authenticated by you; `ag agents doctor` only reports.

---

# Part 1 — CLI

## Install

Requires Python 3.9+, POSIX, git. `./install.sh [--prefix DIR] [--force]` copies only the `ag` file (default `~/.local/bin/ag`); no rc edits. Put the dir on `PATH`. Running `./ag` from the checkout also works. Details, SSH use, uninstall: [docs/INSTALL.md](docs/INSTALL.md). Tip: symlink `~/.local/bin/ag` to the checkout's `ag` so updates land in place.

**Updates.** ag checks GitHub at most once a day, in a detached background process, and applies updates on the next run. A git checkout gets `fetch` + `merge --ff-only`, but only when the tree is clean, tracking a remote, and not ahead; local commits or edits are never touched. A copy install downloads `ag` from `master`, checks that it compiles, and swaps it in atomically. `ag update` runs it now, `ag update --status` shows state, `ag update --auto off|on` toggles (`~/.config/ag/config.json`), and `AG_AUTO_UPDATE=0` disables per shell. Log: `~/.cache/ag/update.log`.

## State dir

Resolution, no upward search: `--dir` -> `$AGENT_CLI_DIR` -> `$PWD/.agent`. Wrong cwd silently creates a fresh roster (looks wiped, isn't). **Always pass `--dir`.** Never commit `.agent/`, `approvals.json`, `history.jsonl`, `notes.jsonl`, `todos.json`.

For coding agents driving `ag`: absolute path to the binary, one invocation per shell call (no `;`/`&&` chains), never run TTY commands (`tui`, `attach`, `shell`, `handoff --exec`; use `spawn` + `snap`). The skills `ag-cli` and `ag-agents` encode this; see [Setup](#setup).

## Sessions (PTY)

Live programs, non-blocking; pings land in `events.jsonl`.

```sh
ag spawn -- bash                  # returns instantly; --name --cwd --env --on-exit
ag snap <id> [--clean] [--since OFFSET]    # many readers OK
ag send <id> "ls"                 # or --key ctrl-c|ctrl-d|esc|up|down|left|right|enter|tab, --no-enter
ag wait <id> --timeout 30         # only blocking command; bound it
ag attach <id>                    # full terminal (TTY; Ctrl-] detaches)
ag kill [--force] <id>
ag sessions | status | events --limit 20
ag forget <id>                    # wipe logs (auth traces)
```

## run, approvals, rtk

```sh
ag run -- git status              # logged to history.jsonl; `--` needed when the child takes flags
ag approvals ; ag approve <id>    # dangerous command blocked -> approve (10 min) or re-run --force
ag history
```

Also `read|write|edit|ls|grep` file ops. Multi-token commands are shell-quoted (old approvals re-prompt once).

**rtk output compaction:** if `rtk` is on PATH, `run` executes the rtk-rewritten command (result has `rtk`). `run --raw` skips it per call; `AG_RTK=0` disables. claude/opencode use their own rtk hooks; codex/gemini/cursor get a prompt hint. `agents doctor` shows coverage.

## Secrets

Sudo password, SSH passphrase, OTP/PIN: ag shows a native macOS dialog (masked, OK/Cancel) and passes the value straight to the program. The model never sees it.

```sh
ag spawn -- sudo make install     # prompt detected -> dialog -> typed into PTY
ag send <id> --secret             # value from dialog (getpass on a non-mac TTY)
ag askpass "Password:"            # askpass protocol; Cancel = exit 1
ag spawn --no-secret-popup -- ... # opt out per session; AG_SECRET_POPUP=0 globally
```

- **PTY detection:** output idle + (echo off in canonical mode with a prompt-like last line, or a last line matching `password|passphrase|passcode|verification code|one-time|otp|2fa|pin|security code|token ... :/?`). One dialog at a time; the same prompt never pops twice. `secret_prompt` event logs outcome `ok|cancel|error`, never the value.
- **Backend turns and spawned sessions:** env gets `SUDO_ASKPASS`, `SSH_ASKPASS` (+ `SSH_ASKPASS_REQUIRE=force`), `GIT_ASKPASS` -> `<state>/bin/ag-askpass`; `<state>/bin` is prepended to `PATH` with a `sudo` shim adding `-A` (unless `-A/-S/-n` given). Your own variables are never overridden. A still-blocked prompt logs `secret_needed` and adds one retry note. `ag askpass` answers only when its parent process is sudo/ssh/git, so an agent cannot read a secret back.
- **Guarantee:** never written to disk in plaintext; lives in memory and pipes only (osascript -> ag -> PTY fd, or askpass stdout -> sudo/ssh/git). `input.log` has `[secret input redacted]`.
- **Limits:** the receiving program gets plaintext; Python can't zero strings; a program that echoes its own input can leak to `output.log`; keyword detection is heuristic (Cancel sends nothing); the parent-name check can be spoofed, so every dialog names the requesting command in `[brackets]`. macOS only (`osascript`); elsewhere injection/detection are off.

## Notes and todos

`ag note "..."` / `ag notes` · `ag todo add|list|done|clear` · `ag history` · `ag events`.

## Models

```sh
ag models [--backend B] [--refresh] [--json]
```

Live discovery: opencode (`opencode models`), codex (`~/.codex/models_cache.json`), cursor (`agent --list-models`); claude and gemini are static aliases (their CLIs have no list command). Cache `~/.cache/ag/models.json`, TTL 24h, refreshed automatically when stale; `--refresh` forces. A failing source keeps its last good list and records `error`. The built-in static list stays as fallback; pickers prefer the cache. `default` model = no `-m` flag, except opencode where it means `opencode/muse-spark-1.3-contributor-free` everywhere ag launches opencode (details: [docs/HARNESS_ROLES.md](docs/HARNESS_ROLES.md)).

## Setup

```sh
ag setup [--scope global|project] [--harness claude,opencode,codex|all]
         [--preset cost-first|balanced|quality-first]
         [--set JOB=claude:ALIAS] [--set JOB=ag:BACKEND/MODEL]
         [--claude-md|--no-claude-md] [--yes] [--dry-run]
```

Interactive on a TTY: refresh models, pick scope (default global), harnesses (default: detected), preset, then per-job model choices, then confirm. Flags make it fully scriptable. It writes:

1. `routing.json` for the scope (see [Routing](#routing)).
2. The `ag-cli` and `ag-agents` skills (rendered from `skills/` with your `ag` path, routing and scope) into each harness skill dir:

| harness | global | project |
|---|---|---|
| claude | `~/.claude/skills/<name>/` | `<project>/.claude/skills/<name>/` |
| opencode | `~/.config/opencode/skills/<name>/` | `<project>/.opencode/skills/<name>/` |
| codex | `~/.codex/skills/<name>/` | `<project>/.agents/skills/<name>/` |

3. claude + global (unless `--no-claude-md`): the routing block in `~/.claude/CLAUDE.md` between `<!-- ag:routing:start -->` / `<!-- ag:routing:end -->`; appended if markers are absent, other content untouched.

Writes are atomic and printed; `--dry-run` writes nothing and prints a diff; nothing is ever deleted. The repo's old `.claude/skills/using-ag` and `ag-delegate` were removed; `ag setup --scope project --harness claude` renders their replacements (`ag-cli`, `ag-agents`). Old copies elsewhere are left alone; delete by hand once the new skills are in.

## TUI

`ag tui` (or `scripts/ag-tui.command`): composer-first, no insert mode — just type. Home lists projects grouped by agent workdir; `Enter` sends to the selected agent and opens its chat; `/` opens the slash popup; `Esc` selects (`q` quits). Mouse supported; keyboard has full parity. Needs a TTY, at least 40x16 (sidebar only at 110+ cols). `NO_COLOR=1`, `AG_ASCII=1` supported. `ag keys` prints the cheat-sheet; layout/design in [docs/UI_DESIGN.md](docs/UI_DESIGN.md), captures in `docs/ui-previews/`.

| Key | Action |
|---|---|
| type / `Enter` | message / send (Home: also opens chat) |
| `/` | command popup (`↑↓`, `Tab` complete, `Enter` run, `Esc` keep draft) |
| `ctrl+p` | command palette |
| `Esc` | Home: select; session: back home (draft kept) |
| `↑↓ PgUp PgDn tab 1-9` | select agent / scroll / switch target |
| `c` | configurator (provider, model, profile, behavior, conversation length, memory, appearance) |
| `m` / `M` | model picker / custom model string |
| `b` / `P` / `R` | backend / profile / role picker |
| `H` | native handoff (suspend UI, exec backend TUI with profile env, restore) |
| `n` / `d` | new agent / delegate |
| `!cmd` | run shell now |
| `a` | approve oldest pending dangerous command |
| `t` | cycle theme (`abyss`/`mono`/`ember`) |
| `?` | full key list |

Slash commands run locally and never reach the model; they work in the composer and headless (`ag chat send claude "/help"`; `\` is an alias): `/projects /agents /config /model /backend /profile /new /delegate /context /compact /compact-now /theme /handoff /approve /help /quit` (`/harness` = legacy `/backend`). Unknown `/word` errors with a hint; `/Users/...` paths stay messages. Conversation-length presets: orchestrators summarize every 30 turns / ~20k tokens keeping 10, subagents every 20 / ~20k keeping 5 (see [docs/AUTO_COMPACTION.md](docs/AUTO_COMPACTION.md)).

---

# Part 2 — Agents

## Roster and backends

`claude` orchestrates; subs run on `opencode`/`gemini`/`codex`/`cursor`; `echo` is the keyless test backend. Default roster: `claude` (orchestrator) + `oc` (sub).

```sh
ag agents doctor | list
ag agents add w --backend opencode --role sub [--model ID] [--dir D] [--persona P|--system TEXT|@file] [--job JOB]
ag agents set w --backend B --model ID --persona P --worktree      # refused while busy
ag agents rm w [--force]
ag harness use w codex ; ag harness current w     # switch backend; remembers one model + one session id per backend
```

Switches preserve name/workdir/role/persona, never resume a foreign session id, and refuse while a turn runs (one cross-process flock guard per agent, also covering live native handoffs).

## Routing

Pick a **job type**, then use the routed model. Fixed keys: `mechanical` (scripts, rote edits, boilerplate, lookups), `implement` (clear spec), `review` (review, verification), `debug` (failing tests, root cause), `plan` (design, architecture), `hardest` (ambiguous, high-stakes, security).

Each job maps to a Claude alias (for Claude Code subagents' `model:`) and an ag backend/model (for ag sub-agents). Preset `cost-first` (default):

| job | claude | ag |
|---|---|---|
| mechanical | haiku | opencode / opencode/muse-spark-1.3-contributor-free |
| implement | sonnet | claude / sonnet |
| review, debug, plan | opus | claude / opus |
| hardest | fable | claude / fable |

Also `balanced` (review -> sonnet) and `quality-first` (mechanical sonnet, implement opus, rest fable). Unset ag entries fall back to opencode muse-spark.

Storage: global `~/.config/ag/routing.json`; project `<git toplevel or cwd>/.agent/routing.json`; shape `{"version":1,"preset":"cost-first","jobs":{job:{"claude":...,"ag":{"backend":...,"model":...}}}}`. Effective routing = global overlaid per job by project.

```sh
ag setup                      # create/change routing (+ skills)
ag route [JOB] [--json]       # effective routing + source of each entry (global/project/preset-default)
ag agents add w --job mechanical        # backend+model from routing when --backend omitted
ag chat send w "task" --job implement   # delegate / wake / chat send: records `job` only, no backend switch
```

## Delegation and wake

```sh
ag chat send w "task" [--timeout S] [--job J]   # one headless turn; read with `ag chat log w`
ag delegate w "task"                            # orchestrator assigns, logged in its chat
ag wake w "task" [--max-runtime S] [--timeout S] [--notify X] [--on-done X] [--job J]
ag wakes [--agent w] [--limit N] [--wait JOB --timeout S] [--cancel JOB]
ag spawn --wake w --wake-message "task" -- ./long-job.sh    # follow-up when the session ends
ag chat send w --command review "path"          # opencode native command only
```

- Same agent serializes (flock + FIFO ticket queue, see below); different agents run in parallel. Fan-out = different agents.
- `--timeout` (default 1800, `0` = none) is a hard backend budget starting after guard acquisition (lock wait excluded); expiry kills the backend group and exits 124. `wake --max-runtime` is the same for background turns; `wake --timeout` only marks `stalled`.
- A wake is a durable job (`wake/jobs/<id>.json`) run by a tracked worker session `__wake-<agent>-<id>`. `queued` = guard not yet acquired. `kill <worker-sid>` or `wakes --cancel` stops one (job `failed`).
- `spawn --wake` fires at most once per session (exit/kill/force-kill all count); worker completion never re-fires. Messages are argv, never shell-interpreted. Trail: `events` (`wake`, `wake_done`, `wake_fail`).
- Always-on bots: `spawn --wake` plus host cron/launchd re-issuing `ag wake`. ag has no scheduler.

- `--request-id K` on `wake`/`delegate`/`chat send` makes the call idempotent: receipt `<state>/receipts/<sha1(cmd+agent+K)>.json` (O_EXCL). Same payload replays the original result (wake: same job + `replayed:true`; others: stored reply, or `status:in_progress` + ref while running); different payload = `request id conflict`; a failed original replays its failure. `ag receipts gc [--days 7]` prunes.

## Delegated-completion cohorts
`ag wake|delegate <child> "task" --parent <agent> [--group GID] [--quiet S]` runs the child as a background wake job tagged `parent`/`group` (`delegate` without `--parent` is unchanged). Group state: `<state>/wake/groups/<gid>.json` (pending/done/delivery_jid). When the last pending child finishes, ONE wake is sent to the parent: `[delegated] 3/3 tasks finished (2 completed, 1 failed; outcome: failed). ...` with each child's summary (marked automated: tool results, not user instructions). A still-queued delivery is rewritten in place instead of relaunched. `--quiet S` delivers partial results when S seconds pass (checked on each child finish; no daemon). `ag wakes --group GID [--json]` shows the cohort; `--stop` sets disposition=stopped (never delivers). Launch failure is recorded in the group (`deliver_error`) and event `cohort_deliver_fail`.

## Queue, stop, steer

```sh
ag queue w [--json]                  # waiting turns in run order: position, id, prio, source, pid, text
ag queue w --cancel ID               # drop one waiter (its process exits: "cancelled from queue")
ag queue w --hold | --release        # HOLD: no new turn starts; waiters keep waiting
ag stop w [--cascade]                # hold + cancel waiters + interrupt running turn + cancel wakes
ag chat steer w "text" [--json]      # interrupt running turn, run this next on the same session
```

- Every turn (chat/delegate/wake) takes a ticket `queue/<agent>/<prio>-<seq>-<id>.json` and starts only when it is the lowest live ticket and the flock is free: FIFO within priority, steer (`0`) before normal (`5`). Dead-pid tickets are reaped. `status`/`agents list --json` show depth.
- Interrupted turns (`stop`, `steer`) exit 130, keep their partial reply in the log, are marked `interrupted` (wake job `failed`/`interrupted`) and never retried.
- `stop` leaves HOLD in place (`ag queue w --release`). `--cascade` also stops agents whose active wake jobs, tickets or running turn record `parent == w`. Python: `agent_stop(sdir, name, cascade=False)`.
- Steer text sent to the model: `[steer] user interrupted the previous turn; new instruction: <text>`; on an idle agent it is a plain priority-0 turn.

## Reliability

- **Stale-session recovery:** if the backend no longer knows the stored session id (`Session not found`, `No conversation found`, `no rollout found`), ag clears it, replays recent history into a fresh session, relaunches once, logs `[resume] ... started fresh`.
- **Transient retry:** rate limit, overload, 503/529, connection reset: up to 2 retries (2s, 6s) only if the attempt produced no output. Guard stays held, user row not duplicated, `--timeout` bounds the whole turn. `AG_RETRY=0` disables.
- codex headless turns resume via `exec --json`; gemini turns are stateless (history replay).
- **Provider probe:** `ag agents doctor [--refresh] [--json]` shows installed, version, compat (`COMPAT` min-version table: ok/graceful/broken/unknown) and auth (`claude auth status`, `codex login status`, `agent status`, `opencode auth list`; gemini = unknown). Cached 60s in `$AG_CACHE_HOME/providers.json`. Turns pre-flight it: definitely logged out fails fast with `not logged in to <backend>: <hint>` (no launch, no retry); unknown proceeds. `AG_PROBE=0` disables.
- Daemon crash (`kill -9`): sessions go `stale`, pending wake does not fire.

## Capabilities and permission modes

`ag agents caps [BACKEND]` (`--json`) prints the per-backend capability table (`CAPS`: resume, system/mcp flag, skills, interrupt, usage, plan, supported `modes`, `native_enforce`).
`ag agents add|set NAME --mode ro|edits|auto|full [--plan|--no-plan]` sets a per-agent permission mode (`--mode ""` clears; unset = backend default, unchanged). Mapping: claude `--permission-mode` (dontAsk/acceptEdits/auto/bypassPermissions, `plan`), codex `-s` read-only/workspace-write/danger-full-access, gemini `--approval-mode` (plan/auto_edit/yolo; no `auto`), cursor `--mode ask|plan` / `--force [--sandbox enabled|disabled]`, opencode a `permission` block merged into a scoped `OPENCODE_CONFIG` (`.agent/modes/<agent>/opencode.json`; profile config kept). Codex/opencode emulate `--plan` with read-only. A mode a backend cannot enforce (echo, gemini `auto`) prints one `[mode] ...` warning line on `add` and each turn and runs with the backend default.

## Isolation: worktree per agent

Agents sharing a `dir` clobber each other. Give each a worktree:

```sh
ag agents add w1 --backend claude --dir ~/proj --worktree [--base main]   # branch ag/w1, worktree ~/proj.ag-wt/w1
ag agents set existing --worktree        # idle agent only
ag agents rm w1 [--force]                # clean: removes worktree; --force drops uncommitted work; branch kept
```

`list` shows `[ag/<name>]`; `--json` has `worktree`. Merge yourself (`git merge ag/w1`); ag has no merge. An identical existing worktree is reused; other clashes error with a hint.

## Checkpoints

If the agent dir is in a git repo, each non-slash turn is snapshotted before and after (hidden refs `refs/ag/<agent>/<n>-pre|post`, temp index; your index/HEAD/branches untouched; ignored files excluded; last 20 turns; non-git dirs skipped; `AG_CHECKPOINT=0` disables). Changed files append to the chat as `[checkpoint] turn 7: 3 files changed (+40 -5): ...` and appear as `checkpoint` in the turn result.

```sh
ag chat checkpoints w
ag chat diff w [--turn N] [--stat]
ag chat revert w [--turn N]      # whole repo worktree back to before turn N
```

`revert` restores snapshot files, deletes non-ignored files that didn't exist then, never touches `.git`/ignored files, refuses while busy, and saves current state at `refs/ag/<agent>/pre-revert` (undo via `git diff`/`git checkout` from that ref). Scope is the whole repo, not just the agent subdir.

## Usage

`ag chat usage w` prints last-turn and total provider token usage for the agent.

## Context and handoff

Scoped task + memory so the next agent continues, not restarts (details: [docs/AGENT_CONTEXT.md](docs/AGENT_CONTEXT.md)).

```sh
ag context init
ag context assign ui-a --scope ui --task "own chat panel" --acceptance "tests pass" --brief-file brief-a.md
ag context checkpoint ui-a --file ckpt-a.md
ag context show ui-b             # what the agent actually got: sources + warnings
ag compact show|config|run      # auto-summaries (docs/AUTO_COMPACTION.md)
ag handoff w [--spawn|--exec]    # native backend TUI argv + profile env; --exec needs a TTY
```

Same scope inherits the prior `HANDOFF.md`; sibling scopes are isolated. Native TUI gets no auto-inject. Handoff argv never carries auto-approve flags; `claude`/`cursor` keep `--resume SID`, others launch bare. Multi-host portable snapshots: [docs/MULTI_HOST.md](docs/MULTI_HOST.md) (`hosts`, `sync`; no automatic failover).

## Harness profiles

Opt-in named sources (instructions, memory, skills, MCP) shared by N agents. Files live in `<state>/profiles_effective/<name>/`; no home/global config is written.

```sh
ag harness profile add docs --instructions ./AGENTS.md --memory ./MEMORY.md --skills ./.claude/skills --mcp ./.mcp.json
ag harness profile list | show docs | validate docs | status
ag harness profile set oc --profile docs      # --none clears; clears session
ag harness profile rm docs [--force]          # refuses while agents use it
ag harness show|export|import|link --to DIR   # sharing; secrets redacted
```

Per-backend delivery is in the [capability matrix](#capability-matrix). MCP source accepts `{"mcpServers":...}`, `{"mcp":...}` or bare `{name: entry}`; `{env:X}`/`${X}` refs preserved. `show`/`validate`/`status` print names and counts, never env values. Codex extra config: `--codex-config ./extra.toml` (appended to the scoped `config.toml`; `auth.json` symlinked, never copied).

## Roles

System-prompt presets per agent: `ag roles list|show|add|set|rm|reset NAME`. Built-ins include `planner` (assumptions, verifiable steps), `implementer` (minimal diffs), `reviewer` (terse bug/security check). Override per call/agent with `--persona` or `--system TEXT|@file`. Defaults for orchestrator vs sub: [docs/HARNESS_ROLES.md](docs/HARNESS_ROLES.md).

---

## Docs

[INSTALL](docs/INSTALL.md) · [HARNESS_ROLES](docs/HARNESS_ROLES.md) · [AGENT_CONTEXT](docs/AGENT_CONTEXT.md) · [AUTO_COMPACTION](docs/AUTO_COMPACTION.md) · [WAKE_CONTROL](docs/WAKE_CONTROL.md) · [WAKE_STATE](docs/WAKE_STATE.md) · [WAKE_STATUS](docs/WAKE_STATUS.md) · [STREAMING](docs/STREAMING.md) · [MULTI_HOST](docs/MULTI_HOST.md) · [UI_DESIGN](docs/UI_DESIGN.md) · [MUSE_FEEDBACK](docs/MUSE_FEEDBACK.md)

## Limitations (honest)

- Wake is at-most-once *launch*, not completion: a crash between claim and enqueue, or `kill -9` of a worker, can lose/stall it. Check `events` for a missing `wake` after `exit`, `wakes` for dead sessions. No retry scheduler (transient-error retry is per turn only).
- Daemon `kill -9`: sessions go `stale` (`exit=-1`), pending wake does not fire (`wake_fired:false` in `status`). Re-wake manually.
- While a turn runs its agent's guard is held: other turns on that agent block; backend/profile/model switches refuse. No queue fairness/priority/FIFO.
- Message cap 4000 chars (truncated). `wake/jobs` is never pruned by `forget`.
- POSIX only (`spawn`/workers need the pty daemon; TUI needs curses). Secret popup is macOS only.
- `ag run echo --json` treats `--json` as global; use `ag run -- echo --json`.
- Atomic writes only for `status.json`, wake jobs and `ag setup` outputs; other state JSON can corrupt on crash (agents/todos fall back to defaults). Back up `.agent/` before risky ops. Logs never rotate.
- Checkpoints/revert act on the whole git repo; non-git dirs have none. Worktree agents leave their branch behind; ag does not merge.
- Model lists for claude/gemini are static aliases; discovery depends on each backend CLI.
- Rendering is cell-based (CJK/ZWJ safe); below 40x16 the TUI asks for a resize; mouse is best-effort.
