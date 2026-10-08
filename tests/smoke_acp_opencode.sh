#!/bin/bash
# Live (non-CI) smoke: real `opencode acp` through ag's ACP transport.
WT=${WT:-$(cd "$(dirname "$0")/.." && pwd)}
T=$(mktemp -d /tmp/ag-acp-smoke.XXXX)
export AG_AUTO_UPDATE=0 AG_HOME=$T/h AG_CONFIG_HOME=$T/c AG_CACHE_HOME=$T/k AG_CHECKPOINT=0
AG="python3 $WT/ag --dir $T/.agent"
mkdir -p $T/w
$AG agents add aoc --backend opencode --dir $T/w --transport acp
echo "--- turn 1"
$AG chat send aoc "Reply with exactly the word: pong" 2>&1 | tail -5
echo "--- turn 2 (session/load resume)"
$AG chat send aoc "What exact word did you reply with last time? Answer with just that word." 2>&1 | tail -5
echo "--- turn 3 (tool use)"
$AG chat send aoc "Create a file named hello.txt in the current directory containing exactly: hi. Then reply done." 2>&1 | tail -5
echo "file: $(cat $T/w/hello.txt 2>&1)"
echo "--- meta"; cat $T/.agent/chats/aoc.meta.json | python3 -c 'import json,sys; m=json.load(sys.stdin); print("sid", m.get("sid"), "usage", (m.get("usage") or {}).get("last"))'
echo "--- chat log roles"; python3 -c 'import json,sys; [print(r["role"], repr(r["text"][:80])) for r in map(json.loads, open(sys.argv[1]))]' $T/.agent/chats/aoc.jsonl
echo "--- events"; grep -h acp $T/.agent/events*.jsonl 2>/dev/null | tail -3
rm -rf "$T"
