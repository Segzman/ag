# ag feedback — report ag's own bugs

When ag itself misbehaves (bad flag, crash, wrong state), file it instead of working around it silently:

```sh
{{AG}} --dir <state> feedback "wake crashed on --bogus flag" [--severity low|med|high] [--agent NAME]
{{AG}} --dir <state> feedback harvest [--since 7d]
{{AG}} --dir <state> feedback report [--md]
```

- `feedback "text"` appends one entry to `<state>/feedback.jsonl`: ts, agent (or `$AG_PARENT_AGENT`), node, origin (`unknown` until provenance lands), ag version, normalized signature.
- `feedback harvest` mines chat logs + wake job records for ag-side failures (tracebacks, `Unrecognized flag`, usage-limit failures, stalls, `error=` fields), dedupes by signature, appends with origin `harvest`. First word `harvest`/`report` only dispatches when alone or followed by flags — feedback text starting with those words plus more words is filed as text.
- `feedback report` groups by signature: counts, first/last seen, example agents. `--md` is pasteable into a plan.
- Every wake/chat system prompt already carries the one-sentence hint to run `ag feedback`; opt out with `"feedback": {"prompt": false}` in `config.json`.
- `relay sync` carries feedback between nodes: published redacted to `feedback/<node>.jsonl`, remote copies land in `results/<node>/feedback.jsonl` (secret scanner redaction — never file a raw secret as feedback text either).
