#!/usr/bin/env python3
"""Two PCs, one memory - over the home network, with nothing to install.

The owner has two computers and one Nila. The pieces already existed (an E2EE
blob store, a reference server, a sync client); what was missing was the part he
would actually touch: a way to start the server on one PC and point the other at
it without typing a command, a token, or an IP address.

This does that, and it is honest about what it is:

* **The first run on the host PC prints a join code.** Six groups of four
  letters, chosen from an alphabet with no O/0, I/1 or S/5 in it, so it can be
  read down a phone or typed without a mistake. The join code *is* the token:
  one secret, not two, and the other PC proves it by being accepted.
* **The second PC is told three things**: this PC's address on the home network,
  the port, and the join code. `join` writes them into that PC's config.
* **Nothing is sent to the internet.** Both ends must be on the same network;
  `serve` binds 0.0.0.0 (so the other PC can reach it) and says which addresses
  it is reachable on, and `join` refuses a public address.
* **The data stays ciphertext end to end.** The server carries blobs it cannot
  read. A wrong join code is refused by the server, and nothing is overwritten.

Commands:
    start [--port 8790] [--data FOLDER]   run the server and show the join code
    join ADDRESS [--port 8790] [--code ...] [--home V]   point this PC at that one
    code                                  show this PC's join code again
    status [--home V]                     what this PC is pointed at
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 8790
TOKEN_ENV = "BRAIN_SYNC_TOKEN"
CODE_FILE = "sync-join-code.txt"

#: Exactly the characters that cannot be mistaken for each other when read down a
#: phone or typed by hand: no O/0, no I/1, no S/5, no B/8, and no L (a lower-case
#: l and a 1 are the same stroke in most fonts).
ALPHABET = "ACDEFGHJKMNPQRTUVWXY2346789"
WORDS = 4                     # 4 groups of 4 characters = 32 chosen from 25


def _config(home: str | Path):
    from brain.config import Config

    return Config.load(Path(home).expanduser())


def make_token() -> str:
    """A fresh sync token that is easy to read out: 24 characters, 4 per group."""
    return "".join(secrets.choice(ALPHABET) for _ in range(24))


def join_code(token: str) -> str:
    """The token in readable groups - or a fingerprint if it was set by hand.

    A token the owner typed himself (`--token` or `$BRAIN_SYNC_TOKEN`) may contain
    anything, so it cannot be shown as fours; then the code shown is a one-way
    fingerprint, and the window says plainly that the token itself must be typed
    on the other PC.
    """
    text = str(token or "").upper()
    if text and all(c in ALPHABET for c in text) and len(text) % 4 == 0:
        return "-".join(text[i:i + 4] for i in range(0, len(text), 4))
    digest = hashlib.sha256(("nila-join|" + str(token or "")).encode()).hexdigest()
    out = []
    for group in range(6):
        value = int(digest[group * 8:(group + 1) * 8], 16)
        letters = ""
        for _ in range(4):
            letters += ALPHABET[value % len(ALPHABET)]
            value //= len(ALPHABET)
        out.append(letters)
    return "-".join(out)


def code_is_token(token: str) -> bool:
    """True when the printed code really is the token (so it can be typed in)."""
    text = str(token or "").upper()
    return bool(text) and all(c in ALPHABET for c in text) and len(text) % 4 == 0


def normalise_code(code: str) -> str:
    """Accept what a person types - spaces, a missing dash, lower case."""
    keep = [c for c in str(code or "") if c.isalnum()]
    return "".join(keep)


def code_variants(code: str) -> list[str]:
    """A token is case-sensitive, but a person types in whatever case he likes."""
    base = normalise_code(code)
    seen: list[str] = []
    for candidate in (base, base.upper(), base.lower()):
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def token_exists(home: Path) -> str:
    """The token this PC already uses, or a new one, saved next to the vault."""
    found = os.environ.get(TOKEN_ENV, "").strip()
    if found:
        return found
    path = home / CODE_FILE
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text
    return ""


def save_token(home: Path, token: str) -> None:
    path = home / CODE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:               # Windows has no POSIX modes; the vault ACL covers it
        pass


def local_addresses() -> list[str]:
    """The addresses another PC on this network could use, private ones first."""
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("10.255.255.255", 1))     # no packet is sent
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found:
                found.append(addr)
    except OSError:
        pass
    out = []
    for addr in found:
        try:
            if ipaddress.ip_address(addr).is_loopback:
                continue
        except ValueError:
            continue
        out.append(addr)
    private = [a for a in out if ipaddress.ip_address(a).is_private]
    return private + [a for a in out if a not in private]


def is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def cmd_start(args) -> int:
    home = Path(args.home or os.environ.get("BRAIN_HOME")
                or Path.home() / ".personal-brain").expanduser()
    token = (str(getattr(args, "token", "") or "").strip()
             or token_exists(home) or make_token())
    if not token_exists(home):
        save_token(home, token)
    data = Path(args.data).expanduser() if args.data else home / "sync-server"
    data.mkdir(parents=True, exist_ok=True)
    code = join_code(token)
    addresses = local_addresses()
    print("Nila sync server - your other PC will read from this one.", flush=True)
    print(f"  folder kept here : {data}", flush=True)
    print(f"  join code        : {code}", flush=True)
    for address in addresses or ["(no network address found)"]:
        print(f"  address          : {address}   port {args.port}", flush=True)
    print("", flush=True)
    print("On the OTHER PC, open sync-join.bat and type:", flush=True)
    if addresses:
        print(f"  address {addresses[0]}   port {args.port}", flush=True)
    print(f"  code    {code}", flush=True)
    if not code_is_token(token):
        print("  (you set that token yourself, so the code above only proves this PC "
              "is the right one - type the token itself on the other PC)", flush=True)
    print("", flush=True)
    print("Everything is encrypted before it leaves this PC; the server cannot read it.",
          flush=True)
    print("While this window is open, any PC on this Wi-Fi that has the code can sync.",
          flush=True)
    print("Press Ctrl+C in this window to stop sharing.", flush=True)
    env = dict(os.environ, **{TOKEN_ENV: token})
    server = ROOT / "cloud" / "sync_server" / "app.py"
    if not server.is_file():
        print(f"error: {server} is missing - the download is incomplete.", flush=True)
        return 2
    try:
        # 0.0.0.0, not loopback: the whole point is that the other PC on the home
        # network can reach it. The token is what keeps strangers out, and the
        # window above says so in a sentence rather than a footnote.
        return subprocess.call([sys.executable, str(server), "--host", "0.0.0.0",
                                "--port", str(args.port), "--data", str(data)], env=env)
    except KeyboardInterrupt:              # pragma: no cover - the owner pressed Ctrl+C
        print("\nstopped.", flush=True)
        return 0


def cmd_code(args) -> int:
    home = Path(args.home or os.environ.get("BRAIN_HOME")
                or Path.home() / ".personal-brain").expanduser()
    token = token_exists(home)
    if not token:
        print("No join code on this PC yet. Run sync-host.bat once - it makes one.",
              flush=True)
        return 2
    print(join_code(token), flush=True)
    return 0


def cmd_join(args) -> int:
    home = Path(args.home or os.environ.get("BRAIN_HOME")
                or Path.home() / ".personal-brain").expanduser()
    address = str(args.address or "").strip()
    if not address:
        print("error: give the address the other PC printed, e.g. 192.168.1.5",
              flush=True)
        return 2
    if not is_private(address):
        print(f"error: {address} is not a home-network address. Two PCs sync over "
              f"your own Wi-Fi, and Nila does not send memory to the internet.",
              flush=True)
        return 2
    attempts = code_variants(args.code)
    if not attempts:
        print("error: type the join code the other PC printed.", flush=True)
        return 2
    url = f"http://{address}:{args.port}"

    # The code is checked against the server, not against a rule: the server
    # answers 200 for the right token and 401 for a wrong one, so the proof is the
    # same one a real sync will use a second later. Case is tried both ways
    # because the token is case-sensitive and the owner is typing fours by hand.
    import urllib.error
    import urllib.request

    def probe(token: str) -> str:
        request = urllib.request.Request(url + "/v1/blobs?prefix=nila-probe",
                                         headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return "ok" if response.status == 200 else f"http {response.status}"
        except urllib.error.HTTPError as exc:
            return "wrong" if exc.code == 401 else f"http {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            return f"unreachable: {exc}"

    # Try the code as the token itself first (the host prints the same string on
    # both sides when the token IS the code), then say exactly what is wrong.
    accepted = ""
    verdict = "wrong"
    for candidate in attempts:
        verdict = probe(candidate)
        if verdict.startswith("unreachable"):
            print(f"error: no answer from {url} - is sync-host.bat still running on "
                  f"the other PC, and are both on the same Wi-Fi?", flush=True)
            return 2
        if verdict == "ok":
            accepted = candidate
            break
        if verdict != "wrong":
            print(f"error: the other PC answered {verdict}.", flush=True)
            return 2
    if not accepted:
        print("error: the server refused that code. Copy it again from the other "
              "PC's window - it is the six groups of four, and dashes do not matter.",
              flush=True)
        return 2

    cfg = _config(home)
    cfg.sync = {"backend": "http", "url": url, "token_env": TOKEN_ENV}
    cfg.save()
    save_token(home, accepted)
    print(f"joined: this PC will sync with {url}", flush=True)
    print(f"  token saved next to the vault ({CODE_FILE})", flush=True)
    print("  now run sync-now.bat to pull the other PC's memory into this one.",
          flush=True)
    return 0


def cmd_status(args) -> int:
    home = Path(args.home or os.environ.get("BRAIN_HOME")
                or Path.home() / ".personal-brain").expanduser()
    cfg = _config(home)
    sync = dict(cfg.sync or {})
    token = token_exists(home)
    out = {"ok": True, "sync": sync, "has_token": bool(token),
           "join_code": join_code(token) if token else ""}
    print(json.dumps(out, indent=2), flush=True)
    if str(sync.get("backend") or "local") != "http":
        print("This PC is not joined to another one yet (run sync-join.bat).", flush=True)
    return 0


#: Inline commands, so nothing has to be typed at a shell prompt.
INLINE = {"start": cmd_start, "serve": cmd_start, "join": cmd_join,
          "code": cmd_code, "status": cmd_status}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in INLINE and " " in argv[0]:
        # `brain.py sync-lan "join 192.168.1.5"` - the shape a .bat echoes whole.
        argv = argv[0].split() + argv[1:]
    parser = argparse.ArgumentParser(prog="sync-lan",
                                     description="Two PCs, one Nila, over your own network.")
    parser.add_argument("command", nargs="?", default="status",
                        choices=sorted(INLINE))
    parser.add_argument("address", nargs="?", default="")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data", default="")
    parser.add_argument("--code", default="")
    parser.add_argument("--token", default="", help=argparse.SUPPRESS)
    parser.add_argument("--home", default="")
    args = parser.parse_args(argv)
    if args.code and args.command == "join" and not args.address:
        print("error: give the address too: join 192.168.1.5 --code XXXX-XXXX",
              flush=True)
        return 2
    return INLINE[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
