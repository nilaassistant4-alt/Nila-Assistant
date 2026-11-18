# ADR 0008 — The second factor fails closed

**Status:** accepted (v0.1)

**Context.** The CLI opened the vault with the passphrase alone even when an authenticator was
enrolled, and with nothing enrolled it silently granted a full-tier session. The owner's
requirement is "no action without real-time authorization".

**Decision.** `open_vault` requires the authenticator code when one is enrolled; refuses when no
factor exists except for the setup commands; a break-glass recovery code is supported. The
ceremony's passphrase mode is refused unless `identity.allow_degraded` is set. "Degraded" means a
*stronger* factor exists and was not used, not that none exists.

**Consequences.** A leaked passphrase is not enough. First run must enrol an authenticator
(`brain totp`, which confirms the code before saving). Losing both phone and recovery codes loses
the vault by design.
