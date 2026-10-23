---
name: work-on
description: Use to manually enqueue a Linear ticket for autonomous processing. Takes a ticket ID (e.g. LIN-123) or full Linear URL. The ticket gets added to the work queue; if no worker is running, a worker is dispatched immediately.
---

# /work-on <ticket-id-or-url>

Enqueue a Linear ticket and (if idle) dispatch a worker.

## Pre-flight

1. Parse argument. Accept either `LIN-123` (or any team-prefix-NUM form) or a full URL `https://linear.app/<team>/issue/LIN-123/...`. Extract the ticket ID (the `LIN-123` portion).
2. Confirm Linear MCP is available — try `mcp__claude_ai_Linear__list_teams` (cheap call). If unavailable, halt with: "Linear MCP not connected. Run `/mcp` in Codex and authenticate the Codex.ai Linear connector."

## Resolve the ticket

3. Call `mcp__claude_ai_Linear__get_issue` with the ticket ID. Capture `title` and `url`. If not found, halt with: "Ticket <id> not found in Linear."

## Enqueue

4. Run `bash ~/.Codex/work/lib/queue.sh enqueue "<ticket-id>" "<title>" "<url>"`. If exit 2 (already in some list), find which list by running `bash ~/.Codex/work/lib/queue.sh show` and grepping; report and halt.

## Dispatch if idle

5. Read `bash ~/.Codex/work/lib/queue.sh stats`.
6. If `running=0` AND `paused=false`:
   - Try `bash ~/.Codex/work/lib/queue.sh mark-dispatch "/work-on"`. If exit 1 (marker fresh), skip dispatch and report "queued at position N; another dispatcher is starting a worker."
   - If mark-dispatch succeeded, dispatch a worker via Agent tool:
     - subagent_type: `general-purpose`
     - run_in_background: `true`
     - description: `Worker: <ticket-id>`
     - prompt: contents of `/Users/sekun/.Codex/work/lib/worker-prompt.md`
7. Else (running or paused), report "queued at position N; worker will pick it up after <current-ticket> finishes" or "paused — resume with /work-resume".

## Report

End with one line: `Enqueued <ticket>. Worker: <dispatched | waiting on current | paused>.`
