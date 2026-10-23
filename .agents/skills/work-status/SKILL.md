---
name: work-status
description: Use to view the current work queue — running ticket, queue depth, recent completions, recent failures. Read-only.
---

# /work-status

Print a compact view of the work queue.

## Steps

1. `bash ~/.Codex/work/lib/queue.sh init` (ensures file exists).
2. Read the queue: `bash ~/.Codex/work/lib/queue.sh show` (returns the full JSON).
3. Check pause flag: `~/.Codex/work-paused` exists?
4. Detect stale running: if `running.started_at` is older than 60 minutes, flag it as suspected stuck.
5. Print as:

```
Pause flag: clear | SET

Running:
  <ticket> — <title> (started Nm ago)
  [SUSPECTED STUCK — last update Nm ago; run /work-clear-stuck to clear]

Queued (N):
  1. <ticket> — <title> (added Nm ago)
  ...

Completed (last 5):
  ✓ <ticket> — <title> → <pr_url>
  ...

Failed (last 5):
  ✗ <ticket> — <title> (<reason>) — retry with /work-retry <ticket>
  ...

Cancelled (last 3):
  ⊘ <ticket> — <reason>
```

If a section is empty, omit it. Compute time deltas via `date +%s` and the entries' ISO timestamps.
