"""End-to-end encrypted sync with a deterministic merge rule.

Threat model
------------
The cloud back-end is a **dumb blob store**: it holds bytes it cannot read and
metadata it cannot link to your identity.

::

    MK --HKDF--> Ksync   (sync root key; never leaves the device)
    Ksync --HKDF--> Ko   opaque-id key    remote_id = b64(HMAC(Ko, local_id))
    Ksync --HKDF--> Ke   re-wrap key      body      = encrypt(record_json, Ke, aad=remote_id)
    Ksync --HKDF--> Ki   integrity key    hmac      = HMAC(Ki, remote_id|v|body)

Consequences: the server cannot link two of your records to each other, cannot
link two of your devices, cannot enumerate your ids, and cannot read anything.
Records are **re-wrapped** on push rather than uploaded as their local envelope,
so cloud-side keys are independent of local per-record keys.

Merge semantics
---------------
Each record is an LWW-register with *loss-preerving* conflict handling:

    winner = the copy with the greater ``updated_at``
             (tie broken by origin device id, so every device decides identically)

    local wins  -> keep local as canonical, push it, and store the remote copy as
                   ``<id>-conflict-<remote.updated_at>`` tagged ``conflict``
    remote wins -> adopt remote as canonical, and store our local copy as
                   ``<id>-conflict-<local.updated_at>`` tagged ``conflict``

Because the conflict-copy id is derived from the *loser's* timestamp, both
devices independently generate the **same** conflict record id and therefore
converge to an identical canonical + conflict set. Nothing is ever silently
discarded, and no sync loop can re-detect the same divergence twice (the base
cursor advances past every reconciled remote version).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..memory.vecvault import assert_syncable
from ..crypto.envelope import CryptoError, decrypt, encrypt
from ..crypto.hash import fast_kdf, hkdf, mac
from ..memory.store import MemoryStore, Record
from ..memory.vault import Vault
from ..util.http import RedirectRefused, open_once
from ..util.misc import b64d, b64e, dumps, ensure_dir, now_ms, write_private

MANIFEST_NAME = "manifest.json"


class SyncError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# blob stores
# --------------------------------------------------------------------------- #
class BlobStore:
    """An opaque key/value store with a monotonic per-key version counter.

    ``expect_version`` implements optimistic concurrency: pass the version you
    read (or ``0`` to assert "must not exist"), and the store must reject the
    write if it has moved on. Pass ``None`` to write unconditionally.
    """

    def get(self, key: str) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def put(self, key: str, value: Dict[str, Any], expect_version: Optional[int] = None) -> int:
        raise NotImplementedError

    def delete(self, key: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def list_keys(self, prefix: str = "") -> List[str]:  # pragma: no cover
        raise NotImplementedError


class LocalBlobStore(BlobStore):
    """A directory acting as the cloud store.

    Useful for tests, for air-gapped sync over Syncthing/rsync/an S3-mounted
    volume, and for "sync to my NAS" deployments.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        ensure_dir(self.root)

    def _path(self, key: str) -> Path:
        return self.root / f"{urllib.parse.quote(key, safe='')}.json"

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_bytes())
        except Exception:
            return None

    def put(self, key: str, value: Dict[str, Any], expect_version: Optional[int] = None) -> int:
        p = self._path(key)
        current = self.get(key)
        cur_v = int((current or {}).get("version", 0))
        if expect_version is not None and cur_v != int(expect_version):
            raise SyncError(f"version conflict on {key[:16]}…: "
                            f"expected {expect_version}, store has {cur_v}")
        value = dict(value)
        value["version"] = cur_v + 1
        value["server_ts"] = now_ms()
        write_private(p, json.dumps(value).encode())
        return value["version"]

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_keys(self, prefix: str = "") -> List[str]:
        out = []
        for p in sorted(self.root.glob("*.json")):
            key = urllib.parse.unquote(p.stem)
            if key.startswith(prefix):
                out.append(key)
        return out


