"""Remembering the passphrase on THIS laptop, so it is typed once and never again.

The owner's complaint, in his own words: *"I don't know what the password is, I
forgot it, and the old password still works... I extract the ZIP, start it, and it
asks for the password again and again. Let me type it once, and after a restart it
must not ask again."*

He is right, and the old build had no answer: every command unlocked the vault and
every unlock asked for the passphrase (and the authenticator code, if one was
enrolled). That is correct cryptography and a bad daily experience, and the two are
not in conflict - the fix is to stop treating "unlocked" as a password prompt.

**What this module does.** After a successful unlock it can seal the *master key*
(not the passphrase - those are different things, see below) into the operating
system's own secret store:

* **Windows: the Credential Locker** (`keyring`), or **DPAPI** if `keyring` is not
  installed. DPAPI encrypts with a key derived from the logged-in Windows account,
  so the blob on disk is useless to another user and useless if copied to another
  machine running as a different account.
* **macOS: the Keychain**, **Linux: Secret Service** - both through `keyring`.

**What "remembered" honestly means.** It means *this Windows account, on this
laptop, can open Nila without typing anything*. That is exactly the behaviour the
owner asked for and it is also a real trade: anyone who can sit at the unlocked
laptop can open Nila. So it is:

* **opt-in**, decided once by `nila-first-run.bat` or `brain passphrase remember`,
* **reversible** in one command (`brain passphrase forget`), and
* **audited** every time it is used (`unlock_via_device_key`), so the journal says
  "opened by the saved device key" instead of pretending a human typed something.

**What it does not store.** Never the passphrase. A passphrase cannot be rotated
once it is on disk in cleartext; a stored *wrapped master key* can be deleted, and
if the laptop is lost the passphrase still protects the files. The master key is
sealed with the OS store's own encryption as well, so it is ciphertext at rest even
though the OS will hand it back to this user.

**The forgetting problem.** The owner does not know his passphrase and is afraid of
losing the vault. That is answered by three things, not one: the passphrase is not
needed once the device key exists; `brain passphrase status` says whether this
laptop can open the vault without it; and `brain recover` lets him set a NEW
passphrase while the vault is open, which is the only honest way to "change the
password" when the old one is unknown.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SERVICE = "nila-assistant"
USER_PLACEHOLDER = "device-master-key"

#: Where the DPAPI fallback (Windows, no `keyring` package) keeps its blob. It is
#: in BRAIN_HOME beside the vault because it is only readable by this Windows
#: account anyway - the encryption is the protection, the folder is just a home.
DPAPI_FILE = "device-key.dpapi"

KEYRING_HINT = "pip install keyring   (Windows: it then uses the Credential Locker)"


class VaultKeyError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# the OS stores
# --------------------------------------------------------------------------- #
def _keyring():
    """The `keyring` package, or None. Imported lazily so a missing one is not fatal."""
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def _dpapi_available() -> bool:
    return sys.platform.startswith("win")


def _dpapi_protect(data: bytes) -> bytes:
    """Encrypt with the logged-in Windows account (CryptProtectData, user scope)."""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> DataBlob:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DataBlob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    src = blob(data)
    out = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(src), "Nila device key", None, None, None, 0, ctypes.byref(out)):
        raise VaultKeyError("Windows refused to protect the device key (DPAPI error)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> DataBlob:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DataBlob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    src = blob(data)
    out = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise VaultKeyError("this Windows account cannot decrypt the saved device key "
                            "(it was saved by a different user or on another PC)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# --------------------------------------------------------------------------- #
# the device key itself
# --------------------------------------------------------------------------- #
def _secret(master_key: bytes, home: Path) -> str:
    """What actually goes into the OS store.

    The raw master key plus the vault home, so a remembered key cannot be replayed
    against a *different* vault that happens to be on the same machine.
    """
    return base64.b64encode(master_key).decode() + "|" + str(Path(home).resolve())


def _parse(blob: str, home: Path) -> Optional[bytes]:
    try:
        encoded, _, stored_home = str(blob).partition("|")
        if stored_home and Path(stored_home) != Path(home).resolve():
            return None                      # a key for another vault
        return base64.b64decode(encoded)
    except Exception:
        return None


def store_path(home: Path) -> Path:
    return Path(home) / DPAPI_FILE


def backend(home: Optional[Path] = None) -> Dict[str, Any]:
    """Which mechanism would be used on this machine, in plain words."""
    kr = _keyring()
    if kr is not None:
        try:
            name = str(kr.get_keyring())
        except Exception:                     # pragma: no cover - broken plugin
            name = "keyring (unknown backend)"
        return {"ok": True, "method": "keyring", "detail": name,
                "can_remember": True,
                "note": "stored in the operating system's own secret store"}
    if _dpapi_available():
        return {"ok": True, "method": "dpapi", "detail": "Windows DPAPI (per-user)",
                "can_remember": True,
                "note": ("no `keyring` package, so the key is sealed with the Windows "
                         "account itself - another user cannot read it, and copying the "
                         "file to another PC does not help either")}
    return {"ok": False, "method": "none", "detail": "no secret store found",
            "can_remember": False,
            "note": (f"on this system the key needs {KEYRING_HINT}; without it Nila "
                     f"asks for the passphrase, which is the safe default")}


def save(master_key: bytes, home: Path, user: str = "") -> Dict[str, Any]:
    """Remember the master key for this machine. Idempotent."""
    if not master_key:
        raise VaultKeyError("nothing to remember - unlock the vault first")
    which = backend(home)
    payload = _secret(master_key, Path(home))
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, user or USER_PLACEHOLDER, payload)
            return {**which, "saved": True, "where": f"keyring:{SERVICE}"}
        except Exception as exc:              # noqa: BLE001 - fall through to DPAPI
            last = f"keyring refused: {type(exc).__name__}: {exc}"
    else:
        last = "no keyring"
    if _dpapi_available():
        path = store_path(home)
        path.write_bytes(_dpapi_protect(payload.encode()))
        try:
            os.chmod(path, 0o600)
        except OSError:                       # pragma: no cover - Windows
            pass
        return {"ok": True, "method": "dpapi", "detail": "Windows DPAPI (per-user)",
                "saved": True, "where": str(path), "note": backend(home)["note"],
                "keyring_problem": last}
    raise VaultKeyError(f"could not remember the key on this system ({last}); "
                        f"try {KEYRING_HINT}")


def load(home: Path, user: str = "") -> Optional[bytes]:
    """The remembered master key, or None. Never raises: a miss is not an error."""
    kr = _keyring()
    if kr is not None:
        try:
            blob = kr.get_password(SERVICE, user or USER_PLACEHOLDER)
            if blob:
                found = _parse(blob, Path(home))
                if found:
                    return found
        except Exception:                     # pragma: no cover - backend trouble
            pass
    path = store_path(home)
    if path.exists() and _dpapi_available():
        try:
            return _parse(_dpapi_unprotect(path.read_bytes()).decode(), Path(home))
        except Exception:
            return None
    return None


def forget(home: Path, user: str = "") -> Dict[str, Any]:
    """Remove the remembered key. The vault is untouched; it will ask again."""
    removed: Dict[str, Any] = {"keyring": False, "file": False}
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(SERVICE, user or USER_PLACEHOLDER):
                kr.delete_password(SERVICE, user or USER_PLACEHOLDER)
                removed["keyring"] = True
        except Exception:                     # pragma: no cover
            pass
    path = store_path(home)
    if path.exists():
        try:
            path.unlink()
            removed["file"] = True
        except OSError:                       # pragma: no cover
            pass
    return {"ok": True, **removed,
            "still_saved": status(home)["remembered"],
            "note": ("Nila will ask for the passphrase again from now on. Your memories "
                     "are not affected - only the shortcut into them was removed.")}


def status(home: Path, user: str = "") -> Dict[str, Any]:
    """Can this machine open the vault without asking? The question he asked."""
    which = backend(home)
    key = load(home, user)
    return {"ok": True,
            "remembered": key is not None,
            "method": which.get("method"),
            "detail": which.get("detail"),
            "can_remember": which.get("can_remember"),
            "where": str(store_path(home)) if which.get("method") == "dpapi" else
                     (f"keyring:{SERVICE}" if which.get("method") == "keyring" else ""),
            "note": ("this laptop opens Nila without asking for the passphrase - "
                     "typing it once at first run was enough"
                     if key is not None else
                     "this laptop will ask for the passphrase on every start; run "
                     "`brain passphrase remember` (or nila-first-run.bat) once")}
