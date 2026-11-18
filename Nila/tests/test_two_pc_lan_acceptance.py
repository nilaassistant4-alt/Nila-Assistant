"""Acceptance tests: stage 12, gap 5 - two PCs, and the owner never types a command.

The pieces were already here (an E2EE blob store, a reference server, a sync
client, and tests that prove them over a socket). What was missing is the only
part the owner touches: a way to start sharing on one PC and join from the other
by *reading two lines off a window*, not by setting environment variables and
editing JSON.

So this file tests the two things that make that true:

* the **join code** is readable and survives being typed by a human - no letter
  that can be mistaken for a digit, dashes and case optional, a wrong code
  refused by the server rather than silently accepted;
* the **join refuses anything that is not a home-network address**, because
  "2 PCs" must not quietly become "your memory on the internet".

The server is started for real, on a loopback address, and joined for real.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import sync_lan  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "cloud" / "sync_server" / "app.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- #
# the join code
# --------------------------------------------------------------------------- #
def test_the_code_is_readable_and_cannot_look_like_a_digit():
    token = sync_lan.make_token()
    assert len(token) == 24
    for confusing in "01ILl5SB":          # 8 is a digit, and kept: it is unmistakable
        assert confusing not in sync_lan.ALPHABET, confusing
    code = sync_lan.join_code(token)
    assert code == "-".join(token[i:i + 4] for i in range(0, 24, 4))
    assert code.count("-") == 5


def test_a_human_typing_the_code_any_way_still_joins():
    token = sync_lan.make_token()
    code = sync_lan.join_code(token)
    for typed in (code, code.lower(), code.replace("-", ""),
                  code.replace("-", " "), f" {code} "):
        assert token in sync_lan.code_variants(typed)


def test_a_token_the_owner_set_himself_is_shown_as_a_fingerprint_not_a_code():
    """`--token` may be any string, so the printed code must not pretend to be it."""
    token = "a-token-with-dashes-and-!-in-it"
    assert not sync_lan.code_is_token(token)
    code = sync_lan.join_code(token)
    assert len(code) == 29 and "-" in code
    assert sync_lan.join_code(token) == code          # stable, not random


# --------------------------------------------------------------------------- #
# addresses
# --------------------------------------------------------------------------- #
def test_only_home_network_addresses_are_accepted():
    for good in ("192.168.1.5", "10.0.0.7", "172.16.4.4", "127.0.0.1"):
        assert sync_lan.is_private(good), good
    for bad in ("8.8.8.8", "1.1.1.1", "example.com", "", "not an address"):
        assert not sync_lan.is_private(bad), bad


def test_joining_a_public_address_is_refused_and_nothing_is_written(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    code = sync_lan.join_code(sync_lan.make_token())
    rc = sync_lan.main(["join", "8.8.8.8", "--code", code, "--home", str(home)])
    printed = capsys.readouterr().out
    assert rc == 2 and "not a home-network address" in printed
    assert not (home / "sync-join-code.txt").exists()
    from brain.config import Config

    assert not dict(Config.load(home).sync or {})       # config untouched


# --------------------------------------------------------------------------- #
# the real thing: one server, one client, over a socket
# --------------------------------------------------------------------------- #
@pytest.fixture()
def host(tmp_path):
    port = free_port()
    token = sync_lan.make_token()
    data = tmp_path / "server-data"
    env = dict(os.environ, BRAIN_SYNC_TOKEN=token)
    proc = subprocess.Popen([sys.executable, str(SERVER), "--port", str(port),
                             "--data", str(data)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:                                              # pragma: no cover
        proc.terminate()
        pytest.skip("the sync server did not start here")
    yield {"port": port, "token": token, "data": data}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:                  # pragma: no cover
        proc.kill()


def test_join_writes_the_address_and_the_token_next_to_the_vault(host, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    code = sync_lan.join_code(host["token"])
    rc = sync_lan.main(["join", "127.0.0.1", "--port", str(host["port"]),
                        "--code", code, "--home", str(home)])
    printed = capsys.readouterr().out
    assert rc == 0, printed
    assert "joined" in printed and "sync-now.bat" in printed
    from brain.config import Config

    sync = dict(Config.load(home).sync)
    assert sync["backend"] == "http"
    assert sync["url"] == f"http://127.0.0.1:{host['port']}"
    assert (home / "sync-join-code.txt").read_text().strip() == host["token"]


def test_a_wrong_code_is_refused_by_the_server_and_changes_nothing(host, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    rc = sync_lan.main(["join", "127.0.0.1", "--port", str(host["port"]),
                        "--code", sync_lan.join_code(sync_lan.make_token()),
                        "--home", str(home)])
    printed = capsys.readouterr().out
    assert rc == 2 and "refused that code" in printed
    assert not (home / "sync-join-code.txt").exists()


def test_without_a_server_the_error_says_what_to_check(host, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    dead = free_port()                                  # nothing listening there
    rc = sync_lan.main(["join", "127.0.0.1", "--port", str(dead),
                        "--code", sync_lan.join_code(host["token"]),
                        "--home", str(home)])
    printed = capsys.readouterr().out
    assert rc == 2 and "no answer from" in printed
    assert "sync-host.bat" in printed and "same Wi-Fi" in printed


def test_the_saved_token_is_used_by_the_client_without_an_environment_variable(
        host, tmp_path, monkeypatch):
    """The owner never sets BRAIN_SYNC_TOKEN - sync-join.bat saves it instead."""
    from brain.cloud.protocol import SyncEngine
    from brain.config import Config
    from brain.memory.store import MemoryStore
    from brain.memory.vault import Vault

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("BRAIN_SYNC_TOKEN", raising=False)
    rc = sync_lan.main(["join", "127.0.0.1", "--port", str(host["port"]),
                        "--code", sync_lan.join_code(host["token"]),
                        "--home", str(home)])
    assert rc == 0

    cfg = Config.load(home)
    cfg.fast_kdf = True
    cfg.agent["device_id"] = "lan-pc"
    cfg.save()
    vault = Vault(cfg)
    vault.initialize("lan-passphrase-77", user="owner", device_label="lan-pc")
    memory = MemoryStore(cfg, vault.session.mk)
    memory.remember("the water can comes on Tuesday")
    try:
        report = SyncEngine(cfg, memory, vault).sync()
        assert report["errors"] == [], report
        assert report["pushed"] >= 1
    finally:
        memory.close()


def test_the_start_window_prints_an_address_port_and_code(capsys, monkeypatch):
    """The host's window is the interface: what it prints is what gets typed."""
    seen = {}

    def fake_call(argv, env=None):                      # don't run a server in a test
        seen["env"] = env
        seen["argv"] = argv
        return 0

    import tools.sync_lan as module

    monkeypatch.setattr(module.subprocess, "call", fake_call)
    monkeypatch.setattr(module, "local_addresses", lambda: ["192.168.1.5"])
    rc = module.main(["start", "--port", "8799", "--data", "/tmp/x", "--home", "/tmp/y"])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "192.168.1.5   port 8799" in printed
    # the code is on screen twice - once as a label, once in the block that gets
    # typed on the other PC - and the address is on screen with its port
    assert printed.count("-") >= 5
    assert "  code    " in printed and "address " in printed
    assert "nothing" in printed.lower() or "encrypted" in printed.lower()
    assert seen["env"]["BRAIN_SYNC_TOKEN"]                  # the server gets the token
    # the server must listen on the network, not just loopback, or the other PC
    # can never reach it - and the window must say what that means
    assert seen["argv"][seen["argv"].index("--host") + 1] == "0.0.0.0"
    assert "same Wi-Fi" in printed or "Wi-Fi" in printed


def test_the_three_scripts_exist_are_crlf_and_only_call_python():
    """A double-click has to work: same rules as every other .bat in the ZIP."""
    for name in ("sync-host.bat", "sync-join.bat", "sync-now.bat"):
        raw = (ROOT / name).read_bytes()
        assert raw.startswith(b"@echo off\r\n"), name
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{name} has a bare LF"
        assert all(byte < 128 for byte in raw), f"{name} is not pure ASCII"
        text = raw.decode()
        assert "%~dp0nila-env.bat" in text, name
        assert "%~dp0nila-common-check.bat" in text, name
        assert "pause" in text, name                       # never closes on the owner
        assert "python " not in text.replace("%NILA_PY%", ""), f"{name} bypasses NILA_PY"
        assert "tools\\sync_lan.py" in text or "brain.py" in text
