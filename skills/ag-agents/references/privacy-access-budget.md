# Privacy, guardrails, access, usage, budgets

## Data privacy

Work code only to zdr models. Source: opencode.ai/docs/zen Privacy (2026-10-12); `{{AG}} models --rank` shows `data` (zdr/retain/train), `--zdr` filters. `{{AG}} route --check` asks the project data class; `setup --scope project --answers '{"data":"work"}'` stores `routing.json` `privacy.default`. `agents add|set --data work|public` (default `work`); `work` refuses train/unknown models at add/set and at turn time (exit 2); `AG_PRIVACY=off` disables (unsafe).

| data | models |
|---|---|
| zdr | `opencode/space-bunny-free`, `opencode/longcat-2.5-preview-free`, `opencode/step-5-preview-free`; paid Mistral/Jev; local `ollama`/`lmstudio` |
| retain | paid OpenAI/Anthropic (30d); direct `claude`/`codex` (own subscription); `gemini`/`cursor` |
| train | `opencode/muse-spark-1.3-contributor-free`, `big-pickle`, `exo-free`, `mimo-v2.6-flash-free`, `ling-3.1-flash-free`, `ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free` |

## Guardrails

Work code only to zdr models (`space-bunny-free`, `longcat-2.5-preview-free`, `step-5-preview-free`). Pair a cheap Claude (haiku) with a zdr free model for second opinions when the task is work. Don't switch backends/models unless asked. Never request secrets via chat. Unknown `/word` errors; don't retry as prose. Persistent bots = `schedule add` (or `spawn --wake`). Cost: route mechanical work to cheap backends; never use `full` mode casually.

## Access + budgets

```sh
{{AG}} --dir <state> access show|allow GLOB|deny GLOB|rm GLOB [--scope global|project] [--data work|public]
{{AG}} --dir <state> usage [--by provider|model|agent|project|day] [--since 1d|7d|30d]
{{AG}} --dir <state> budget show
{{AG}} --dir <state> budget set [--daily USD] [--monthly USD] [--provider P --rpd N]
```

`access`: deny wins; `agents add/set` + turns refuse with exit 2. `budget` breach refuses unless `chat send|wake|delegate --force`; 80% warns `[budget]`. Full flag reference: skill `ag-cli` `references/access-budget-link.md`.
