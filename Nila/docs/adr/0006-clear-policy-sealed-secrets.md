# ADR-0006 — Policy in the clear, secrets sealed

**Status:** Accepted · **Fixes:** a real bug where `require_voice` was unenforceable

## Context

`policy` (`require_voice`, `require_liveness`, `allow_degraded`, `max_attempts`,
`lockout_base_seconds`) originally lived **inside the encrypted identity blob**.

That produced a genuine security bug: the controls that must run *before*
authentication succeeds could not be read while the vault was locked.
`vault.policy()` fell back to hardcoded defaults, so
`identity.allow_degraded = true` set by the user was silently ignored — and, more
dangerously, a policy *tightened* by the user would also have been ignored. Rate
limiting and the second-factor requirement were reading defaults, not policy.

Found by `tests/test_voice.py::test_policy_allows_degraded_when_explicitly_permitted`.

## Decision

Store `policy` as a **clear** top-level key in `identity.json`. Seal everything
else.

The dividing line is: **can this value be used to authenticate, decrypt, or
impersonate?** If yes → sealed. If it is only a boolean or integer describing
*how strict* authentication is → clear.

| Clear (in `identity.json`) | Sealed (in `identity_blob`) |
|---|---|
| `policy.*` — booleans and integers | `voice.template` — the speaker model |
| `attempts.{count, locked_until, last_failure}` | `totp.secret` |
| `kdf` — scrypt parameters (public by definition) | `recovery.codes` |
| `verifier` — `HMAC(MK, SESSION_TAG)` | `hardware_wrap` |
| `devices[id].label`, `device_id` | `sessions` history |

`export_bundle` carries the clear policy across, so an imported device inherits
the same strictness. `_migrate()` hoists policy out of the blob for vaults
created before this ADR.

## Consequences

**Gained:** lockout thresholds and factor requirements are now actually
enforced. An attacker who tampers with `policy` in the clear can only make
authentication *stricter* or *looser* — and loosening it (`allow_degraded`)
still cannot bypass the passphrase, because `verifier` is sealed behind scrypt.

**Paid:** an observer with the file learns your policy settings. This discloses
nothing personal — only that, say, voice is required and lockout base is 30 s.

**Tamper consideration:** a clear `policy` is writable by anyone with filesystem
access. Setting `allow_degraded=true` downgrades to single-factor — but that
attacker already had filesystem access, and still needs the passphrase. Adding
an HMAC over `policy` keyed by MK is a possible hardening step; it is on the
roadmap, not implemented, because it cannot be verified while locked either
(which is the same problem that caused this ADR).
