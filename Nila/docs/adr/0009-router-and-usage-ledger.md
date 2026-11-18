# ADR 0009 — Router with three lanes and a usage ledger

**Status:** accepted (v0.1)

**Decision.** Each message is answered on one lane: this PC (Ollama), online (Gemini), or the
built-in engine. Online requires an explicit request (or `agent.auto_online`), a key, a message
that scans as non-private, and budget in the ledger. The ledger stores counters only; failed calls
count; an unset limit means a conservative default, never "unlimited"; a 429 sets a cool-down.
Every reply carries the lane that produced it. Provider addresses are validated so keys and
local prompts cannot be redirected by editing config.

**Consequences.** Free quotas are not burned by retry loops; nothing goes online silently.
Combining several models' answers is deferred to Phase 5 because it multiplies cost.
