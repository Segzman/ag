# Channels (shared rooms, hand-off chains)

```sh
{{AG}} --dir <state> channel create room --agents a,b
{{AG}} --dir <state> channel post room "@a review, then @b ship" [--as AGENT|user] [--max-hops 8] [--context 20]
{{AG}} --dir <state> channel log room [--tail N]
{{AG}} --dir <state> channel list
{{AG}} --dir <state> channel rm room
```

An `@member` in a post wakes that agent; its final reply is posted back automatically, so `@b` in a reply continues the chain. `--as` defaults to `$AG_PARENT_AGENT`; unknown `@names` are reported under `ignored`. `--max-hops N` caps the total number of wakes a post can cause (default 8), which stops ping-pong loops. `--context N` = how many recent room messages each woken agent sees (default 20). Inside a turn you can post explicitly: `channel post room "..." --as <self>` (the final reply is auto-posted anyway).
