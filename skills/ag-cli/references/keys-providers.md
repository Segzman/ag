# Keys, providers, api backend

## Keys

```sh
{{AG}} --dir <state> keys set NAME [--from-env VAR]     # Keychain (service ag) via `security -i` on STDIN; macOS only
{{AG}} --dir <state> keys list                          # names + created/updated, never values
{{AG}} --dir <state> keys rm NAME
{{AG}} --dir <state> keys test NAME --provider P        # GET provider /models, prints ok/401, never the key
```

`${keychain:NAME}` works wherever `${VAR}` refs work (agent/profile env); resolved at spawn into child env only, redacted in `agents env`.

## Providers

```sh
{{AG}} --dir <state> providers add ID --base-url URL --key-ref keychain:NAME [--npm @ai-sdk/openai-compatible] [--privacy zdr|retain|train|unknown] [--no-key] [--zdr] [--max-concurrent N] [--rpd N] [--model ID ...]
{{AG}} --dir <state> providers list | show ID | rm ID
{{AG}} --dir <state> providers add groq --key-ref keychain:NAME   # presets: groq|cerebras|openrouter|cloudflare|gemini|mistral|sambanova
{{AG}} --dir <state> agents add l --backend api --model ollama/qwen3:8b   # local keyless presets ollama|lmstudio (zdr, cost 0)
{{AG}} --dir <state> providers add myllm --base-url http://127.0.0.1:8000/v1 --no-key --zdr   # custom keyless
{{AG}} --dir <state> models --backend api --provider ollama      # list a provider's models
```

Providers live in `$AG_CONFIG_HOME/providers.json` (refs only); ag merges an opencode fragment (`"apiKey": "{env:AG_KEY_<ID>}"`) into the scoped `OPENCODE_CONFIG` and injects `AG_KEY_<ID>` into the child env only (never global `~/.config/opencode/opencode.json`). Privacy feeds the work-guard; max_concurrent feeds ratelimit slots for `<provider>/*`.

## api backend

```sh
{{AG}} --dir <state> agents add v --backend api --model PROVIDER/MODEL   # direct OpenAI-compat chat, no tools (e.g. groq/llama-3.3-70b-versatile); history AG_API_BUDGET tokens (default 2000)
```

`api` is for small-limit providers (Groq free 8K TPM, below opencode's first request): bounded history, streamed SSE reply, usage to the ledger, same slots/cooldowns/privacy/access/budgets as other backends. Opencode `ContextOverflowError` (`Request too large ... Limit 8000, Requested N`) kills the turn non-retryably with `use a backend=api agent`.
