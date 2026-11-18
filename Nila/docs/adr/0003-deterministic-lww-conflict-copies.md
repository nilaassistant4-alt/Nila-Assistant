# ADR-0003 — Deterministic LWW with loss-preserving conflict copies

**Status:** Accepted · **Supersedes:** "local always wins, stash remote"

## Context

Two devices edit the same record offline. Both sync. What happens?

The first implementation resolved this as *local always wins; stash the remote
copy*. It failed badly: device A pushes its edit, device B pulls it, B's stash
becomes dirty, B pushes the stash, A pulls the stash and stashes again — an
infinite ping-pong that never converged. Three further bugs surfaced in the same
area: the "already reconciled" short-circuit ran *before* the dirty check so
nothing was ever pushed; cursors mixed record versions with store-assigned
versions; and skipping blobs whose id was not already local prevented a new
device from bootstrapping.

## Decision

Treat every record as an **LWW register** whose evaluation is identical on every
device, and preserve the loser rather than discarding it.

```
winner = greater updated_at
         tie → lexicographically greater origin device id
loser  → new record  id = "<canonical_id>-conflict-<loser.updated_at>"
                      tags += ["conflict"]
                      title = "[conflict] " + title
                      meta  = {conflict_of, conflict_reason: "concurrent-edit",
                               resolved_by: "lww+preserve"}
```

The conflict id derives from the **loser's** timestamp. Both devices therefore
compute the *same* conflict id independently and converge to an identical
canonical + conflict set, with no coordination and no conflict-resolution round
trip.

Supporting rules:
- **All cursors use the store-assigned `blob["version"]`**, never the record
  version. `expect_version=0` means "must not exist" and surfaces as HTTP 412.
- **Dirty handling precedes the reconciled short-circuit.**
- **Pull unwraps first, skips only on integrity failure** — so a brand-new
  device can bootstrap from an empty vault via `list_keys("")`.
- Records are **re-wrapped on every push**, so per-record keys never leave the
  device and the server cannot correlate across versions.

## Consequences

**Gained:** guaranteed convergence to a fixed point (observed: ~3 alternating
rounds, then every report all-zero and inert for 3 further rounds). Nothing is
ever silently lost — the losing edit is retrievable by
`list_records(tag="conflict")` and visibly titled `[conflict]`.

**Paid:** conflicts leave clutter that a human must eventually resolve, and the
`[conflict]` prefix mutates the title. Sync is *eventually* consistent, so tests
must sync to a fixed point rather than asserting one round is enough — encoded
as `tests/test_sync.py::sync_to_fixed_point`.

**Rejected:** CRDTs (correct but heavy, and an OR-Set over text fields produces
nonsense merges for prose); operational transform (needs a live session);
manual conflict prompts (breaks unattended sync between a laptop and a phone).

## Verification

Concurrent edit → identical canonical body on both devices, identical conflict
sets, `[conflict]` title and full `meta` provenance. Idempotency over 5 rounds.
Tombstone propagation. 8 offline records from both devices all arriving on both.
Foreign vault: `pulled: 0`, reported as `refused`. Malicious server advancing
`version` with corrupted content: refused, not applied, sync continues.
