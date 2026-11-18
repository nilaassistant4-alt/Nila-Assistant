# ADR-0007 — The agent is named Nila

**Status:** Accepted

## Context

The implementation was built under the neutral package name `personal-brain`
with a persona-free system prompt ("You are the user's private, local-first
personal brain"). The product specification names the agent **Nila**.

## Decision

The agent's *persona* is Nila: the system preamble, the offline greeting, the
REPL banner and the API status identify her by name. The **package, module and
CLI names stay `brain`/`personal-brain`**.

Rationale: `brain` is the stable engineering identifier — it appears in import
paths (`brain.memory.vault`), the CLI binary, `$BRAIN_HOME`, environment
variables (`BRAIN_SYNC_TOKEN`, `BRAIN_UPDATE_TOKEN`), config keys
(`brain config set agent.provider`), the envelope magic (`PBX1`), and HKDF domain
separation strings (`pbx/sync/opaque-id/v1`). Renaming any of those would
invalidate existing vaults, break key derivation compatibility and force a data
migration for zero user benefit.

## Consequences

**Gained:** a named, consistent persona in every user-facing surface, with no
migration risk and no change to on-disk formats or derived keys.

**Paid:** a naming split between the persona (Nila) and the engineering
identifier (`brain`). Documented here so it is a decision rather than an
inconsistency.

The persona is a *label*, not a claim: Nila does not assert sentience, and the
system prompt's honesty rules (never claim data was sent when no tool sent it;
say plainly when a capability is missing) are unchanged.
