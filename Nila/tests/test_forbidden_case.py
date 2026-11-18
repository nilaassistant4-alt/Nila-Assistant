"""The never-read list must not depend on letter case (Windows/macOS paths are case-insensitive)."""
from __future__ import annotations

from pathlib import Path

import pytest

from brain.agent.host_tools import forbidden_reason


@pytest.mark.parametrize("path", [
    "C:/Users/me/AppData/Roaming/Microsoft/Credentials/ABC123",
    "C:/Users/me/AppData/Roaming/Microsoft/credentials/ABC123",
    "C:/Users/me/APPDATA/ROAMING/MICROSOFT/CREDENTIALS/x",
    "C:/Users/me/appdata/local/google/chrome/User Data/Default/Login Data",
    "C:/Users/me/AppData/Local/Microsoft/Vault/x.vcrd",
    "C:/Users/me/AppData/Local/MICROSOFT/Protect/S-1-5/x",
    "/home/me/.SSH/id_rsa", "/home/me/.Ssh", "/home/me/.AWS/credentials",
    "/home/me/.MOZILLA/firefox/x/key4.db", "/home/me/LOGIN DATA",
    "/home/me/Backups/KEYS.KDBX", "/home/me/.Config/Google-Chrome/Default/x",
])
def test_credential_paths_blocked_in_any_casing(path):
    assert forbidden_reason(Path(path)), path


@pytest.mark.parametrize("path", [
    "/home/me/Documents/notes.txt", "/home/me/Downloads/photo.jpg",
    "/home/me/projects/app/main.py", "C:/Users/me/Pictures/holiday.png",
    "/home/me/Documents/credit-union-letter.txt",
])
def test_ordinary_paths_still_readable(path):
    assert forbidden_reason(Path(path)) is None, path


def test_symlink_to_mixed_case_credential_is_caught(tmp_path):
    target = tmp_path / ".SSH"; target.mkdir(); (target / "k").write_text("x")
    link = tmp_path / "innocent"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert forbidden_reason(link / "k")