class HttpBlobStore(BlobStore):
    """Talks to the reference sync server (``cloud/sync_server/app.py``).

    The bearer token comes from an environment variable (never from the repo).
    Transport should be TLS, but note that a compromised relay still learns only
    blob sizes and access times - the payload is already ciphertext.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 30.0,
                 verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify_tls = verify_tls
        self._ssl_ctx = None
        if not verify_tls:
            import ssl

            self._ssl_ctx = ssl._create_unverified_context()

    def _req(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
             headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            # The bearer token is attached to every request here, so a 3xx must
            # never be followed: it would hand the token to whatever host the
            # relay names. Reproduced with a dummy token before this fix.
            with open_once(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except RedirectRefused as exc:
            return 0, {"error": str(exc)}
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:
                pass
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"error": raw.decode(errors="replace")[:200]}
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SyncError(f"cannot reach sync server {self.base_url}: {exc}") from exc

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        status, payload = self._req("GET", f"/v1/blobs/{urllib.parse.quote(key, safe='')}")
        if status == 404:
            return None
        if status != 200:
            raise SyncError(f"GET {key[:16]}… -> HTTP {status}: {payload}")
        return payload

    def put(self, key: str, value: Dict[str, Any], expect_version: Optional[int] = None) -> int:
        headers = {"If-Match": str(expect_version)} if expect_version is not None else {}
        status, payload = self._req("PUT", f"/v1/blobs/{urllib.parse.quote(key, safe='')}",
                                    value, headers)
        if status in (409, 412):
            raise SyncError(f"version conflict on {key[:16]}…: server says {payload}")
        if status not in (200, 201):
            raise SyncError(f"PUT {key[:16]}… -> HTTP {status}: {payload}")
        return int(payload.get("version", 0))

    def delete(self, key: str) -> bool:
        status, _ = self._req("DELETE", f"/v1/blobs/{urllib.parse.quote(key, safe='')}")
        return status in (200, 204)

    def list_keys(self, prefix: str = "") -> List[str]:
        q = f"?prefix={urllib.parse.quote(prefix)}" if prefix else ""
        status, payload = self._req("GET", f"/v1/blobs{q}")
        if status != 200:
            raise SyncError(f"LIST -> HTTP {status}: {payload}")
        return list(payload.get("keys", []))


def build_store(config: Config, vault: Optional[Vault] = None) -> BlobStore:
    cfg = dict(config.sync or {})
    # `backend: ""` used to raise "unknown sync backend" instead of meaning
    # "local"; `None` from a config file did the same. Both are unset, not a typo.
    kind = str(cfg.get("backend") or "local").strip().lower()
    if kind in ("local", "dir", "folder", "nas"):
        root = cfg.get("path") or str(config.sync_dir / "remote")
        return LocalBlobStore(Path(root).expanduser())
    if kind in ("http", "https", "cloud", "s3"):
        url = cfg.get("url")
        if not url:
            raise SyncError("sync.backend=http requires sync.url")
        token_env = cfg.get("token_env", "BRAIN_SYNC_TOKEN")
        token = os.environ.get(token_env, "") or cfg.get("token", "")
        if not token:
            raise SyncError(f"sync token missing: set ${token_env} (or sync.token)")
        return HttpBlobStore(url, token, timeout=float(cfg.get("timeout", 30)),
                             verify_tls=bool(cfg.get("verify_tls", True)))
    raise SyncError(f"unknown sync backend: {kind}")


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
@dataclass
class SyncReport:
    pushed: int = 0
    pulled: int = 0
    conflicts: int = 0
    refused: List[str] = field(default_factory=list)
    deleted: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    dry_run: bool = False
    ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"pushed": self.pushed, "pulled": self.pulled, "conflicts": self.conflicts,
                "deleted": self.deleted, "skipped": self.skipped, "errors": self.errors,
                "refused": self.refused, "dry_run": self.dry_run, "ms": self.ms}


class SyncEngine:
    def __init__(self, config: Config, memory: MemoryStore, vault: Vault,
                 store: Optional[BlobStore] = None):
        if not vault.is_unlocked():
            raise SyncError("unlock the vault before syncing")
        self.config = config
        self.memory = memory
        self.vault = vault
        self.store = store or build_store(config, vault)
        mk = vault.session.mk
        self.ko = fast_kdf(mk, b"pbx/sync/opaque-id/v1", 32)
        self.ke = fast_kdf(mk, b"pbx/sync/envelope/v1", 32)
        self.ki = fast_kdf(mk, b"pbx/sync/integrity/v1", 32)
        self.device_id = str(config.agent.get("device_id") or vault.device_id or "device")

    # ------------------------------------------------------------------ #
    def opaque_id(self, rid: str) -> str:
        return b64e(mac(self.ko, rid.encode()))

    def _wrap(self, rec: Record, oid: str) -> Dict[str, Any]:
        payload = rec.to_sync_dict()
        payload["origin"] = self.device_id
        body = b64e(encrypt(dumps(payload).encode(), self.ke, aad=oid.encode()))
        return {"oid": oid, "body": body,
                "hmac": b64e(mac(self.ki, f"{oid}|{rec.version}|{body}".encode())),
                "v": rec.version, "ts": rec.updated_at, "tomb": int(rec.deleted),
                "client": self.device_id}

    def _unwrap(self, blob: Dict[str, Any]) -> Optional[Record]:
        """Decrypt a remote blob. Raises SyncError if it is not authentically ours."""
        oid = str(blob.get("oid", ""))
        body = str(blob.get("body", ""))
        expect = b64e(mac(self.ki, f"{oid}|{blob.get('v', 0)}|{body}".encode()))
        if not expect == blob.get("hmac"):
            raise SyncError(f"integrity check failed for {oid[:12]}… (tampered or foreign vault)")
        try:
            raw = decrypt(b64d(body), self.ke, aad=oid.encode())
        except CryptoError as exc:
            raise SyncError(f"cannot decrypt {oid[:12]}…: {exc}") from exc
        data = json.loads(raw)
        rec = Record.from_sync_dict(data)
        rec.meta.setdefault("origin", data.get("origin") or blob.get("client", ""))
        return rec

    # ------------------------------------------------------------------ #
    @staticmethod
    def _wins(local: Record, remote: Record, local_device: str) -> bool:
        """Deterministic LWW: greater updated_at, tie broken by origin device id.

        Every device evaluates the same inputs and therefore reaches the same
        verdict, which is what makes the fleet converge instead of ping-ponging.
        """
        if remote.updated_at != local.updated_at:
            return local.updated_at > remote.updated_at
        remote_origin = str(remote.meta.get("origin", ""))
        return local_device >= remote_origin

    def _stash_conflict(self, loser: Record, canonical_id: str) -> str:
        """Persist the losing copy under a deterministic id derived from its own
        timestamp, so all devices produce the *same* conflict record."""
        cid = f"{canonical_id}-conflict-{loser.updated_at}"
        copy = Record.from_sync_dict(loser.to_sync_dict())
        copy.id = cid
        copy.tags = list(dict.fromkeys(copy.tags + ["conflict"]))
        if not copy.title.startswith("[conflict]"):
            copy.title = f"[conflict] {copy.title}"[:200]
        copy.meta = dict(copy.meta)
        copy.meta.update({"conflict_of": canonical_id, "conflict_reason": "concurrent-edit",
                          "resolved_by": "lww+preserve"})
        self.memory._write_full(copy)
        self.memory.conn.execute("UPDATE records SET sync_state='dirty' WHERE id=?", (cid,))
        self.memory.conn.commit()
        return cid

    # ------------------------------------------------------------------ #
    def status(self) -> Dict[str, Any]:
        pending = self.memory.pending_ids()
        cfg = dict(self.config.sync or {})
        row = self.memory.conn.execute(
            "SELECT COUNT(*) AS n, MAX(last_synced) AS last FROM sync_state").fetchone()
        conflicts = self.memory.conn.execute(
            "SELECT COUNT(*) FROM records WHERE tag_index LIKE '%conflict%'"
        ).fetchone()[0]
        backend = str(cfg.get("backend") or "local").strip().lower()
        target = cfg.get("url") or cfg.get("path") or str(self.config.sync_dir / "remote")
        # A user who sets sync.url and nothing else gets backend=local, and a
        # successful local push looks like a cloud backup. Say so, on every
        # status call and in the sync report (audit B12 / threat T43).
        warnings: List[str] = []
        if cfg.get("url") and backend in ("local", "dir", "folder", "nas"):
            warnings.append(
                f"sync.url is set ({cfg['url']}) but sync.backend is '{backend}', so "
                f"nothing is uploaded: this is a local file copy. Set "
                f"sync.backend=http to use the URL.")
        return {
            "backend": backend,
            "target": target,
            "remote": backend in ("http", "https"),
            "warnings": warnings,
            "device_id": self.device_id,
            "pending": len(pending),
            "pending_ids": pending[:10],
            "tracked": int(row["n"] or 0),
            "last_synced": int(row["last"] or 0),
            "unresolved_conflicts": int(conflicts),
            "e2ee": True,
            "note": "the cloud sees only ciphertext, opaque ids and versions",
        }

    def sync(self, dry_run: bool = False) -> Dict[str, Any]:
        started = now_ms()
        report = SyncReport(dry_run=dry_run)
        self.vault.audit("sync_start", {"dry_run": dry_run, "backend": self.status()["backend"],
                                        "device": self.device_id})

        # every local record (dirty or not) is a candidate: a remote change may
        # have to be pulled into a clean record, and a dirty record may have to
        # be pushed or reconciled.
        # Allowlist check before anything is read for upload. Vectors and
        # biometrics are recomputed/enrolled per device and must never appear in
        # an oplog: syncing an embedding syncs a derivable copy of the text, and
        # a voiceprint cannot be rotated after exposure.
        assert_syncable("records")
        rows = self.memory.conn.execute("SELECT id FROM records ORDER BY rowid").fetchall()
        local_ids = [r["id"] for r in rows]
        known_ids = set(local_ids)

        for rid in local_ids:
            local = self.memory.get(rid)
            if local is None:
                continue
            oid = self.opaque_id(rid)
            dirty = self.memory.dirty(rid)
            base = self.memory.sync_base(rid)
            base_remote = int(base.get("remote_version", 0))
            try:
                blob = None if dry_run else self.store.get(oid)
            except SyncError as exc:
                report.errors.append(f"get {rid[:12]}…: {exc}")
                continue
            remote_v = int((blob or {}).get("version", 0))

            # ---------------- 1. nothing remote yet -----------------------
            if blob is None:
                if dirty:
                    if dry_run:
                        report.pushed += 1
                        continue
                    try:
                        new_v = self.store.put(oid, self._wrap(local, oid), expect_version=0)
                        self.memory.mark_synced(rid, new_v)
                        report.pushed += 1
                        if local.deleted:
                            report.deleted += 1
                    except SyncError as exc:
                        report.errors.append(f"push {rid[:12]}…: {exc}")
                continue

            # ---------------- 2. authenticate the blob --------------------
            try:
                remote = self._unwrap(blob)
            except SyncError as exc:
                report.skipped += 1
                report.errors.append(f"skip {oid[:12]}…: {exc}")
                continue
            if remote is None:
                report.skipped += 1
                continue

            same_data = (remote.version == local.version
                         and remote.updated_at == local.updated_at
                         and remote.body == local.body)

            # ---------------- 3. local edits (push or reconcile) ----------
            if dirty:
                if same_data:
                    if not dry_run:
                        self.memory.mark_synced(rid, remote_v)
                    continue
                if remote_v <= base_remote:
                    # remote has not moved since our base -> a plain fast-forward push
                    if dry_run:
                        report.pushed += 1
                        continue
                    try:
                        new_v = self.store.put(oid, self._wrap(local, oid),
                                               expect_version=remote_v)
                        self.memory.mark_synced(rid, new_v)
                        report.pushed += 1
                        if local.deleted:
                            report.deleted += 1
                    except SyncError as exc:
                        if "conflict" in str(exc):
                            report.errors.append(
                                f"push race on {rid[:12]}…: {exc} (will reconcile next sync)")
                        else:
                            report.errors.append(f"push {rid[:12]}…: {exc}")
                    continue

                # both sides moved -> deterministic LWW, loser preserved
                if dry_run:
                    report.conflicts += 1
                    continue
                if self._wins(local, remote, self.device_id):
                    # our copy wins: preserve theirs as a conflict record and
                    # push ours. The conflict record is left dirty so phase 4
                    # publishes it and the other device converges on the same
                    # (deterministic) id.
                    self._stash_conflict(remote, rid)
                    new_v = self.store.put(oid, self._wrap(local, oid), expect_version=remote_v)
                    self.memory.mark_synced(rid, new_v)
                    report.conflicts += 1
                    report.pushed += 1
                else:
                    self._stash_conflict(local, rid)
                    self.memory.apply_remote(remote.to_sync_dict())
                    self.memory.mark_synced(rid, remote_v)
                    report.conflicts += 1
                    report.pulled += 1
                continue

            # ---------------- 4. local clean: pull if remote moved --------
            if remote_v <= base_remote:
                continue                      # already reconciled
            if dry_run:
                report.pulled += 1
                continue
            self.memory.apply_remote(remote.to_sync_dict())
            self.memory.mark_synced(rid, remote_v)
            report.pulled += 1
            if remote.deleted:
                report.deleted += 1

        # ---------------- 5. publish records created during this sync ----
        # (conflict copies, and anything the agent stored while we were
        # reconciling). They have no remote counterpart yet, so this is a plain
        # create with expect_version=0.
        for rid in self.memory.pending_ids():
            if rid in known_ids:
                continue
            rec = self.memory.get(rid)
            if rec is None:
                continue
            oid = self.opaque_id(rid)
            if dry_run:
                report.pushed += 1
                continue
            try:
                if self.store.get(oid) is not None:
                    continue          # someone else published it first; next sync pulls
                new_v = self.store.put(oid, self._wrap(rec, oid), expect_version=0)
                self.memory.mark_synced(rid, new_v)
                report.pushed += 1
            except SyncError as exc:
                report.errors.append(f"push-new {rid[:12]}…: {exc}")

        # ---------------- 6. remote records we do not have yet ------------
        # Ownership is proven cryptographically (HMAC + decryption under keys
        # derived from our MK), so a brand-new device can bootstrap from an
        # empty vault without knowing any ids in advance.
        try:
            keys = [] if dry_run else self.store.list_keys("")
        except SyncError as exc:
            report.errors.append(f"list: {exc}")
            keys = []
        known = set(self.memory.conn.execute("SELECT id FROM records").fetchall()
                    and [r["id"] for r in self.memory.conn.execute("SELECT id FROM records")])
        for key in keys:
            try:
                blob = self.store.get(key)
                if not blob:
                    continue
                try:
                    remote = self._unwrap(blob)
                except SyncError as exc:
                    # Two very different causes share this path: data belonging
                    # to a *different* vault (expected, e.g. a shared bucket) and
                    # data from OUR vault that failed integrity (tampering or
                    # corruption). We can tell them apart by trying to decrypt:
                    # a foreign blob fails the HMAC with our Ki, but so does a
                    # tampered one - so we only call it "refused" when the blob
                    # claims to be from a device we know.
                    report.skipped += 1
                    if blob.get("client") and blob.get("client") != self.device_id:
                        report.refused.append(f"{key[:12]}…: foreign vault ({exc})")
                    else:
                        report.errors.append(f"integrity {key[:12]}…: {exc}")
                    continue
                if remote is None or remote.id in known:
                    continue
                base = self.memory.sync_base(remote.id)
                if int(blob.get("version", 0)) <= int(base.get("remote_version", 0)):
                    continue
                if dry_run:
                    report.pulled += 1
                    continue
                self.memory.apply_remote(remote.to_sync_dict())
                self.memory.mark_synced(remote.id, int(blob.get("version", 0)))
                report.pulled += 1
                if remote.deleted:
                    report.deleted += 1
            except (SyncError, ValueError, KeyError, json.JSONDecodeError) as exc:
                report.errors.append(f"pull {key[:12]}…: {exc}")

        report.ms = now_ms() - started
        status = self.status()
        report_dict = report.to_dict()
        # The report must say where things actually went. A local push with a
        # sync.url configured is the exact case where a user believes they have an
        # off-machine backup (threat T43).
        report_dict["backend"] = status["backend"]
        report_dict["remote"] = status["remote"]
        report_dict["target"] = status["target"]
        if status["warnings"]:
            report_dict.setdefault("errors", [])
            report_dict["warnings"] = status["warnings"]
        self.vault.audit("sync_done", report_dict)
        return report_dict

    # ------------------------------------------------------------------ #
    def reset_cursors(self) -> int:
        n = self.memory.conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]
        self.memory.conn.execute("DELETE FROM sync_state")
        self.memory.conn.commit()
        return int(n)

    def purge_tombstones(self, older_than_ms: int = 30 * 86400_000) -> int:
        """Hard-delete tombstones older than the retention window."""
        cutoff = now_ms() - older_than_ms
        rows = self.memory.conn.execute(
            "SELECT id FROM records WHERE deleted=1 AND updated_at<?", (cutoff,)).fetchall()
        for r in rows:
            self.memory.delete(r["id"], hard=True)
            try:
                self.store.delete(self.opaque_id(r["id"]))
            except SyncError:
                pass
        return len(rows)
