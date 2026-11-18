# ADR-0001 — Zero mandatory dependencies

**Status:** Accepted · **Date:** 2025 · **Deciders:** Nila MVP

## Context

The deliverable is a ZIP that must run on the user's machine *today*, on a
local-first single-machine deployment. An environment probe found Python 3.13
with `numpy`, `scipy`, `soundfile`, `sqlite3`, `httpx`, `requests`, `pydantic`,
`yaml` and `sklearn` present — but **no `cryptography`, no `fastapi`, no `torch`,
no `transformers`**.

A personal brain that refuses to start because a wheel failed to build is worse
than no brain. Requiring `pip install` before first run also breaks the promise
that the ZIP is self-contained and portable.

## Decision

Nila has **zero mandatory third-party dependencies**. Everything runs on the
Python standard library. Optional capabilities degrade gracefully behind
capability probes.

| Need | Stdlib path | Optional upgrade |
|---|---|---|
| Symmetric encryption | **Suite 1:** HMAC-SHA256-CTR (encrypt-then-MAC) | **Suite 2:** AES-256-GCM via `cryptography` |
| Update signatures | `hmac-sha256` | `ed25519` via `cryptography` |
| HTTP server | `http.server.ThreadingHTTPServer` | — |
| HTTP client | `urllib.request` | `httpx` / `requests` |
| Audio I/O | `wave` + `audioop`-free manual decode | `soundfile` |
| Voice features | Hand-rolled MFCC | `numpy`-accelerated path |
| Embeddings | `HashingEmbedder` (512-d, signed hashing) | `SentenceTransformerBackend` |
| Config | `json` | `yaml` |

The envelope format carries a **suite byte** so both encryption paths coexist and
a vault can be opened on a machine that lacks `cryptography`.

## Consequences

**Gained:** runs anywhere Python 3.10+ runs; the ZIP is genuinely portable; no
supply-chain surface for mandatory components; `brain doctor` can report
capability honestly instead of crashing on import.

**Paid:** Suite 1 is a home-grown construction — HMAC-SHA256-CTR is sound in
principle (CTR from a CSPRNG-derived keystream, encrypt-then-MAC with AAD) but
it is not AES and it has not been externally reviewed. AES-256-GCM is preferred
whenever `cryptography` is available. The hand-rolled MFCC is slower than a
`numpy`/`librosa` path. The hashing embedder cannot do conceptual similarity.

**Rejected:** bundling wheels in the ZIP (platform-specific, bloats the archive,
still fails on exotic targets); requiring `pip install` at first run (breaks
portability); vendoring a crypto library (maintenance burden and a false sense
of review).

## Verification

`brain doctor` reports the active suite and every optional dependency present.
Observed on the reference machine: `crypto envelope PASS
hmac-sha256-ctr (stdlib)`, `optional deps PASS numpy, soundfile`.
