---
name: work-resume
description: Use to resume the background worker chain after /work-pause. If there are queued tickets and no worker running, dispatches a worker.
---

# /work-resume

Resume the worker chain.

## Steps

1. `rm -f ~/.Codex/work-paused`
2. Read `bash ~/.Codex/work/lib/queue.sh stats`.
3. If `running=0` AND `queued>0`:
   - Try `bash ~/.Codex/work/lib/queue.sh mark-dispatch "/work-resume"`. If exit 1, skip.
   - If mark-dispatch succeeded, dispatch worker via Agent tool (same args as /work-on).
4. Report: "Resumed. <dispatched worker for LIN-X | no queued tickets | worker already running>."
