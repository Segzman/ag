---
name: plan-ticket
description: Use to create a Linear ticket from the current Claude Code session — gathers context from git, chat history, referenced Linear tickets, and open PRs, proposes a cheap framing paragraph first, then a full structured ticket (with parent + sub-issues for large scope). Created via Linear MCP.
---

# /plan-ticket [seed]

Create a Linear ticket with auto-grabbed context. Two gates: cheap framing first, full draft second. Cancellation safe at either gate.

## Constants — first-run setup

Edit these once on first use:

- `DEFAULT_TEAM_KEY`: `DSPORTS` (replace with your actual team key; check via `mcp__claude_ai_Linear__list_teams`)
- `DEFAULT_PROJECT`: (leave empty unless you have a single default project)
- `DEFAULT_LABEL_SUGGESTIONS`: `engineering`, `design`, `bug`, `feature`
- `DEFAULT_PRIORITY`: `Medium`
- `TEAM_CACHE_HELPER`: `/Users/sekun/.claude/work/plan-ticket/lib/team-cache.sh`

## Pre-flight

1. Confirm Linear MCP is available — try `mcp__claude_ai_Linear__list_teams` (cheap call). If it errors, halt with: "Linear MCP not connected. Run `/mcp` and authenticate the claude.ai Linear connector."
2. Parse the seed argument. If empty, prompt: "What do you want to plan? Give me a one-line seed."

## Step 1: Context gather (silent, parallel)

Run these in parallel:

**a. Git state** — use the Bash tool:

```bash
echo "repo: $(git -C . rev-parse --show-toplevel 2>/dev/null || echo n/a)"
echo "branch: $(git -C . branch --show-current 2>/dev/null || echo n/a)"
git -C . log -10 --pretty=format:'  - %s' 2>/dev/null || echo "  (no commits)"
echo "uncommitted: $(git -C . status --short 2>/dev/null | wc -l | tr -d ' ') file(s)"
git -C . worktree list 2>/dev/null | tail -n +2 || true
```

**b. Chat scan** — review the current conversation's recent turns directly from your working memory (no external read needed). Identify: file paths read or written; topics discussed; decisions documented; any `LIN-\d+` ticket IDs mentioned. This is already in your context — just consolidate.

**c. Linear refs** — for each unique `LIN-\d+` found in the chat scan (max 5), call `mcp__claude_ai_Linear__get_issue` and capture `title`, `url`, `state`.

**d. Open PRs in repo** — use the Bash tool:

```bash
gh pr list --state open --json number,title,url --limit 10 2>/dev/null || echo "[]"
```

Compile all of (a)-(d) into a structured context object for your own use across the remaining steps.

## Step 2: Clarify pass (only when context is ambiguous)

Max 2-4 questions. **Skip the pass entirely if everything is resolvable from context.**

### Team resolution (precedence)

1. Get the current repo path. Run `bash "$TEAM_CACHE_HELPER" get "<repo-path>"`. If exit 0, use the returned team key. Done.
2. Else: if the repo name obviously implies one team (e.g., `d-sports-engage-native` → `DSPORTS`), use that AND write to the cache: `bash "$TEAM_CACHE_HELPER" set "<repo-path>" "<key>"`.
3. Else: call `mcp__claude_ai_Linear__list_teams`. If only one team is returned, use it. If multiple, ask the user "Which team? <list of names + keys>" and cache the answer via `team-cache.sh set`.
4. Fall through to `DEFAULT_TEAM_KEY` if nothing above resolves.

### Other clarifications (ask only if needed)

- **Scope hint** — if your initial scope estimate is borderline 2-5 days, ask once: "Single ticket or break into sub-issues?"
- **Related tickets** — if Linear refs were found in chat, ask once: "I found these recently mentioned: <list>. Link them as related? (y/n)"
- **Priority** — if no priority signal in seed or context, ask once with options: `None / Low / Medium / High / Urgent`. Default to `DEFAULT_PRIORITY` if user skips.

## Step 3: ☆ Framing draft (the cheap gate — target ≤150 words, aim for 80)

Compose a brief paragraph in this exact shape:

```
**Framing:** <one-sentence problem statement, action-shaped>

**Scope:** <single ticket | parent + N children — brief rationale using one of the anchors below>

**Context I'm using:**
• Related: <ticket-id(s) or PR or branch>
• Source: <chat / git>

**Assumptions:** <1-2 things I'm presuming>

Does this capture it? (yes / redirect / cancel)
```

### Scope-judgment anchors (use these to calibrate)

| Seed | Verdict |
|---|---|
| "fix a typo in error message" | single ticket, no checklist |
| "add a one-off API field" | single ticket with 2-3 checklist items |
| "refactor the wallet card component" | single ticket with detailed checklist |
| "add a new payment provider" | parent + ~5 sub-issues |

