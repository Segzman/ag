---
name: wrap-up
description: Use when finishing a feature — runs typecheck, tests, design-system debt check, drafts memory updates to a pending file, and creates/updates the PR. Never merges and never deletes worktrees.
---

# /wrap-up

Drive a feature from "tasks done" to "PR open" with safety gates.

## Pre-flight (refuse on failure)

1. Confirm CWD is a git repo and inside a worktree (this skill expects to be called from a worktree set up by `/feature-start`). Check via `git rev-parse --show-superproject-working-tree` — should be non-empty. If empty, halt: "Not inside a worktree. /wrap-up expects to be run from `.worktrees/<branch>`."
2. Confirm no uncommitted changes: `git status --porcelain`. If dirty, list files and halt: "Commit or stash before wrapping up."
3. Confirm `gh` auth: `gh auth status`. If unauthed, tell the user to run `! gh auth login` and halt.

## Quality gates (halt on any failure, in order)

1. **Typecheck**: if `package.json` defines a `typecheck` script (`jq -e '.scripts.typecheck'`), run `bun run typecheck`. Halt on non-zero exit; surface the error output.
2. **Tests**: if `package.json` defines a `test` script (`jq -e '.scripts.test'`), run `bun run test`. Halt on non-zero exit. If no `test` script exists, skip (note this in the report).
3. **colors.* debt**: For each file changed in this branch vs `main` (`git diff --name-only main...HEAD --diff-filter=ACM | grep -E '\.(ts|tsx|js|jsx)$'`), grep for `(^|[^A-Za-z_])colors\.[A-Za-z_]`. Exclude files listed in `.claude/colors-allowlist.txt`. Halt and list violations if any new ones appear.

For each gate that fails: print exact errors and stop. Do NOT proceed to PR creation. The user fixes and re-runs `/wrap-up`.

## Memory drafting

1. Read existing memory files in `/Users/sekun/.claude/projects/-Users-sekun/memory/`. (This path is user-specific; adjust if running on a different machine.)
2. Compare to session work: what changed in the codebase or workflow that warrants a memory update? Look for:
   - New patterns or conventions introduced
   - Architectural decisions made
   - New files/components of note
   - Workflow gotchas surfaced
3. Draft proposed updates to `MEMORY_PENDING.md` at the worktree root. Format as markdown with one section per memory file that would be touched. Include enough context that the user can paste sections directly into the real memory file later.
4. **CRITICAL: Do NOT modify any real memory files in `~/.claude/projects/`.** Only the pending draft is written. The user reviews and applies manually.

If there are no memory updates needed, skip this step and note "(no memory updates needed)" in the final report.

## PR handling

1. Push the branch: `git push -u origin <branch>` (skip the `-u` if already tracking).
2. Check for an existing PR: `gh pr list --head <branch> --json number,url,title,body`.
3. **If exists:** update the PR body with the current task list + summary. Use `gh pr edit <number> --body-file <tmpfile>`. Do not change the title unless the feature was renamed.
4. **If absent:** create with `gh pr create --title "<feature name>" --body-file <tmpfile>`. Body should include:
   - One-line summary
   - The task list from `.claude/current-feature.json` (with completion checkmarks)
   - Test plan section (what reviewers should verify)
   - Link to `MEMORY_PENDING.md` for review (or note if no memory updates)

## Hard stop

Print:
- PR URL (from `gh pr view --json url -q .url`)
- Summary of what changed (count of files touched, count of commits via `git rev-list --count main..HEAD`)
- Reminder: review `MEMORY_PENDING.md` and apply manually if accepted
- Status: the worktree is intact and the branch is unmerged

**Do NOT** merge the PR.
**Do NOT** delete the worktree.
**Do NOT** apply pending memory.
**Do NOT** clean up `/tmp/claude-autonomous-*` or `/tmp/claude-wrapup-nudged-*` flags (the user controls when autonomous mode ends).

Wait for the user's next instruction. Common follow-ups:
- "Looks good, merge it" → invoke `gh pr merge`, then `git worktree remove`
- "Apply the memory updates" → copy sections from MEMORY_PENDING.md into the real memory files
- "Keep iterating" → user continues work; nothing to do
