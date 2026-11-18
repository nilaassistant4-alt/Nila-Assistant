# ADR 0010 — Taint rule

**Status:** accepted (v0.1)

**Decision.** Tools tagged network, filesystem, media or shell produce *untrusted* output. Once
one has succeeded in a turn, every tool with risk "high" or tag "destructive" (and unknown tools)
is held for the remainder of that turn, regardless of grants. The owner sends a new message to
proceed.

**Consequences.** Instructions hidden in a file, photo metadata or command output cannot trigger a
risky action. Cost: some legitimate multi-step requests need a second message.