### User options at the framing gate

- **yes** → proceed to Step 4 (full draft).
- **redirect** → user describes what's wrong; regenerate ONLY this framing paragraph (token-cheap). After 3 redirects on the same invocation, suggest "cancel" — likely the seed was too sparse and a fresh invocation will be better.
- **cancel** → exit cleanly. No ticket created. No further work.

## Step 4: Full structured draft (only after framing approved)

### Single-ticket shape

```markdown
**Title:** <action-shaped, 60-80 chars>

**Body:**
## Problem
<1-2 paragraphs>

## Acceptance criteria
- [ ] <concrete, testable criterion>
- [ ] <concrete, testable criterion>
- [ ] <concrete, testable criterion>

## Context
- Branch: `<branch>`
- Related commits:
  - <subject>
  - <subject>
- Related files: <paths>
- Related tickets: <LIN-IDs with markdown links>
- Related PRs: <urls>

## Out of scope
- <thing>

**Labels:** <suggested from DEFAULT_LABEL_SUGGESTIONS + topic — pick 1-3>
**Priority:** <from Step 2 clarify, or DEFAULT_PRIORITY>
**Team:** <from Step 2>
**Project:** <inferred or DEFAULT_PROJECT or empty>
**Milestone:** <if applicable, else empty>
**Assignee:** <you, the user>
```

### Parent + children shape (when scope is large per the anchors)

```markdown
**Parent:**
<single-ticket shape above, but with high-level acceptance criteria spanning the children>

**Sub-issues (3-8):**
1. **<title>** — <1 sentence>
   acceptance:
   - <criterion>
   - <criterion>
2. **<title>** — ...
3. ...
```

### User options at the full-draft gate

- **approve** → proceed to Step 5 (Linear creation).
- **tweak inline** → user says what to change (e.g., "priority high", "add label `wallet`", "drop sub-issue 3", "rephrase the problem statement"); regenerate the affected pieces only.
- **regenerate** → start the full draft over with the same framing.
- **cancel** → exit cleanly. No Linear writes.

## Step 5: Create via Linear MCP

### a. Resolve label IDs

Call `mcp__claude_ai_Linear__list_issue_labels` (filter by team if the parameter is supported). For each label name suggested in the draft, find its ID. Cache the full list in your conversational memory for this run (don't re-call). If a suggested label doesn't exist, skip it (don't auto-create labels in v1 — too easy to pollute the workspace).

### b. Resolve project ID (if set)

Call `mcp__claude_ai_Linear__list_projects`. Find the project ID matching the draft's project name. Cache.

### c. Resolve milestone ID (if set)

Call `mcp__claude_ai_Linear__list_milestones`. Find the milestone ID matching the draft. Cache.

### d. Resolve assignee ID

Call `mcp__claude_ai_Linear__get_user` for the current user (cache for the run).

### e. Create the parent ticket

Call `mcp__claude_ai_Linear__save_issue` with:

- `teamId`: resolved team ID
- `title`: from draft
- `description`: the markdown body from draft
- `labelIds`: from (a)
- `projectId`: from (b) or omit
- `priority`: from draft (Linear's enum: `0=None`, `1=Urgent`, `2=High`, `3=Medium`, `4=Low` — verify on first save)
- `assigneeId`: from (d)

Capture the returned `id` and `url`.

### f. Create sub-issues (if any)

For each child in order, call `save_issue` with the same shared defaults plus `parentId: <parent's id>`. Capture each child's `url`.

### g. Retry policy

If `save_issue` fails: retry once after 3s. If still failing on the parent: show the full draft markdown to the user with: "Linear creation failed. Here's the draft — paste into Linear's web UI to create manually." Exit.

If the parent created successfully but a child fails: continue with remaining children, then in the final output flag the failed child(ren). The user can retry the failed ones manually.

## Final output

```
✅ Created <parent ticket id>: <parent title>
   <parent url>

Sub-issues (if any):
  ✅ <child id>: <child title> — <child url>
  ✅ <child id>: <child title> — <child url>
  ...

Team: <team-key>
Labels: <comma-separated names>
Priority: <name>
```

If any sub-issue creation failed mid-flight:

```
⚠️  Parent created but child(ren) failed: <list of failed titles>
   Retry by re-running /plan-ticket and saying "create child issues under <parent-id>: ..."
```

## Boundaries

- Never modify existing tickets (create-only in v1).
- Never auto-create tickets without explicit user approval at both gates.
- Never auto-create labels that don't exist — skip them in the draft and note in the final output.
- Never expose sensitive context (Sensitive-list senders from SP3, credentials, etc.) in ticket bodies.
- Always show the framing first; never skip directly to full draft.
- After "cancel" at any gate, no Linear MCP write calls happen.
- After 3 redirects on the same framing, suggest "cancel" rather than continuing to regenerate.
