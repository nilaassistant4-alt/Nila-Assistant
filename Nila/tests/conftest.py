"""Shared fixtures. Runs under pytest; the suite also works with `python -m unittest`."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSPHRASE = "test-passphrase-42"


@pytest.fixture()
def home(tmp_path):
    d = tmp_path / "home"
    d.mkdir()
    os.environ["BRAIN_HOME"] = str(d)
    yield d
    os.environ.pop("BRAIN_HOME", None)


@pytest.fixture()
def config(home):
    from brain.config import Config

    cfg = Config.load(home)
    cfg.fast_kdf = True           # scrypt n=2^12 so the suite stays fast
    cfg.agent["device_id"] = "test-device"
    return cfg


@pytest.fixture()
def vault(config):
    from brain.memory.vault import Vault

    v = Vault(config)
    v.initialize(PASSPHRASE, "tester", "test-device")
    yield v
    v.lock()


@pytest.fixture()
def memory(config, vault):
    from brain.memory.store import MemoryStore

    m = MemoryStore(config, vault.session.mk)
    yield m
    m.close()


@pytest.fixture()
def brain(config, vault, memory):
    from brain.agent.agent import Brain

    return Brain(config, vault, memory)


@pytest.fixture(scope="session")
def voice_wavs(tmp_path_factory):
    """Deterministic synthetic speakers, rendered once per test session."""
    from brain.util.audio import write_wav
    from scripts.synth_voice import synth_speaker

    out = tmp_path_factory.mktemp("voices")
    phrase = "seven three nine one four two"
    paths = {}
    for i in range(4):
        p = out / f"alice-{i}.wav"
        write_wav(p, synth_speaker("alice", phrase, seed=i).samples, 16000)
        paths[f"alice{i}"] = str(p)
    for name in ("bob", "carol", "dave"):
        p = out / f"{name}.wav"
        write_wav(p, synth_speaker(name, phrase, seed=7).samples, 16000)
        paths[name] = str(p)
    paths["silence"] = str(write_wav(out / "silence.wav", [0.0] * 48000, 16000))
    paths["phrase"] = phrase
    return paths


@pytest.fixture()
def gateway_factory(tmp_path):
    """Build an IntegrationGateway from a list of raw integration dicts."""
    import json

    def make(integrations):
        from brain.agent.integrations import IntegrationGateway
        from brain.config import Config
        n = len(list(tmp_path.glob("integ-*.json")))
        path = tmp_path / f"integ-{n}.json"
        path.write_text(json.dumps({"integrations": integrations}))
        cfg = Config.load(tmp_path)
        cfg.fast_kdf = True
        # signature is (config, memory=None, vault=None, path=None)
        return IntegrationGateway(cfg, path=path)

    return make


@pytest.fixture()
def brain_offline(config, vault, memory):
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    return Brain(config, vault, memory, provider=LocalBrain())
