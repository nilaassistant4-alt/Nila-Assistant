# What Nila can do (v0.1)

The dashboard's *What Nila can do* list is computed from the real state of the system. This page
explains the states and how to move an item toward **ready**.

| State | Meaning |
|---|---|
| ready (`possible`) | works now |
| asks you first (`needs_permission`) | allowed, but every use needs your approval in the Approvals box |
| needs setup (`needs_setup`) | something must be installed or configured |
| blocked | refused by policy, a limit, or the panic latch |
| not built yet (`planned`) | on the roadmap; do not expect it |

## Items

| Item | How to enable / notes |
|---|---|
| Remember, projects, attachments | always ready while the vault is unlocked |
| Built-in engine | always ready; basic intent matching, not a language model |
| Local AI (Ollama) | install Ollama, `ollama pull llama3.2:3b`, keep it running. Change model: `brain config set agent.local_model <name>` |
| Online AI (Gemini) | `brain connectors key gemini` (hidden prompt, stored sealed) **or** set `GEMINI_API_KEY`; `brain connectors test gemini` proves it works; set limits `agent.online_daily_requests`, `agent.online_rpm`, `agent.online_stop_pct`; model `agent.online_model`; key variable name `agent.online_key_env` |
| Unlock again in the browser | after a session ends, the dashboard shows an `Unlock again` form (passphrase + authenticator code) - no restart, and no need to reach the console |
| Stop Nila | a clean `Stop Nila` button locks the vault and drops keys; a ctrl-c leaves the session in the identity snapshot, so the next start asks for the passphrase |
| Auto-online | **does not exist and is not planned.** Auto is always local or offline; Online is a per-message choice in the chat box. (Older docs listed an `agent.auto_online` switch; no code ever read it, and a test asserts it stays ignored.) |
| Read files | approve each time; choose shared folders with `agent.dashboard_roots` |
| Run a command | approve each time; one plain command; runs in `<data>\workspace` |
| Photos | approve each time; metadata only |
| Export | fresh login check each time |
| Outside services | off by default. `brain connectors list` shows each one, whether it is on, and which hosts it may reach; switching one on asks for a fresh login. Enable the plugin in `integrations.json` first (`"enabled": true`), and every call still asks for `net.host` approval |

## Routing rules

1. **Auto** (default): this PC if Ollama is running, otherwise the built-in engine.
2. **This PC only**: never leaves the device.
3. **Online**: only if a key is set, the message contains no private data, the daily and per-minute
   budget allow it, and the provider is not cooling down after a rate-limit reply. Otherwise it
   answers locally and says why.

## Settings reference (`python brain.py config set <key> <value>`)

`agent.local_model`, `agent.ollama_url` (must be 127.0.0.1), `agent.online_provider`,
`agent.online_model`, `agent.online_key_env`, `agent.online_daily_requests`, `agent.online_rpm`,
`agent.online_stop_pct`, `agent.route_default` (auto | local only; online is per message),
`agent.dashboard_roots`.


## Owner profile (v0.2)

Facts about the owner - what to call them, their language, their work - set with
`brain profile set <field> <value>`, capped at 20 fields of 200 characters. They
are stored encrypted, applied to **every local turn** (the same path as standing
instructions), and are **never** included in the online prompt. Setting one uses
the same safety filter as instructions, so "ignore all rules" is refused whether
it arrives as an order or as a "fact".

## Strict mode (v0.2)

With strict mode on, a tool runs only when one of these is true:

1. it is on the owner's allow list (`brain strict allow <tool>`), or
2. the owner **named** it in this message, or
3. the owner answered Nila's question with "yes" - which authorises **one** call.

Read-only tools still run. Tools whose purpose is to remove the owner from the
loop are refused unconditionally. It is enforced in the agent's tool loop, not in
the prompt, so a message that talks the model into a tool still cannot run it.
A locked vault fails **closed** (nothing runs), never open.
