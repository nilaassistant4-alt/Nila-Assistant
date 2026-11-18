"""Dashboard support for `brain serve`: same-origin static assets, hardware metrics,
and allowlisted file *names*. There is deliberately no read-content endpoint."""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

STATIC = Path(__file__).parent / "static"
ASSETS = {"/": ("dashboard.html", "text/html; charset=utf-8"),
          "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
          "/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8")}
# No inline script/style anywhere, so nothing but same-origin files may run.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
# Phase 4 (browser voice unlock) must change microphone=() to microphone=(self).
PERMISSIONS = "microphone=(), camera=(), geolocation=(), payment=()"
DEFAULT_ROOTS = ("~/Documents", "~/Downloads", "~/Pictures")
MAX_ENTRIES = 300


def asset(path: str) -> Optional[Tuple[bytes, str]]:
    spec = ASSETS.get(path)
    if not spec:
        return None
    f = STATIC / spec[0]
    return (f.read_bytes(), spec[1]) if f.is_file() else None


def _configured(config) -> List[str]:
    raw = config.agent.get("dashboard_roots") or DEFAULT_ROOTS
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(r).strip() for r in raw if str(r).strip()]


def root_status(config) -> List[Dict[str, Any]]:
    """Every configured folder and whether it exists, so a missing one is explained, not silent."""
    out = []
    for r in _configured(config):
        try:
            ok = Path(r).expanduser().resolve().is_dir()
        except OSError:
            ok = False
        out.append({"configured": r, "exists": ok})
    return out


def roots(config) -> List[Path]:
    """Allowlisted folders (config: agent.dashboard_roots, comma-separated or list)."""
    out: List[Path] = []
    for r in _configured(config):
        try:
            p = Path(r).expanduser().resolve()
        except OSError:
            continue
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def list_names(allowed: List[Path], requested: str,
               forbidden: Callable[[Path], Any]) -> Dict[str, Any]:
    """Names only. `forbidden` is the project's never-read check, applied to the
    folder AND to every entry (symlinks are resolved first)."""
    if not requested:
        return {"path": "", "parent": None,
                "entries": [{"name": str(r), "dir": True, "path": str(r)} for r in allowed]}
    p = Path(requested).expanduser().resolve()
    if not any(p == r or r in p.parents for r in allowed):
        raise PermissionError("outside the allowlisted folders")
    if forbidden(p):
        raise PermissionError("never-read path")
    if not p.is_dir():
        raise PermissionError("not a folder")
    entries = []
    for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if e.name.startswith(".") or forbidden(e):
            continue
        try:
            e.resolve().relative_to(next(r for r in allowed if r == p or r in p.parents))
        except (ValueError, OSError, StopIteration):
            continue                      # symlink pointing outside the allowlist
        entries.append({"name": e.name, "dir": e.is_dir(), "path": str(e) if e.is_dir() else ""})
        if len(entries) >= MAX_ENTRIES:
            break
    return {"path": str(p), "parent": "" if p in allowed else str(p.parent), "entries": entries}


# ----------------------------------------------------------------------- metrics
def _win() -> Dict[str, Any]:                    # stdlib only; UNTESTED here (no Windows)
    import ctypes
    from ctypes import c_ulong, c_ulonglong, c_ubyte, c_uint32, byref, sizeof

    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", c_ulong), ("dwMemoryLoad", c_ulong), ("a", c_ulonglong),
                    ("b", c_ulonglong), ("c", c_ulonglong), ("d", c_ulonglong),
                    ("e", c_ulonglong), ("f", c_ulonglong), ("g", c_ulonglong)]

    class PWR(ctypes.Structure):
        _fields_ = [("ac", c_ubyte), ("flag", c_ubyte), ("pct", c_ubyte), ("sys", c_ubyte),
                    ("life", c_uint32), ("full", c_uint32)]

    out: Dict[str, Any] = {}
    m = MEM(); m.dwLength = sizeof(MEM)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(byref(m)):
        out["mem_pct"] = int(m.dwMemoryLoad)
    p = PWR()
    if ctypes.windll.kernel32.GetSystemPowerStatus(byref(p)) and p.pct != 255:
        out.update(battery_pct=int(p.pct), plugged=p.ac == 1)
    return out


def _linux() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        out["mem_pct"] = round(100 * (1 - mi["MemAvailable"] / mi["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        pass
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            out["battery_pct"] = int((bat / "capacity").read_text())
            out["plugged"] = (bat / "status").read_text().strip() in ("Charging", "Full")
            break
        except (OSError, ValueError):
            continue
    return out


def metrics() -> Dict[str, Any]:
    """psutil if installed; otherwise stdlib (Linux/Windows). macOS without psutil gets
    disk and load only."""
    m: Dict[str, Any] = {"platform": platform.system()}
    try:
        m["load1"], m["cpus"] = round(os.getloadavg()[0], 2), os.cpu_count()
    except (AttributeError, OSError):
        m["cpus"] = os.cpu_count()
    try:
        import psutil
        m["cpu_pct"], m["mem_pct"] = psutil.cpu_percent(), psutil.virtual_memory().percent
        b = psutil.sensors_battery()
        if b:
            m.update(battery_pct=round(b.percent), plugged=b.power_plugged)
    except ImportError:
        sysname = platform.system()
        m.update(_win() if sysname == "Windows" else _linux() if sysname == "Linux" else {})
    try:
        du = shutil.disk_usage(Path.home())
        m["disk_pct"] = round(100 * du.used / du.total, 1)
    except OSError:
        pass
    return m
