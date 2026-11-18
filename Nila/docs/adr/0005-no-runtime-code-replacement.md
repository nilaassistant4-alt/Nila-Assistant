# ADR-0005 — No runtime code replacement

**Status:** Accepted

## Context

The brief asks for "continuous sync that allows local updates without resetting
state". Read literally, that invites hot-patching a running Python process —
swapping modules in `sys.modules`, reloading importers, monkey-patching live
objects.

That path is a reliability disaster. A half-reloaded module graph produces
objects whose classes no longer match their `isinstance` checks, stale closures
holding references to old code, and failures that appear hours later in an
unrelated call. For a system holding encrypted personal data and cryptographic
key material, an unpredictable failure mode is unacceptable.

## Decision

**Code updates install to disk and take effect on restart. Data-like artifacts
hot-apply immediately. State is never touched.**

```
updates/installed/<version>/     ← unpacked artifacts (zip-slip + symlinks refused)
updates/current                  ← atomic swap via current.tmp
updates/previous                 ← one-command rollback target
updates/history.jsonl            ← append-only record of every apply
```

| Artifact type | Applies | Restart? |
|---|---|---|
| `voice_model` | hot | no |
| `prompt` | hot | no |
| `config` | hot, **merged — never clobbers user settings** | no |
| `integration` | hot | no |
| `package` (code) | staged to `installed/<v>`, `current` repointed | **yes** — `requires_restart: true` |

Installation is **stage → hash-verify every artifact → unpack → optional import
test → atomic pointer swap → prune (keep 3)**. Any hash failure wipes staging
and aborts, so a partial install can never become `current`.

The updater is structurally unable to reach user state: `brain/updates/` has no
import path to `memory.store`, and no write access to
`$BRAIN_HOME/{memory.db, identity.json, audit.jsonl}`. Invariant 4 is enforced
by the dependency graph, not by discipline.

## Consequences

**Gained:** "continuous updates without resetting state" is satisfied in the
sense that matters — your memories, keys, devices, enrolment and audit history
survive every update, and a bad release is one `brain update rollback` away from
gone. Verified: apply 0.2.0 → 0.2.1 → rollback to 0.2.0, with three separate
tamper variants rejected before that.

**Paid:** code changes are not live until restart. An agent mid-conversation
keeps running the old code. That is a visible, explainable limitation rather than
a subtle one.

**Rejected:** `importlib.reload` chains (module-graph corruption); subprocess
supervision with exec-on-update (loses the in-process session key and complicates
the threat model); container-style immutable deploys (contradicts the
local-first single-machine deployment decision).
