---
name: work-sync
description: Use to poll Linear for tickets labeled `claude-auto` and enqueue any not already tracked. Idempotent — safe to run repeatedly. Also runs automatically on session start via the briefing hook.
---

# /work-sync

Pull tickets labeled `claude-auto` from Linear into the queue.

## Pre-flight

1. Confirm Linear MCP available (same check as /work-on). If not, halt with the same auth hint.

## Fetch

2. Call `mcp__claude_ai_Linear__list_issues` filtered by label `claude-auto` (use the labels filter parameter). For each returned issue not already in any queue array, capture id, title, url.

## Enqueue new ones

3. For each ticket, run `bash ~/.claude/work/lib/queue.sh enqueue "<id>" "<title>" "<url>"`. Exit 2 = already tracked (skip silently). Exit 0 = newly enqueued (count it).
4. Track `newly_enqueued` count.

## Dispatch if idle and new tickets added

5. If `newly_enqueued > 0`:
   - Read stats. If `running=0` AND `paused=false`:
     - Try `bash ~/.claude/work/lib/queue.sh mark-dispatch "/work-sync"`. If exit 1, skip.
     - If mark-dispatch succeeded, dispatch a worker via Agent tool (same args as /work-on, description `Worker: chain`).

## Report

End with one line: `/work-sync: added N new tickets (M total in Linear). Queue: <stats output>.`
