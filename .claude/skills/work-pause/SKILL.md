---
name: work-pause
description: Use to pause the background worker chain. The currently running worker (if any) finishes its ticket, then stops chaining. New /work-on or /work-sync calls also won't dispatch while paused.
---

# /work-pause

Pause the worker chain.

## Steps

1. `touch ~/.claude/work-paused`
2. Report: "Worker chain paused. Current ticket (if any) will finish; queued tickets will wait. Resume with /work-resume."
