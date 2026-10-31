# Models and data privacy

## Contents
- ag models
- Data privacy rule and table

## ag models

```sh
{{AG}} models [--backend B] [--provider P] [--refresh] [--rank] [--sort intel|coding|speed|popular] [--tier small|balanced|big] [--free] [--zdr] [--auto on|off] [--status] [--json]
```

Live list per backend (opencode, codex, cursor discovered; claude/gemini static aliases). Cache `~/.cache/ag/models.json`, TTL 24h, auto-refresh when stale; `--refresh` forces. A failing source keeps its last good list and records `error`. Use it to pick valid model ids before `agents add --model`. `--rank` shows Artificial Analysis scores (intelligence/coding/agentic) + tok/s + latency from openrouter.ai/rankings, plus ctx, price/`free`, release date, `new`/`fast` badges. Tier is derived from the intelligence score (big >= 40, balanced 20-40, small < 20); `tier~` = no score, guessed from the name. Sorted by intelligence per backend; `--sort intel|coding|speed|popular`; `--tier T`/`--free` filter; `--json` has every field + `sources` fetch times. Source: "scores: Artificial Analysis via openrouter.ai/rankings". `AG_AA_KEY` adds the official AA API as an override. Stale caches refresh in a detached background run (never blocks); `{{AG}} models --auto on|off` toggles it (also the daily update's catalog refresh), `--status` shows last fetch per source. Small-fast vs big-smart: `models --backend opencode --free --tier big`, `--sort speed`. `--rank` shows a `data` column (zdr/retain/train); `--zdr` filters zero-retention only.

`--backend api --provider ollama` lists a registered provider's models (local ollama/lmstudio included).

`--backend openrouter` lists tools-support OpenRouter models with scores + endpoint `data` class + endpoint count (privacy from each model page's per-endpoint `dataPolicy`, 24h cache, free + top-60 intel only, <=4 parallel; `AG_OR_MODELS_URL`/`AG_OR_SITE_URL` override). Work agents on openrouter run via `--backend api` (`--model openrouter/<id>` sends `{"provider": {"data_collection": "deny", "zdr": true}}`); opencode backend refuses them (no provider-routing passthrough). `:free` takes 2 ratelimit slots; `/api/v1/key` tier (50/day free, 1000 after $10) feeds `usage` rpd.

`{{AG}} route [JOB] [--profile P] [--check] [--json]` shows the detected harness + effective job routing for its profile; `--check` = per-project setup questions (see `ag-agents`).

## Data privacy

Rule: work code only to zdr models. `agents add|set --data work|public` (default from `routing.json` `privacy.default`, default `work`); project setup asks it (`route --check` data question, `setup --answers '{"data":"work"}'`, web form selector). `work` refuses `train`/`unknown` at add/set and at turn time (exit 2, never sent). `AG_PRIVACY=off` disables (unsafe). Opencode `default` = `opencode/space-bunny-free` (zdr); free catalog picks rank zdr first. Live table: opencode.ai/docs/zen Privacy (`AG_ZEN_URL`, 24h cache); unknown free opencode = `train`.

| data | models |
|---|---|
| zdr | `opencode/space-bunny-free`, `opencode/longcat-2.5-preview-free`, `opencode/step-5-preview-free`; paid Mistral/Jev; local `ollama`/`lmstudio` |
| retain | paid OpenAI/Anthropic (30d); direct `claude`/`codex` (own subscription); `gemini`/`cursor` |
| train | `opencode/muse-spark-1.3-contributor-free`, `big-pickle`, `exo-free`, `mimo-v2.6-flash-free`, `ling-3.1-flash-free`, `ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free` |
| zdr/retain/train per endpoint | `openrouter/<id>` from the endpoint dataPolicy (unknown = train; work only via `--backend api`) |
