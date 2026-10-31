# Setup and update

## ag setup

```sh
{{AG}} setup [--scope global|project] [--harness claude,opencode,codex|all] [--preset cost-first|balanced|quality-first] [--set JOB=claude:ALIAS] [--set JOB=ag:BACKEND/MODEL[@EFFORT]] [--set backend.B.enabled=true|false] [--set backend.B.model=ID] [--set backend.B.mode=ro|edits|auto|full|unset] [--set backend.B.plan=true|false] [--set backend.B.effort=LEVEL|default] [--claude-md|--no-claude-md] [--profile claude|opencode|codex|default] [--answers JSON] [--yes] [--dry-run] [--ui web|cli|plain]
```

Ask the user to run it bare (interactive). Agent context: pass flags + `--yes` (or `--dry-run` first). `--ui` / `AG_SETUP_UI`: `web` (alias `mac`; auto on a local macOS desktop) = local browser form on 127.0.0.1 (random port + one-time token URL; `AG_SETUP_NO_OPEN=1` prints the URL instead of opening; Jobs per profile with grouped model dropdowns + effort select per job (per-model variants when known, else low|medium|high|xhigh|max, plus `default`/`highest`) + capability panel, Backends (with default effort), ranked Models, Skills; Save/Cancel, 15 min idle exits writing nothing), `cli` = curses (jobs table + Backends table, needs 60x24), `plain` = prompts.

Writes `routing.json` (jobs + per-backend defaults that feed `agents add`), sparsely: only jobs differing from the inherited profile are stored (explicit `--set JOB=...` always stored), renders the `ag-cli`/`ag-agents` skills (SKILL.md + references/) into harness skill dirs, and the routing block into each harness's instructions file. Atomic, never deletes. Job routed to a disabled backend: error, no save.

Per-project check: `{{AG}} route --check --json`; if `configured` is false, ask the user the returned `questions`, then `{{AG}} setup --scope project --answers '<json>'` (shape in `answers_format`).

## ag update

ag auto-checks GitHub once a day in a detached process; applies on next run. `schedule tick` runs the same daily check inline (same stamp), so a timer-only node still updates. Git checkout: `fetch` + `merge --ff-only`, only if clean, tracking, not ahead. Copy install: cheap `git ls-remote` sha compare (GitHub API fallback), download the pinned sha from raw, compile check, atomic swap, record in `~/.cache/ag/update-commit` (also the relay heartbeat commit).

```sh
{{AG}} update                  # run now
{{AG}} update --status         # mode, interval, installed commit, last check/result
{{AG}} update --auto off|on    # toggle (~/.config/ag/config.json)
{{AG}} push [GIT_PUSH_ARGS…]   # git push; signal every relay peer to update; sync
```

`AG_AUTO_UPDATE=0` disables per shell (use it in scripts/tests). Log `~/.cache/ag/update.log`.

## Sanity

`{{AG}} selfcheck` (regression checks, own temp dir) - `{{AG}} keys` (TUI cheat-sheet) - `{{AG}} agents doctor` (backends + rtk).
