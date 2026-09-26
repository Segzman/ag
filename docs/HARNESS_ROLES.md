# Harness selection and behavior presets

Use the absolute path to your clone and the same explicit state directory in
every invocation. These examples use `/path/to/ag/ag` and `/path/to/state` as
placeholders. All commands below are headless; add `--json` for structured
results and check the process exit code.

## Switch an existing agent

```sh
/path/to/ag/ag --dir /path/to/state harness current worker
/path/to/ag/ag --dir /path/to/state harness use worker codex
/path/to/ag/ag --dir /path/to/state harness use worker opencode --model provider/model-id
/path/to/ag/ag --dir /path/to/state --json harness current worker
```

`harness use AGENT BACKEND [--model MODEL]` preserves the agent's name,
work directory, topology role, persona, instructions, system override, and
profile. Backends are `claude`, `opencode`, `gemini`, `codex`, `cursor`, and the
local test backend `echo`. It selects configuration without launching or
probing a backend. Use `agents doctor` to check installed backend commands.

Each agent remembers its last model separately for each backend. On the first
switch to a backend, the backend default is used; OpenCode uses ag's configured
default model, `opencode/muse-spark-1.3-contributor-free`. A custom ID from
another backend is never carried over implicitly. Returning to a backend
restores its saved model; explicit `--model` replaces that preference.
`--model default` selects the default again. Custom IDs are accepted as given;
the selected backend validates availability when it runs.

Native session IDs are also kept per backend. Switching back can resume that
backend's previous session, but never another backend's session. Saved chat
history remains available, and stateless backends continue using history replay.

`agents set worker --backend codex [--model MODEL]`, the TUI backend picker,
and `/harness use codex` share this switching behavior. Every `agents set`
— even a dir/role/system/instructions-only tweak — takes the per-agent
turn guard (non-blocking) and refuses while a turn or tracked native
handoff runs. This is intentional: the guard is held through the whole
settings update so a concurrent turn cannot start halfway through it and
observe a half-written roster/meta pair. Retry once the agent is idle.

Both `harness use` and `harness current` return this data with `--json`:

```json
{"ok": true, "cmd": "harness", "data": {
  "agent": "worker", "backend": "codex", "model": "default",
  "effective_model": "", "dir": ".", "role": "sub",
  "persona": "reviewer", "profile": "", "sid": ""
}}
```

An empty `effective_model` means no explicit model argument is sent; an empty
`sid` means no native session is selected. Errors return `ok: false`, an
`error` string, and a nonzero exit code.

## Customize built-in behavior

`reviewer`, `planner`, and `implementer` are editable system-prompt presets
stored in this state's `roles.json`. Configure them before starting work:

```sh
/path/to/ag/ag --dir /path/to/state roles list
/path/to/ag/ag --dir /path/to/state roles set reviewer --system "Review correctness and security. Report file:line, impact, and a minimal fix."
/path/to/ag/ag --dir /path/to/state roles set planner --file /path/to/planner.md
/path/to/ag/ag --dir /path/to/state roles set implementer --system "Implement the agreed plan with minimal changes. Run the relevant tests and report the result."
/path/to/ag/ag --dir /path/to/state --json roles show reviewer
```

`roles show` prints the prompt normally. With `--json`, `data.role` contains
`name`, `system`, `builtin`, and `customized`. The latter indicates whether a
built-in prompt differs from the shipped default. `--file` reads up to 8,000
characters and takes precedence over `--system`.

Assign the behavior using `--persona`:

```sh
/path/to/ag/ag --dir /path/to/state agents add reviewer-worker --backend codex --role sub --persona reviewer
/path/to/ag/ag --dir /path/to/state agents set worker --persona implementer
/path/to/ag/ag --dir /path/to/state agents set worker --persona planner --system ""
```

`--role orchestrator|sub` describes the agent's place in orchestration;
`--persona reviewer|planner|implementer` selects behavior. These are separate
settings. A nonempty agent `--system` overrides its persona; `--system ""`
clears that override, and `--persona ""` clears a persona assignment.
Presets do not grant tools, choose models, or enforce permissions.

Role changes affect agents that reference the preset when their prompt is next
assembled. For backends that receive the role only at native session creation,
an already resumed session can retain the old instructions; configure the
persona before starting that session, or use a newly added agent for a fresh
session. The role commands do not interrupt active work or erase sessions.

Restore one built-in independently, including one that was removed:

```sh
/path/to/ag/ag --dir /path/to/state roles reset reviewer
/path/to/ag/ag --dir /path/to/state roles reset planner
/path/to/ag/ag --dir /path/to/state roles reset implementer
```

Reset leaves other presets and agent assignments intact. Only these three
known built-ins can be reset. Custom presets still use `roles add`, `set`,
`show`, and `rm`; trying to reset a custom or unknown name returns an error.
