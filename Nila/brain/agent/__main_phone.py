"""``python -m brain.agent.__main_phone`` - the phone bridge in its own window.

Thin: argument parsing lives here, the loop lives in ``brain.agent.phone``. The
vault opening is the *same* ceremony as the CLI (``open_vault``) on purpose - the
phone must not become a side door around the authenticator, the recovery rules or
the idle lock. The only difference is that this window stays open.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="brain-phone",
                                     description="Answer Telegram messages from your phone.")
    parser.add_argument("--home", default=os.environ.get("BRAIN_HOME", ""))
    parser.add_argument("--passphrase-env", default="", metavar="VAR",
                        help="read the passphrase from this variable (never from argv)")
    parser.add_argument("--passphrase-stdin", action="store_true")
    parser.add_argument("--once", action="store_true", help="one round trip, then exit")
    parser.add_argument("--timeout", type=float, default=25.0,
                        help="long-poll seconds (default 25)")
    parser.add_argument("--max-rounds", type=int, default=0)
    # The vault ceremony is the CLI's (`open_vault`), so the same three ways in
    # must be accepted here: an authenticator code, a one-time recovery code, or
    # an explicitly degraded session. Without these, the phone window would be
    # the one Nila entry point that cannot get past its own second factor.
    parser.add_argument("--totp", default="")
    parser.add_argument("--recovery", default="")
    parser.add_argument("--fast-kdf", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from ..agent.agent import Brain
    from ..agent.phone import PhoneBridge
    from ..agent.telegram import Telegram, TelegramError
    from ..cli.main import open_memory, open_vault

    try:
        config, vault = open_vault(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    memory = open_memory(config, vault)
    bridge = Telegram(vault=vault)
    try:
        if not bridge.configured:
            print(bridge.status()["how"], file=sys.stderr)
            return 2
        brain = Brain(config, vault, memory)
        loop = PhoneBridge(bridge, brain=brain,
                           printer=lambda line: print(line, flush=True))
        print(f"Nila is listening on Telegram for chat {bridge.chat_id}."
              "\nClose this window to stop (the vault locks when it closes).", flush=True)
        result = loop.run(once=args.once, max_rounds=args.max_rounds, timeout=args.timeout)
        if args.once:
            print(result)
        if args.once and not result.get("ok", True):
            print("Could not reach Telegram. Check this PC's internet, then run this "
                  "window again - nothing was lost; messages wait in Telegram.",
                  file=sys.stderr)
        return 0 if result.get("ok", True) else 1
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    except TelegramError as exc:
        print(f"phone bridge: {exc}", file=sys.stderr)
        return 1
    finally:
        memory.close()
        vault.lock()


if __name__ == "__main__":
    raise SystemExit(main())
