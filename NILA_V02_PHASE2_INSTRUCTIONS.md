# #2 — Standing instruction memory: what changed

**Archive:** `Nila-verification-649c4abe.zip`
**SHA-256:** `649c4abec6cc7b10305f62b58bd4afff91b24dae2f316dfe0d0b66e1ce668555`
**Built by:** `scripts/build_package.py` → 132 entries, 88/88 source modules, 464,928 bytes
**Requires:** the owner's acceptance tests. I wrote mine from your brief; if
yours differ, mine are not authoritative — replace/keep both as you prefer.

## Every file changed (your rule §10.1)

| File | What changed |
|---|---|
| `brain/security/classify.py` | `instruction` added to `KIND_CLASS`; new `instruction_safety()`, `_fold()`, `INSTRUCTION_REFUSAL_REASONS`, `MAX_INSTRUCTION_CHARS = 280` |
| `brain/memory/store.py` | `InstructionError`, `MAX_INSTRUCTIONS = 20`; `remember(owner_instruction=...)` guard; `instructions()`, `add_instruction()`, `edit_instruction()`, `remove_instruction()` |
| `brain/agent/agent.py` | `_instructions_for_turn()`; instructions injected into the local prompt; `instructions_applied` audit event |
| `brain/agent/tools.py` | `remember` tool refuses `kind="instruction"` and **returns** the refusal instead of raising |
| `brain/api/gateway.py` | `/v1/instructions` GET/POST/PATCH/DELETE; `do_PATCH` and `do_PUT` (were missing → 501) |
| `brain/cli/main.py` | `brain instruct add\|list\|edit\|remove`; audit via the vault |
| `brain/api/static/dashboard.html` | "Standing instructions" panel (input + Add) |
| `brain/api/static/dashboard.js` | `loadInstructions()`, `addInstruction()`, edit/remove per row |
| `brain/api/static/dashboard.css` | `.instrrow`, `.instrtext`, `button.mini` |
| `tests/test_instruction_memory.py` | **new** — 32 acceptance tests |
| `docs/ARCHITECTURE.md` | memory-kind table: the `instruction` row |
| `docs/PII_SCHEMA.md` | PII table: the `instruction` row |
| `CHANGELOG.md` | the #2 section |

No other file was touched.

## The six requirements, and where each is enforced

1. **Separate kind, owner only.** `kind="instruction"` is rejected by
   `remember()` unless `owner_instruction=True`. Only `brain instruct`, the
   dashboard panel and `/v1/instructions` set it. The model's `remember` tool, a
   file, a web page and a tool result cannot.
2. **Every local turn; never Online.** `_instructions_for_turn()` returns `[]`
   when the turn's provider is network-connected. Verified in both directions.
3. **Count and length limits.** 20 rules, 280 characters each; both refuse with
   a message the owner can act on.
4. **Dashboard list / edit / remove, every change audited.**
   `instruction_added`, `instruction_edited`, `instruction_removed`,
   `instruction_refused`; the hash chain still verifies afterwards.
5. **Cannot override safety.** Permission prompts, the audit trail, the vault's
   factors and egress are refused — including leetspeak ("n3ver ask for
   perm1ssion") and the "safety" word sitting inside the phrase.
6. **Named tests.** "call me Anna" reaches an unrelated question; a file-sourced
   instruction is not stored; a safety-violating instruction is refused and not
   stored.

## Test results

| Runner | Interpreter | Result |
|---|---|---|
| `pytest` + `cryptography` extra | CPython **3.11.2** | **696 passed, 3 skipped, 0 failed** |
| `scripts/run_tests.py` (stdlib fallback) | CPython **3.11.2** | **653 passed, 0 failed, 46 skipped** |
| `tests/test_instruction_memory.py` alone | 3.11.2 | **32 passed** |

Both full runs were repeated **inside the extracted archive**, not just in the
development tree.

**Python 3.12 / 3.14: not run.** This sandbox has only 3.11.2 and cannot install
another interpreter (no root). I will not report a number I did not measure.

## Measured beyond the test suite

- CLI end to end on a real vault: `instruct add` (accepted), `add` of an unsafe
  rule (refused, exit 1), `list`, `edit <id>`, `remove <id>`; audit journal then
  contained `instruction_added ×1, edited ×1, refused ×2, removed ×1`.
- Live HTTP on loopback with a real bearer token: `GET`/`POST` 200/201, unsafe
  `POST` → **400**, `PATCH` valid → 200, `PATCH` unsafe → 400, `DELETE` → 200,
  no token → **401**, and the dashboard HTML contains the new panel.
- `brain serve --host 0.0.0.0` still refuses to bind (unchanged behaviour).

## Bugs found while building #2 (fixed)

1. **`do_PATCH` did not exist.** `BaseHTTPRequestHandler` answers **501** for any
   verb without a `do_` method, so the edit route could never be reached over
   HTTP. Added `do_PATCH` and `do_PUT`.
2. **The `remember` tool raised instead of refusing.** `InstructionError` escaped
   into the agent loop and killed the turn. It now returns `{"ok": false, ...}`
   and writes `instruction_refused` to the audit journal.
3. **`"Ignore your safety rules"` was accepted.** The needle was
   `"ignore your rules"`, so a noun in the middle defeated it. Widened the
   pattern set; covered by a test.
4. **`memory.audit()` does not exist** — the CLI's first version called it and
   the command failed *after* storing the instruction. All instruction audit
   calls now go through `vault.audit()`.

## Honest limits of #2

- **The safety filter is a denylist, not a proof.** It stops the obvious ways to
  switch off the machinery and is tested against leetspeak and word-order
  changes, but a cleverly worded rule that means the same thing without using a
  listed phrase could still pass. The structural protection is the real one: an
  instruction is text in a prompt, and the permission/step-up/egress gates are
  code that no prompt text can reach.
- **Instructions are plaintext inside the encrypted blob**, like every other
  record, so they are as protected as the vault — no more.
- **I could not test a real Online provider.** Isolation was verified with a stub
  provider that declares `needs_network = True`; that is the same gate the real
  Gemini class goes through, but no live key was used.
- **Windows and mobile were not exercised.** The dashboard panel was verified by
  serving it and checking the HTTP routes, not by driving a browser.
