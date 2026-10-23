---
name: work-cancel
description: Use to remove a ticket from the queue before a worker has started it. Cannot cancel mid-flight — use /work-pause to stop the chain after current ticket finishes.
---

# /work-cancel <ticket-id>

Remove a queued ticket.

## Steps

1. Parse `<ticket-id>` from argument.
2. Run `bash ~/.Codex/work/lib/queue.sh cancel "<ticket-id>" "user requested"`. If exit 2 (not in queued), report "Ticket not in queued. Current running cannot be cancelled — use /work-pause." Halt.
3. Report: "Cancelled <ticket-id>."
