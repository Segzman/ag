---
name: feature-start
description: Use when starting any new feature in a git repo — creates a worktree, loads design context, initializes a task plan, and optionally enters autonomous mode. Accepts an optional spec.md path to seed the task plan.
---

# /feature-start [path/to/spec.md]

Safely set up a new feature branch with a worktree and a task plan.

## Pre-flight (refuse on any failure)

1. Get current working directory and confirm it is a git repo: `git rev-parse --is-inside-work-tree`. If not, halt with: "Not in a git repo. Run from inside a project."
2. Confirm the working tree is clean: `git status --porcelain`. If dirty, halt and list the uncommitted files. Tell the user to commit, stash, or discard before retrying.
3. Confirm not already inside a worktree: `git rev-parse --show-superproject-working-tree`. If non-empty, halt with: "Already inside a worktree. /feature-start must be invoked from the main checkout."

## Determine feature name

- If a spec path was passed as an argument:
  1. Read the spec file into context.
  2. Extract feature name from the first H1 heading. If absent, derive from filename (strip date prefix and extension).
- Else: ask the user for a one-line feature name. Convert to kebab-case for the branch name.

## Branch + worktree collision checks

1. Check local branches: `git branch --list <name>`. If exists, ask user to choose: reuse existing, rename, or abort.
2. Check remote branches: `git ls-remote --heads origin <name>`. If exists, warn and confirm.
3. Check `.worktrees/<name>` directory:
   - If exists and `git -C .worktrees/<name> branch --show-current` matches `<name>` → reuse.
   - If exists with different branch → halt with conflict report.

## Create the worktree

```bash
git worktree add .worktrees/<name> -b <name>
cd .worktrees/<name>
```

If the branch already existed locally, use `git worktree add .worktrees/<name> <name>` (no `-b`).

## Load context

1. Read `AGENTS.md` from the worktree root if it exists.
2. Read `docs/DESIGN.md` if it exists.
3. If a spec was passed, ensure it remains in context.
4. Search for related work:
   - `git log --oneline --grep="<feature-keywords>" -i` (recent matching commits)
   - `gh pr list --search "<feature-keywords>"` (related open PRs)
   Surface anything relevant.

## Initialize task plan

Create `.claude/current-feature.json` (in the worktree, not the main repo). This file is gitignored:

```json
{
  "feature": "<name>",
  "branch": "<name>",
  "spec_path": "<path or null>",
  "started_at": "<ISO 8601 timestamp from `date -u +%Y-%m-%dT%H:%M:%SZ`>",
  "tasks": [
    { "id": "1", "title": "...", "status": "pending" }
  ]
}
```

If a spec was provided, derive tasks from its checklist/steps. Otherwise, build the task list interactively with the user before proceeding.

## Mode selection

Check for a pre-existing autonomous flag at `/tmp/claude-autonomous-${CLAUDE_SESSION_ID}`.

- **If the flag file already exists:** autonomous mode is pre-set (likely by a background worker dispatching this skill). Skip the interactive prompt and confirm "Autonomous mode (pre-set by caller)" in the final response. Do NOT re-write the flag.
- **Otherwise**, ask the user: **"Run autonomously?"**
  - **Autonomous:** write `/tmp/claude-autonomous-${CLAUDE_SESSION_ID}`. The Stop hook will auto-trigger `/wrap-up` when all tasks complete and at least one commit lands.
  - **Supervised (default):** no flag written. The user invokes `/wrap-up` manually when done.

Confirm the mode choice in the final response so the user knows what to expect.

## Report

End with a one-paragraph summary:
- Worktree path
- Branch name
- Number of tasks
- Mode (autonomous / supervised)
- Next action (start on Task 1)
