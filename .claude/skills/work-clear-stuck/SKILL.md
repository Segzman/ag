---
name: work-clear-stuck
description: Use when /work-status flags the running ticket as suspected stuck (typically after Claude Code session crashed mid-flight). Moves the stale ticket to failed so the chain can continue.
---

# /work-clear-stuck

Clear a stuck `running` slot.

## Steps

1. Run `bash ~/.claude/work/lib/queue.sh clear-stuck`. No-op if not stale.
2. Read stats — did `running` change to 0?
3. Report: "Cleared <ticket> (was running for Nm). Moved to failed. Run /work-retry <ticket> to try again." OR "Nothing to clear (running is fresh or empty)."
4. If something was cleared AND queued>0 AND not paused, dispatch (same pattern as /work-on).
