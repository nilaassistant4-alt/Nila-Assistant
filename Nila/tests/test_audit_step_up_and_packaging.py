"""Regression tests for audit findings F7, F8, F9 and F10.

F7: ``Session.step_up_ok`` was read by the tool registry and the classifier but
    nothing ever set it, so ``export_backup`` refused in every normal flow - a
    dead feature rather than a safe one. It is now set by ``Ceremony.step_up``
    and ``brain unlock --step-up``, and consumed by one high-risk call.
F8: ``run_sync`` and ``check_updates`` declared no capability, so the central
    gate had nothing to enforce for two network tools.
F9: ``packages = ["brain"]`` shipped 4 modules and broke `nila`/`brain`.
F10: build output from a wheel build rode into the distributable ZIP.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _ctx(session):
    from brain.agent.tools import ToolContext
    from brain.security.permission import Decision

    class Broker:
        def peek(self, capability, scope=""):
            return Decision(allowed=True, capability=capability, scope=scope, reason="test")

        def request(self, capability, scope="*", **kw):
            return self.peek(capability, scope)

    class Vault:
        def is_unlocked(self):
            return True

        def audit(self, *a, **k):
            pass

    class Memory:
        def export_encrypted_backup(self):
            return b"ciphertext-only"

    return ToolContext(memory=Memory(), vault=Vault(), config=None, session=session,
                       audit=lambda *a, **k: None, permissions=Broker())


def _session():
    from brain.memory.vault import Session
    return Session(mk=b"\x11" * 32, session_id="audit", started_at=0, expires_at=10 ** 13)


# --------------------------------------------------------------------------- #
# F7 — step-up
# --------------------------------------------------------------------------- #
def test_session_has_a_step_up_flag_that_defaults_off():
    assert _session().step_up_ok is False


def test_high_risk_tool_refuses_without_step_up(tmp_path):
    from brain.agent.tools import ToolError, build_default_registry

    reg = build_default_registry()
    with pytest.raises(ToolError) as info:
        reg.call("export_backup", {"path": str(tmp_path / "b.enc")}, _ctx(_session()))
    assert "step-up" in str(info.value)
    assert not (tmp_path / "b.enc").exists()


def test_step_up_authorises_exactly_one_call(tmp_path):
    from brain.agent.tools import ToolError, build_default_registry

    reg = build_default_registry()
    session = _session()
    session.step_up_ok = True
    result = reg.call("export_backup", {"path": str(tmp_path / "b.enc")}, _ctx(session))
    assert result["ok"] is True
    assert session.step_up_ok is False, "the step-up flag must be consumed, not standing"
    with pytest.raises(ToolError):
        reg.call("export_backup", {"path": str(tmp_path / "b2.enc")}, _ctx(session))


def test_a_capability_refusal_does_not_burn_the_step_up(tmp_path):
    """A grant must be spent by the call, not by a refusal on its way there."""
    from brain.agent.tools import build_default_registry
    from brain.security.permission import Decision

    class Broker:
        def __init__(self, allow):
            self.allow = allow

        def peek(self, capability, scope=""):
            return Decision(allowed=self.allow, capability=capability, scope=scope, reason="t")

        def request(self, capability, scope="*", **kw):
            return self.peek(capability, scope)

    class Vault:
        def is_unlocked(self):
            return True

        def audit(self, *a, **k):
            pass

    class Memory:
        def export_encrypted_backup(self):
            return b"ct"

    from brain.agent.tools import ToolContext

    session = _session()
    session.step_up_ok = True
    ctx = ToolContext(memory=Memory(), vault=Vault(), config=None, session=session,
                      audit=lambda *a, **k: None, permissions=Broker(False))
    reg = build_default_registry()
    refusal = reg.call("export_backup", {"path": str(tmp_path / "b.enc")}, ctx)
    assert refusal.get("needs_affirmation") is True
    assert session.step_up_ok is True, "a capability refusal consumed the step-up"
    ctx.permissions = Broker(True)
    assert reg.call("export_backup", {"path": str(tmp_path / "b.enc")}, ctx)["ok"] is True
    assert session.step_up_ok is False


def test_ceremony_step_up_marks_the_session(tmp_path, monkeypatch):
    """The setter that never existed: Ceremony.step_up must flag the session."""
    from brain.identity import ceremony as ceremony_mod

    class Vault:
        session = None

        def audit(self, *a, **k):
            pass

    class Result:
        ok = True

        def __init__(self, session):
            self.session = session
            self.mode = "auto"
            self.detail = {}

    vault = Vault()
    session = _session()
    vault.session = session
    cer = ceremony_mod.IdentityCeremony.__new__(ceremony_mod.IdentityCeremony)
    cer.vault = vault
    monkeypatch.setattr(cer, "unlock",
                        lambda *a, **k: Result(session))
    out = cer.step_up("passphrase")
    assert out.ok and session.step_up_ok is True
    assert session.step_up_at > 0


# --------------------------------------------------------------------------- #
# F8 — declared capabilities
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["run_sync", "check_updates"])
def test_network_tools_declare_a_capability(name):
    from brain.agent.tools import build_default_registry
    from brain.security.permission import CAP_NET

    spec = build_default_registry().get(name)
    assert spec is not None
    assert spec.capability == CAP_NET, f"{name} must declare net.host"


def test_network_tool_without_a_broker_fails_closed():
    from brain.agent.tools import ToolError, build_default_registry, ToolContext

    reg = build_default_registry()

    class Unlocked:
        def is_unlocked(self):
            return True

        def audit(self, *a, **k):
            pass

    ctx = ToolContext(memory=None, vault=Unlocked(), config=None, session=_session(),
                      audit=lambda *a, **k: None, permissions=None)
    with pytest.raises(ToolError) as info:
        reg.call("check_updates", {}, ctx)
    assert "permission broker" in str(info.value)


# --------------------------------------------------------------------------- #
# F9 — the wheel must be importable
# --------------------------------------------------------------------------- #
def test_pyproject_declares_every_subpackage():
    text = (ROOT / "pyproject.toml").read_text()
    assert "[tool.setuptools.packages.find]" in text
    assert 'include = ["brain", "brain.*"]' in text
    active = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    assert 'packages = ["brain"]' not in active


def test_every_brain_subpackage_has_an_init():
    missing = [str(p.relative_to(ROOT)) for p in (ROOT / "brain").iterdir()
               if p.is_dir() and p.name != "__pycache__"
               and not (p / "__init__.py").exists()]
    assert not missing, f"subpackages without __init__.py: {missing}"


# --------------------------------------------------------------------------- #
# F10 — the builder must exclude generated output
# --------------------------------------------------------------------------- #
def test_builder_excludes_build_and_egg_info():
    text = (ROOT / "scripts" / "build_package.py").read_text()
    assert '"build", "dist", "*.egg-info"' in text or '"build"' in text
    assert "EXCLUDED_DIR_GLOBS" in text


def test_built_zip_has_no_build_artifacts(tmp_path):
    """Build with `build/` and an egg-info dir present: they must not ride along."""
    fake_build = ROOT / "build" / "lib" / "brain"
    fake_egg = ROOT / "personal_brain.egg-info"
    created = []
    try:
        fake_build.mkdir(parents=True, exist_ok=True)
        (fake_build / "__init__.py").write_text("# generated\n")
        created.append(ROOT / "build")
        fake_egg.mkdir(exist_ok=True)
        (fake_egg / "PKG-INFO").write_text("generated\n")
        created.append(fake_egg)

        out = tmp_path / "out"
        result = subprocess.run(
            [sys.executable, "scripts/build_package.py", "audit-zip", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        with zipfile.ZipFile(out / "audit-zip.zip") as zf:
            names = zf.namelist()
        bad = [n for n in names if "/build/" in n or "egg-info/" in n]
        assert not bad, f"build artifacts shipped in the ZIP: {bad[:5]}"
    finally:
        for path in reversed(created):
            import shutil

            shutil.rmtree(path, ignore_errors=True)
