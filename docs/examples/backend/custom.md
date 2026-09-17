# custom.md — example scoped guidance (backend scope)

Scope: headless turns, wake workers, guards, profiles. Agents assigned here
own prompt assembly and backend argv; UI agents own rendering.

## Rules

- Keep the per-agent flock guard: `run_turn` owns it, switches probe it.
- Verified backend flags only; never fake a native command surface.
- No global config writes; scoped files under state dir only.
- Verify with `python3 tests/test_ag_context.py` (echo backend, no models).
