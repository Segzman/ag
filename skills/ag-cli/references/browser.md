# Browser (`ag browser`)

Per-agent isolated Chrome over CDP. Profile per NAME under `<state>/browser/NAME/`; headless unless `--headed`.

```sh
{{AG}} --dir <state> browser open NAME URL [--headed] [--chrome PATH]
{{AG}} --dir <state> browser eval NAME JS
{{AG}} --dir <state> browser click NAME --role button --name "Sign in"   # or --css SEL
{{AG}} --dir <state> browser type NAME "text"                           # into the focused element (click first)
{{AG}} --dir <state> browser upload NAME --css 'input[type=file]' FILE  # native chooser buttons: pass the button's --css
{{AG}} --dir <state> browser snap NAME --text --png shot.png
{{AG}} --dir <state> browser list
{{AG}} --dir <state> browser close NAME
```

- Use `snap --text` to find the role and name before `click`. Errors exit 1 with a hint; add `--json` for machine output.
- Never read cookies or logins out of the profile. `eval` runs the JS you give it; keep secrets out of argv (use `ag` secret prompts).
- Chrome: macOS app path or `google-chrome`/`chromium` on PATH; `--chrome PATH` to override.
