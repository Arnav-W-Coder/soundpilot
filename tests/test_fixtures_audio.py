"""fixtures/make.py: the §7 audio files exist with the stated format, levels and determinism."""
from __future__ import annotations

import numpy as np
import pyloudnorm
import pytest
import soundfile as sf

from fixtures import make


@pytest.fixture(scope="session")
def audio_dir(fixtures_dir):
    return make.ensure_audio(fixtures_dir)


def _read(audio_dir, name):
    return sf.read(str(audio_dir / f"{name}.wav"), dtype="float64", always_2d=True)


def test_every_expected_file_is_48k_24bit_stereo_wav(audio_dir):
    names = make.expected_files()
    assert len(names) == 27
    for name in names:
        info = sf.info(str(audio_dir / name))
        assert (info.format, info.subtype, info.samplerate, info.channels) == ("WAV", "PCM_24", 48_000, 2), name


def test_durations(audio_dir):
    assert sf.info(str(audio_dir / "pink.wav")).duration == pytest.approx(30.0)
    assert sf.info(str(audio_dir / "ebu3341_05.wav")).duration == pytest.approx(60.1)
    assert sf.info(str(audio_dir / "ebu3342_04.wav")).duration == pytest.approx(100.0)


def test_pink_is_minus_20_dbfs_rms_with_independent_channels(audio_dir):
    x, _ = _read(audio_dir, "pink")
    rms_db = 20 * np.log10(np.sqrt(np.mean(x ** 2, axis=0)))
    assert rms_db == pytest.approx([-20.0, -20.0], abs=0.05)
    assert abs(np.corrcoef(x[:, 0], x[:, 1])[0, 1]) < 0.02


def test_generation_is_deterministic_with_seed_1(audio_dir):
    a = make.PinkSpectrum().render()
    b = make.PinkSpectrum().render()
    np.testing.assert_array_equal(a, b)
    x, _ = _read(audio_dir, "pink")
    assert np.max(np.abs(x - a)) < 2 ** -22  # 24-bit quantization only
    assert not np.allclose(make.PinkSpectrum(seed=2).render()[:1000], a[:1000])


def test_eq_variants_are_loudness_matched_to_pink_and_loud_is_plus_3(audio_dir):
    meter = pyloudnorm.Meter(48_000)
    lufs = {n: meter.integrated_loudness(_read(audio_dir, n)[0])
            for n in ("pink", "bright", "bright_fixed", "bright_fixed_loud", "muddy", "boomy", "thin")}
    for n in ("bright", "bright_fixed", "muddy", "boomy", "thin"):
        assert lufs[n] == pytest.approx(lufs["pink"], abs=0.05), n
    assert lufs["bright_fixed_loud"] - lufs["bright_fixed"] == pytest.approx(3.0, abs=0.05)


def test_mono_and_antiphase_are_built_from_pink_left(audio_dir):
    pink, _ = _read(audio_dir, "pink")
    mono, _ = _read(audio_dir, "mono")
    anti, _ = _read(audio_dir, "antiphase")
    np.testing.assert_array_equal(mono[:, 0], mono[:, 1])
    np.testing.assert_allclose(anti[:, 0], -anti[:, 1], atol=2 ** -22)  # 24-bit rounding isn't symmetric
    np.testing.assert_allclose(mono[:, 0], pink[:, 0], atol=2 ** -22)


def test_no_fixture_clips(audio_dir):
    for name in make.expected_files():
        x, _ = sf.read(str(audio_dir / name), dtype="float64")
        assert np.max(np.abs(x)) < 1.0, name


def test_eq_curve_shapes():
    f = np.array([1000.0, 6000.0, 48_000.0])
    assert make.high_shelf(f, 6000, 6.0) == pytest.approx([0.0, 3.0, 6.0], abs=0.01)
    assert make.low_shelf(f, 6000, -6.0) == pytest.approx([-6.0, -3.0, 0.0], abs=0.01)
    assert make.bell(np.array([300.0, 300 * 2 ** 0.5, 3000.0]), 300, 6.0) == pytest.approx([6.0, 3.0, 0.0], abs=0.01)


def test_ensure_audio_rebuilds_missing_files(tmp_path):
    out = make.ensure_audio(tmp_path)
    assert sorted(p.name for p in out.glob("*.wav")) == sorted(make.expected_files())
    (out / "pink.wav").unlink()
    assert not make.audio_is_current(out)
    make.ensure_audio(tmp_path)
    assert (out / "pink.wav").exists() and make.audio_is_current(out)
    assert not list(out.glob(".*.tmp"))
