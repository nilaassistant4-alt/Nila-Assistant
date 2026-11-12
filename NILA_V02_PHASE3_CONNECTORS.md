# #3 — Connectors screen: what changed

**Archive:** `Nila-verification-93bbfb84.zip`
**SHA-256:** `93bbfb84d21d418ec45636920265d5001f1fcfbcfc251241ad59f1d7268816ca`
**Built by:** `scripts/build_package.py` → 134 entries, 90/90 source modules, 493,374 bytes
**Tests I wrote:** `tests/test_connectors.py`, 33 tests (your acceptance tests override mine)

## Every file changed

| File | What changed |
|---|---|
| `brain/agent/connectors.py` | **new** — catalogue, ON/OFF state, sealed key storage, today's usage, fixed-prompt test call, fresh-login approvals |
| `brain/identity/ceremony.py` | `authorise_action()`, `consume_action()`, `pending_actions()`, `grant_from_fresh_session()`; approvals in `approvals.json` |
| `brain/agent/router.py` | `connector_settings` hook; an explicit **OFF** is honoured, and reported as the reason before "no key" |
| `brain/agent/agent.py` | `Brain.connectors` (one registry, shared with the dashboard) and the router hook |
| `brain/api/gateway.py` | `/v1/connectors` GET, `/authorise`, `/switch`, `/key` POST/DELETE, `/test` |
| `brain/api/static/dashboard.html` | "Connectors" panel + the Approve form |
| `brain/api/static/dashboard.js` | `loadConnectors()`, `cxRow()`, `cxIntent()`, `cxApprove()` |
| `brain/api/static/dashboard.css` | `.cxrow/.cxhead/.cxname/.cxstate/.cxmeta` |
| `brain/cli/main.py` | `brain connectors list\|on\|off\|key\|remove-key\|test\|approve` |
| `brain/identity/totp.py` | `counter_for()` spend rule corrected (see bugs) |
| `brain/memory/vault.py` | `Session.fresh_login_spent`, `Vault.forget_fresh_login()` |
| `tests/test_connectors.py` | **new** — 28 tests |
| `docs/ARCHITECTURE.md` | the module, and the `approvals.json` row |
| `CHANGELOG.md` | the #3 section |

## The brief, item by item

| Requirement | Where it is enforced | Test |
|---|---|---|
| Lists Ollama, Gemini, OpenAI-compatible, Anthropic + outside services | `CATALOGUE` in `connectors.py` | `test_the_screen_lists_every_ai_provider` |
| Name, ON/OFF, key exists, allowed hosts, today's usage | `status()`; usage read from the same ledger the budget uses | `test_each_row_carries_hosts_and_today_usage` |
| **Never show the key** | only `has_key`, last 4 chars, fingerprint; no route returns it | `test_no_response_ever_contains_a_secret` (walks the whole JSON tree) |
| ON/OFF needs **fresh login verification** | `authorise_action` → single-use grant bound to the session | `test_switching_requires_an_approval`, `test_an_approval_is_spent_by_one_action`, `test_an_approval_from_another_session_does_not_count`, `test_a_stale_session_cannot_approve` |
| No payment/bank/card connectors | the catalogue has none; a test asserts no row offers one | `test_there_is_no_payment_connector` |
| Gemini key entered **hidden** via CLI, stored in the vault | hidden `getpass` prompt or `--key-env`; no `--key` argument exists; sealed in the identity blob | `test_the_cli_refuses_a_key_on_the_command_line`, `test_a_key_never_lands_in_a_plaintext_file`, `test_a_key_is_stored_sealed` |
| Dashboard **Test** button: one small fixed prompt, counted against budget | `ConnectorRegistry.test()`; `TEST_PROMPT` is constant and the parameter is refused | `test_a_test_call_is_counted_against_the_budget`, `test_the_test_prompt_cannot_be_replaced`, `test_a_test_call_is_refused_when_the_budget_is_spent`, `test_a_failed_test_call_is_still_counted` |
| ChatGPT/Claude "optional, OFF" | `optional: True`, `default_on: False` | `test_paid_providers_default_to_off` |

## Bugs found while building #3 (all fixed)

1. **`counter_for()` refused every step after the stored ceiling.** The rule was
   `counter <= min_counter: skip`, so approving anything in the same 30-second
   window as the unlock failed with "invalid or already-used". Found by driving
   the real CLI, not by reading. Equality is the correct rule.
2. **The CLI accepted one authenticator code for unlimited commands.** It called
   `verify()` (mathematics only) and never marked the step spent. Audit B6's fix
   had reached the API ceremony but not the main human route.
3. **A refused approval left no trace.** Wrong passphrase, missing code, stale
   grant: all silent. Now `action_refused` / `connector_refused` are journaled.
4. **The key was read before the approval.** A refused `brain connectors key`
   consumed a stdin line that belonged to the prompt; the order is now approve,
   then read.
5. **A key entered in the screen never reached the router.** The screen read the
   vault; the router built its provider from the environment. You could add a
   Gemini key, see "key ending 7890", and still be told "no online key set".
   `key_material()` is now the single answer, `build_provider()` accepts a key in
   the config, and a switched-off connector lends no key.

## Honest limits

- **I could not reach a real provider from this sandbox.** The test call was
  exercised against a stub that implements the same `complete()` contract, and
  once against the real Gemini endpoint, which failed at TLS (`SSL EOF`) because
  outbound access is blocked here. Whether a real key works on your machine is
  **not verified by me**; the Test button is exactly the thing that will tell
  you.
- **The dashboard was verified by serving it and driving the HTTP routes**, not
  by clicking in a browser. The panel's HTML/CSS/JS are checked in (CSP-safe, no
  inline script) and the JS parses, but no browser was available.
- **The passphrase typed into the dashboard's Approve form** is held in that
  input, sent once, and cleared on every outcome (including failure). It is not
  stored, but it does travel over the loopback HTTP connection - the same
  channel `serve` already uses for `/v1/auth/verify`.
- **`approvals.json` is plaintext**, like `policy`. It holds action names,
  session ids, expiry and use counts - no secrets, no personal data - and is
  written 0600.
- **Python 3.12 / 3.14: not run.** This sandbox has only 3.11.2 and no way to
  install another interpreter.
