# Progress log

A new session can resume from `ROADMAP.md`, this file and `docs/CAPABILITIES.md`.

## v0.1.0

**Built and tested here (Linux sandbox, Python 3.12 and 3.14.4, with and without the
`cryptography` package):** second-factor enforcement, recovery login, token handling, grant
limits, command hardening, taint rule, usage ledger, Gemini provider (against a mock server),
router, projects, encrypted file store, diagnostics, capability map, dashboard API routes,
offline project intents. The earlier 384-test suite still passes (one test changed on purpose),
plus 43 new tests. Mandate probe: 23 pass, 0 fail, 6 known gaps, 0 errors.

**NOT verified (needs your PC):**
- `setup.bat`, `start-nila.bat`, `report.bat`, `nila-cmd.bat` on Windows 11 (build 26300) with
  Python 3.14.10
- the dashboard rendered in a real browser (JS syntax-checked and the API exercised, but not
  displayed)
- live Ollama and live Gemini calls (Gemini tested only against a mock; model name
  `gemini-flash-latest` is a guess that must be checked against AI Studio)
- the authenticator QR/key flow on your phone
- performance of llama3.2:3b on your RTX 2050 (4 GB)

**Known limits / decisions**
- `run_command` is hardened, not sandboxed (Phase 3 adds a real sandbox).
- The taint rule holds *all* high-risk tools for the rest of a reply once untrusted content
  appears; it errs on the side of asking you to send a new message.
- Router chooses one provider per message; there is no automatic escalate-on-low-quality yet
  (Phase 5).
- Online lane is limited to non-private messages and never attaches stored memories.
- Day boundaries in the usage ledger use the PC's local midnight; providers may reset at a
  different time, so keep the stop-at percentage below 100.

**Next:** collect your first-run errors → v0.2 fixes; then Phase 2 (voice, image, reports).
