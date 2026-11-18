# ADR-0002 — Plaintext ids, blind content

**Status:** Accepted · **Supersedes:** the original design, which indexed
plaintext keywords

## Context

`memory.db` must support two things at once: exact lookup by id (for `get`,
`update`, sync, and the FTS join) and keyword search. The first design stored
content tokens in the clear in `records_fts`, and tags as
`'["health","finance"]'` in a `tags TEXT` column.

That made search fast and simple — and it leaked. Anyone holding the file could
read your vocabulary and your tag set without breaking a single cipher. For a
system whose entire promise is "encrypted at rest", this was a defect, not a
trade-off.

## Decision

Split the record into three disclosure classes:

1. **Clear:** `id`, `kind`, `created_at`, `updated_at`, `version`, `deleted`,
   `sync_state`. Structural facts needed for lookup and for sync's LWW clock,
   which must be comparable *without* decryption.
2. **Sealed:** `title`, `body`, `tags`, `meta` — each under
   `HKDF(MK, "rec", record_id)` with a distinct domain string.
3. **Blind:** every searchable token, as
   `HMAC(fast_kdf(MK, "pbx/blind-index/v1|" + record_id), token)[:24]`.

Record ids are explicitly **lookup keys, not content**. Their format
(`<kind[:3]>_<random>`) leaks only the coarse class.

## Consequences

**Gained:** a stolen database reveals how many records exist, their classes and
their edit times — nothing else. Blinding is *per record*, so there is no global
vocabulary to frequency-analyse and a leaked token says nothing about other
records. Keyword search still works, with BM25 ranking over blinded term
frequency.

**Paid:**
- **No prefix matching.** `"pass"*` cannot find `"passport"`, because the blind
  of a prefix is not a prefix of the blind.
- **No stemming beyond our own `fold()`** — which we must implement ourselves,
  since SQLite's FTS5 `porter` tokenizer is not compiled into every build
  (verified: `"running"*` matched nothing in this environment).
- **O(records) queries.** Each candidate row needs its own per-record HMAC.
  Single-digit milliseconds at personal scale.

**Escape hatch, not taken:** a *global* blind index keyed by
`HKDF(MK, "global-blind")` would restore O(1) lookup but leaks document
frequency across records. Revisit only if the O(n) scan becomes measurable.

## Verification

23-token probe set (names, card numbers, SSIDs, passphrases, tag words, medical
and place terms) against `memory.db` and `identity.json`: **zero** matches.
`tests/test_memory.py::test_ciphertext_at_rest`,
`tests/test_sync.py::test_cloud_never_sees_plaintext`.

An earlier test asserted `rec.id not in raw` and a second asserted
`rec.id in raw`. The contradiction was resolved in favour of the second: ids are
plaintext by design, and the test now documents why.
