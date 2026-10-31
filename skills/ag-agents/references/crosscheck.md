# Second opinions via crosscheck

```sh
{{AG}} --dir <state> crosscheck cheap zdr "is this a real bug? <evidence>" [--judge orch]
```

Pair a cheap Claude (haiku, mechanical/review job) with a zero-retention free model for an independent second read; verdict-like replies (`OK`/`BUG:`/`PASS`/`FAIL`/`yes`/`no`, JSON `verdict`) report `AGREE`/`DISAGREE` (exit 0/3), prose reports a unified diff + similarity ratio. `--judge <third>` asks one more agent which answer is right and why (one turn). `--more <c>` adds agents; `--timeout S` bounds each. Tasks must be self-contained (both agents see only the task + their own history). When opencode overflows a small tier (Groq 8K TPM: `ContextOverflowError`, non-retryable `use a backend=api agent`), use an `api` agent instead: `agents add v --backend api --model groq/<id>` (direct chat, no tools, history `AG_API_BUDGET` tokens default 2000). Suggested pairs:

| Cheap Claude | ZDR free second | Use for |
|---|---|---|
| haiku | `opencode/step-5-preview-free` (high) | bug/no-bug verdicts, security review, quick-fix review (most reliable zdr: 97%, ~30s) |
| haiku | `opencode/space-bunny-free` (max) | one-commit / rote-diff sanity checks (fastest zdr, ~9s; weak on subtle logic) |
| none (no paid tokens) | `space-bunny-free` + `longcat-2.5-preview-free` | free second opinion on work code; they fail differently (logic vs arithmetic/dates), so agreement is meaningful |
| none (public/OSS code only) | `muse-spark-1.3-contributor-free` (xhigh) + `nemotron-3-ultra-free` | strongest free pair; both train/log on prompts, never work code |

Benchmarked 2026-10-12 (12 auto-graded tasks x3; probe: every live free model took 8 short concurrent calls; ~10 long agent turns at once did trip an account limit). Avoid: `exo-free` (deprecated, 410), `ling-3.0-flash-fin-free` (unavailable), `ling-3.1-flash-free` (frequent empty replies). Don't trust nemotron/longcat alone on arithmetic or dates.

Per-agent privacy guard applies: `work` agents refuse train/unknown models. OpenRouter second opinions: `models --backend openrouter` (tools-support + endpoint `data` class), work agents via `--backend api --model openrouter/<id>` (sends `{"provider": {"data_collection": "deny", "zdr": true}}`); opencode backend refuses openrouter work models.
