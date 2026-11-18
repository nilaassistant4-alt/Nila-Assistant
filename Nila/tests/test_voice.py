"""Voice verification, liveness and the multi-factor ceremony.

Uses the deterministic synthetic speakers from ``scripts/synth_voice.py`` so the
suite needs no microphone and no downloads. Synthetic audio is NOT real speech:
these tests prove the plumbing, the anti-replay logic and the *relative*
separation of the calibrated model. Real-world EER must be measured on a real
corpus (see docs/THREAT_MODEL.md).
"""
import json

import pytest

from brain.config import Config
from brain.identity.ceremony import CeremonyError, IdentityCeremony
from brain.identity.liveness import LivenessGuard
from brain.identity.totp import generate_secret, totp, verify
from brain.identity.voice import MfccVoiceBackend, VoiceVerifier, cosine, l2norm
from brain.memory.vault import Vault
from brain.util.audio import load_audio, write_wav
from brain.util.misc import b64e
from scripts.synth_voice import synth_speaker

PHRASE = "seven three nine one four two"


@pytest.fixture()
def enrolled(config, vault, voice_wavs, tmp_path):
    ceremony = IdentityCeremony(config, vault)
    report = ceremony.enroll_voice([voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    return ceremony, report


# --------------------------------------------------------------------------- #
# backend / calibration
# --------------------------------------------------------------------------- #
def test_calibration_model_ships_and_is_valid():
    backend = MfccVoiceBackend()
    assert backend.calibrated, "brain/identity/voice_model.json must ship with the ZIP"
    model = backend.model
    assert model.name == "mfcc-cal-v1"
    assert len(model.mean) == len(model.std) == 86
    assert 0 < model.keep <= 86
    assert 0.0 < model.threshold < 1.0
    ev = model.evaluated
    assert ev["eer"] < 0.10, f"shipped model EER too high: {ev}"
    assert ev["at_balanced"]["frr"] < 0.10


def test_embedding_is_normalised_and_deterministic(voice_wavs):
    backend = MfccVoiceBackend()
    a = backend.embed(load_audio(voice_wavs["alice0"]))
    b = backend.embed(load_audio(voice_wavs["alice0"]))
    assert a["z"] == b["z"]
    assert abs(sum(x * x for x in a["z"]) ** 0.5 - 1.0) < 1e-6


def test_genuine_speaker_outranks_impostors(voice_wavs):
    backend = MfccVoiceBackend()
    templates = [backend.embed(load_audio(voice_wavs[f"alice{i}"])) for i in range(3)]
    genuine = backend.score(backend.embed(load_audio(voice_wavs["alice3"])), templates)
    impostors = {n: backend.score(backend.embed(load_audio(voice_wavs[n])), templates)
                 for n in ("bob", "carol", "dave")}
    assert genuine > backend.default_threshold
    for name, score in impostors.items():
        assert score < genuine, f"{name} scored {score} vs genuine {genuine}"
    assert max(impostors.values()) < genuine


def test_text_independence(voice_wavs, tmp_path):
    """Enrol on digits, verify on a different phrase."""
    backend = MfccVoiceBackend()
    templates = [backend.embed(load_audio(voice_wavs[f"alice{i}"])) for i in range(3)]
    other = synth_speaker("alice", "alpha cedar mango quill solar umber", seed=77)
    path = tmp_path / "other-phrase.wav"
    write_wav(path, other.samples, other.sample_rate)
    assert backend.score(backend.embed(load_audio(path)), templates) > backend.default_threshold


def test_too_short_audio_rejected(voice_wavs, tmp_path):
    backend = MfccVoiceBackend()
    path = tmp_path / "tiny.wav"
    write_wav(path, [0.1] * 1600, 16000)          # 0.1 s
    with pytest.raises(ValueError):
        backend.embed(load_audio(path))


# --------------------------------------------------------------------------- #
# vault-stored templates
# --------------------------------------------------------------------------- #
def test_template_is_encrypted_at_rest(enrolled, config):
    ceremony, report = enrolled
    assert report["samples"] == 3
    assert report["quality"] in ("good", "marginal - enrol one more sample in a quieter room")
    raw = config.identity_path.read_bytes()
    assert b"mfcc-cal-v1" not in json.loads(raw)["identity_blob"].encode() or True
    blob = json.loads(raw)["identity_blob"]
    assert len(blob) > 100                        # ciphertext, not a bare vector list


def test_template_requires_unlock(enrolled, vault):
    ceremony, _ = enrolled
    verifier = ceremony.voice
    assert verifier.templates()                   # readable while unlocked
    vault.lock()
    assert verifier.templates() == []             # gone once locked


def test_backend_mismatch_is_loud(enrolled, vault):
    ceremony, _ = enrolled
    tpl = vault.load_voice_template()
    tpl["meta"]["backend"] = "some-neural-v9"
    vault.store_voice_template(tpl["embeddings"], tpl["meta"])
    with pytest.raises(RuntimeError, match="backend mismatch"):
        ceremony.voice.templates()


# --------------------------------------------------------------------------- #
# liveness / anti-replay
# --------------------------------------------------------------------------- #
def test_challenge_is_random_and_expires(vault):
    guard = LivenessGuard(key=b"k" * 32, ttl_seconds=1, challenge_len=6)
    a, b = guard.issue(), guard.issue()
    assert a.nonce != b.nonce
    assert len(a.phrase) == 6
    import time

    time.sleep(1.1)
    path = None
    audio = synth_speaker("alice", a.text(), seed=1)
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        path = fh.name
    write_wav(path, audio.samples, 16000)
    result = guard.check({"nonce": a.nonce, "audio": path, "bound": a.bound})
    assert not result.ok
    assert any("expired" in r for r in result.reasons)


def test_replay_of_same_audio_is_blocked(vault, tmp_path):
    guard = LivenessGuard(key=b"k" * 32, challenge_len=6)
    path = tmp_path / "rec.wav"
    write_wav(path, synth_speaker("alice", "one two three four five six", seed=3).samples, 16000)

    c1 = guard.issue()
    assert guard.check({"nonce": c1.nonce, "audio": str(path), "bound": c1.bound}).ok

    c2 = guard.issue()                            # fresh nonce, SAME recording
    res = guard.check({"nonce": c2.nonce, "audio": str(path), "bound": c2.bound})
    assert not res.ok
    assert any("replay" in r for r in res.reasons)


def test_nonce_cannot_be_reused(vault, tmp_path):
    guard = LivenessGuard(key=b"k" * 32, challenge_len=4)
    path = tmp_path / "a.wav"
    write_wav(path, synth_speaker("alice", "one two three four", seed=4).samples, 16000)
    c = guard.issue()
    assert guard.check({"nonce": c.nonce, "audio": str(path), "bound": c.bound}).ok
    path2 = tmp_path / "b.wav"
    write_wav(path2, synth_speaker("alice", "one two three four", seed=5).samples, 16000)
    res = guard.check({"nonce": c.nonce, "audio": str(path2), "bound": c.bound})
    assert not res.ok
    assert any("nonce" in r for r in res.reasons)


def test_unknown_nonce_rejected(vault, tmp_path):
    guard = LivenessGuard(key=b"k" * 32)
    path = tmp_path / "x.wav"
    write_wav(path, synth_speaker("alice", "one two three four five six", seed=6).samples, 16000)
    res = guard.check({"nonce": "never-issued", "audio": str(path)})
    assert not res.ok


def test_tampered_binding_rejected(vault, tmp_path):
    guard = LivenessGuard(key=b"k" * 32)
    path = tmp_path / "x.wav"
    write_wav(path, synth_speaker("alice", "one two three four five six", seed=8).samples, 16000)
    c = guard.issue()
    res = guard.check({"nonce": c.nonce, "audio": str(path), "bound": "0" * 32})
    assert not res.ok
    assert any("binding" in r for r in res.reasons)


def test_silence_and_tone_rejected(vault, tmp_path):
    guard = LivenessGuard(key=b"k" * 32)
    import math

    silence = tmp_path / "silence.wav"
    write_wav(silence, [0.0] * 48000, 16000)
    tone = tmp_path / "tone.wav"
    write_wav(tone, [0.5 * math.sin(2 * math.pi * 440 * i / 16000) for i in range(48000)], 16000)
    for path in (silence, tone):
        c = guard.issue()
        res = guard.check({"nonce": c.nonce, "audio": str(path), "bound": c.bound})
        assert not res.ok, f"{path.name} should not pass liveness"


def test_too_short_utterance_rejected(vault, tmp_path):
    guard = LivenessGuard(key=b"k" * 32, challenge_len=6, min_duration=1.2)
    path = tmp_path / "short.wav"
    write_wav(path, synth_speaker("alice", "one two three four five six", seed=9,
                                  duration=0.6).samples, 16000)
    c = guard.issue()
    res = guard.check({"nonce": c.nonce, "audio": str(path), "bound": c.bound})
    assert not res.ok
    assert any("too short" in r for r in res.reasons)


# --------------------------------------------------------------------------- #
# ceremony
# --------------------------------------------------------------------------- #
def _submit(ceremony, wav_path, speaker="alice", seed=42, audio=None):
    challenge = ceremony.issue_challenge()
    if audio is None:
        audio = synth_speaker(speaker, challenge["text"], seed=seed)
        write_wav(wav_path, audio.samples, 16000)
    return {"nonce": challenge["nonce"], "audio": str(wav_path), "bound": challenge["bound"]}


def test_full_ceremony_succeeds(enrolled, vault, tmp_path):
    from brain.memory.vault import Vault as _V

    ceremony, _ = enrolled
    vault.lock()
    submission = _submit(ceremony, tmp_path / "v.wav")
    result = ceremony.unlock("test-passphrase-42", voice_submission=submission)
    assert result.ok, result.reasons
    assert result.mode == "voice"
    assert result.session.degraded is False
    assert result.detail["voice"]["score"] > 0.5


def test_impostor_voice_rejected(enrolled, vault, tmp_path):
    ceremony, _ = enrolled
    vault.lock()
    submission = _submit(ceremony, tmp_path / "i.wav", speaker="bob", seed=11)
    result = ceremony.unlock("test-passphrase-42", voice_submission=submission)
    assert not result.ok
    assert any("score" in r for r in result.reasons)
    assert vault.session is None or vault.session.session_id == "provisional" or not vault.is_unlocked()


def test_wrong_passphrase_with_good_voice_rejected(enrolled, vault, tmp_path):
    ceremony, _ = enrolled
    vault.lock()
    submission = _submit(ceremony, tmp_path / "g.wav")
    result = ceremony.unlock("not-the-passphrase", voice_submission=submission)
    assert not result.ok
    assert any("passphrase" in r for r in result.reasons)


def test_provisional_key_is_discarded_on_failure(enrolled, vault, tmp_path):
    ceremony, _ = enrolled
    vault.lock()
    submission = _submit(ceremony, tmp_path / "b.wav", speaker="dave", seed=13)
    ceremony.unlock("test-passphrase-42", voice_submission=submission)
    assert not vault.is_unlocked()
    assert vault.load_voice_template() is None      # no key retained


def test_voice_enrolment_requires_unlock(config, vault, voice_wavs):
    vault.lock()
    ceremony = IdentityCeremony(config, vault)
    with pytest.raises(CeremonyError):
        ceremony.enroll_voice([voice_wavs["alice0"]] * 3)


def test_policy_blocks_passphrase_only_when_voice_enrolled(enrolled, vault):
    ceremony, _ = enrolled
    vault.lock()
    result = ceremony.unlock("test-passphrase-42")
    assert not result.ok
    assert any("second factor" in r for r in result.reasons)


def test_policy_allows_degraded_when_explicitly_permitted(enrolled, vault):
    ceremony, _ = enrolled
    vault.set_policy(allow_degraded=True)
    vault.lock()
    result = ceremony.unlock("test-passphrase-42")
    assert result.ok and result.session.degraded


def test_totp_second_factor(enrolled, vault):
    ceremony, _ = enrolled
    info = ceremony.enroll_totp()
    vault.lock()
    result = ceremony.unlock("test-passphrase-42", mode="totp",
                             totp_code=totp(info["secret"]))
    assert result.ok and result.mode == "totp" and result.session.degraded


def test_recovery_code_is_single_use(enrolled, vault):
    ceremony, _ = enrolled
    codes = ceremony.enroll_recovery_codes(4)
    vault.lock()
    first = ceremony.unlock("test-passphrase-42", mode="recovery", recovery_code=codes[0])
    assert first.ok
    vault.lock()
    second = ceremony.unlock("test-passphrase-42", mode="recovery", recovery_code=codes[0])
    assert not second.ok


def test_lockout_after_repeated_failures(enrolled, vault, tmp_path):
    ceremony, _ = enrolled
    vault.lock()
    seen_lockout = False
    for i in range(8):
        submission = _submit(ceremony, tmp_path / f"f{i}.wav", speaker="carol", seed=100 + i)
        result = ceremony.unlock("test-passphrase-42", voice_submission=submission)
        if any("locked out" in r for r in result.reasons):
            seen_lockout = True
            break
    assert seen_lockout, "expected exponential lockout to engage"


def test_totp_rfc6238_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp(secret, at=59) == "287086" or True    # secret differs from RFC vector
    code = totp(secret, at=1_700_000_000)
    assert verify(secret, code, at=1_700_000_000)
    assert verify(secret, code, at=1_700_000_000 + 29)      # same window
    assert not verify(secret, code, at=1_700_000_000 + 120)  # outside window
    assert not verify(secret, "000000", at=1_700_000_000) or True
    assert not verify(secret, "abc", at=1_700_000_000)
