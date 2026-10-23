---
name: work-retry
description: Use to move a previously-failed ticket back to the queue for another attempt. Often used after fixing whatever blocked the worker.
---

# /work-retry <ticket-id>

Re-enqueue a failed ticket.

## Steps

1. Parse `<ticket-id>`.
2. Run `bash ~/.Codex/work/lib/queue.sh retry "<ticket-id>"`. If exit 2 (not in failed), halt with reason.
3. Read stats. If `running=0` AND `paused=false`:
   - Try `bash ~/.Codex/work/lib/queue.sh mark-dispatch "/work-retry"`. If exit 1 (marker fresh), skip.
   - If mark-dispatch succeeded, dispatch worker via Agent tool (same args as /work-on).
4. Report: "Retrying <ticket-id>. <dispatched | queued behind current | paused>."
