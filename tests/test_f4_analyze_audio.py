"""F4 analyze_audio: §7 fixture expectations, output contract, cache, reference diff and limits."""
from __future__ import annotations

import os
import re
import shutil

import numpy as np
import pytest
import soundfile as sf

from fixtures import make
from mixassist import analysis
from mixassist.analysis.spectrum import BANDS
from mixassist.errors import ToolError
from mixassist.tools.audio_tools import analyze_audio

SR = 48_000
REQUIRED = ("analysis_id", "duration_s", "sample_rate", "integrated_lufs", "short_term_max_lufs", "lra_lu",
            "true_peak_dbtp", "sample_peak_dbfs", "crest_db", "correlation_mean", "correlation_p5", "side_mid_db",
            "centroid_hz", "bands_db", "third_octave_db")
IEC_THIRDS = [25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
              2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000]


@pytest.fixture(scope="session")
def audio_dir(fixtures_dir):
    return make.ensure_audio(fixtures_dir)


def run(path, reference=None) -> dict:
    args = {"path": str(path)}
    if reference is not None:
        args["reference_path"] = str(reference)
    return analyze_audio(args)


def wav(audio_dir, name):
    return audio_dir / f"{name}.wav"


def write(path, x, sr=SR, subtype="PCM_24"):
    sf.write(str(path), x, sr, subtype=subtype, format="WAV")
    return path


# --- §7 fixture expectations ------------------------------------------------------------------

@pytest.mark.parametrize("case,expected", sorted(make.EBU3341_LUFS.items()))
def test_ebu3341_integrated_loudness(audio_dir, case, expected):
    out = run(wav(audio_dir, f"ebu3341_{case:02d}"))
    assert out["integrated_lufs"] == pytest.approx(expected, abs=0.1)


@pytest.mark.parametrize("case,expected", sorted(make.EBU3341_TP.items()))
def test_ebu3341_true_peak(audio_dir, case, expected):
    tp = run(wav(audio_dir, f"ebu3341_{case:02d}"))["true_peak_dbtp"]
    assert expected - 0.4 <= tp <= expected + 0.2


def test_ebu3341_inter_sample_cases_peak_between_samples(audio_dir):
    for case in (16, 17, 18, 19, 20, 21, 22, 23):
        out = run(wav(audio_dir, f"ebu3341_{case:02d}"))
        assert out["true_peak_dbtp"] - out["sample_peak_dbfs"] > 0.5, case


@pytest.mark.parametrize("case,expected", sorted(make.EBU3342_LRA.items()))
def test_ebu3342_loudness_range(audio_dir, case, expected):
    assert run(wav(audio_dir, f"ebu3342_{case:02d}"))["lra_lu"] == pytest.approx(expected, abs=1.0)


def test_pink_third_octaves_flat_and_uncorrelated(audio_dir):
    out = run(wav(audio_dir, "pink"))
    levels = [b["db"] for b in out["third_octave_db"] if 63 <= b["hz"] <= 16000]
    assert len(levels) == 25
    assert max(abs(v - np.mean(levels)) for v in levels) <= 1.0
    assert abs(out["correlation_mean"]) < 0.05


def test_mono_and_antiphase_correlation(audio_dir):
    assert run(wav(audio_dir, "mono"))["correlation_mean"] == pytest.approx(1.0, abs=0.01)
    assert run(wav(audio_dir, "antiphase"))["correlation_mean"] == pytest.approx(-1.0, abs=0.01)


# --- output contract --------------------------------------------------------------------------

def test_output_has_every_required_field_in_spec_units(audio_dir):
    out = run(wav(audio_dir, "pink"))
    for key in REQUIRED:
        assert key in out, key
    assert re.fullmatch(r"[0-9a-f]{12}", out["analysis_id"])
    assert out["sample_rate"] == SR and out["duration_s"] == pytest.approx(30.0)
    assert list(out["bands_db"]) == ["20-60", "60-150", "150-500", "500-2k", "2k-5k", "5k-10k", "10k-20k"]
    assert [b["hz"] for b in out["third_octave_db"]] == IEC_THIRDS
    assert out["true_peak_dbtp"] >= out["sample_peak_dbfs"]
    assert out["short_term_max_lufs"] >= out["integrated_lufs"] - 0.5
    assert "reference" not in out


def test_band_powers_add_up_to_the_signal_power(audio_dir):
    out = run(wav(audio_dir, "pink"))
    total = sum(10 ** (v / 10) for v in out["bands_db"].values())
    assert 10 * np.log10(total) == pytest.approx(-20.0, abs=0.1)  # pink is −20 dBFS RMS, 20 Hz–20 kHz


def test_sine_crest_sample_peak_and_centroid(audio_dir):
    out = run(wav(audio_dir, "ebu3341_01"))
    assert out["sample_peak_dbfs"] == pytest.approx(-23.0, abs=0.01)
    assert out["crest_db"] == pytest.approx(3.01, abs=0.05)  # sample peak − RMS of a sine
    assert out["centroid_hz"] == pytest.approx(1000, abs=5)
    assert out["bands_db"]["500-2k"] == pytest.approx(-26.01, abs=0.05)


def test_side_mid(audio_dir):
    assert run(wav(audio_dir, "mono"))["side_mid_db"] == -120.0
    assert run(wav(audio_dir, "antiphase"))["side_mid_db"] == 120.0
    assert run(wav(audio_dir, "pink"))["side_mid_db"] == pytest.approx(0.0, abs=0.2)


def test_correlation_p5_is_a_low_percentile(audio_dir):
    out = run(wav(audio_dir, "pink"))
    assert out["correlation_p5"] < out["correlation_mean"]
    r = analysis.stereo.window_correlations(*sf.read(str(wav(audio_dir, "pink")))[0].T, SR)
    assert len(r) == 75  # 30 s / 400 ms windows


# --- cache --------------------------------------------------------------------------------------

def test_analysis_id_is_sha1_of_path_mtime_options_and_cached(audio_dir):
    path = wav(audio_dir, "pink")
    out = run(path)
    assert out["analysis_id"] == analysis.make_analysis_id(str(path), path.stat().st_mtime_ns, {})
    assert run(path)["analysis_id"] == out["analysis_id"]
    assert analysis.get_analysis(out["analysis_id"]) == out
    with_ref = run(path, wav(audio_dir, "bright"))
    assert with_ref["analysis_id"] != out["analysis_id"]


def test_new_mtime_gives_a_new_id(audio_dir, tmp_path):
    copy = tmp_path / "mix.wav"
    shutil.copy(wav(audio_dir, "ebu3341_15"), copy)
    first = run(copy)["analysis_id"]
    st = copy.stat()
    os.utime(copy, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert run(copy)["analysis_id"] != first


def test_get_analysis_unknown_id_is_not_found():
    with pytest.raises(ToolError) as e:
        analysis.get_analysis("000000000000")
    assert e.value.code == "NOT_FOUND"


def test_get_analysis_returns_a_copy(audio_dir):
    out = run(wav(audio_dir, "pink"))
    got = analysis.get_analysis(out["analysis_id"])
    got["bands_db"]["20-60"] = 99
    assert analysis.get_analysis(out["analysis_id"])["bands_db"]["20-60"] != 99


# --- reference ----------------------------------------------------------------------------------

def test_reference_is_loudness_matched_before_band_diff(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    louder = write(tmp_path / "pink_plus6.wav", pink * 2.0)  # +6.02 dB, same spectrum
    out = run(louder, wav(audio_dir, "pink"))
    ref = out["reference"]
    assert ref["analysis_id"] == run(wav(audio_dir, "pink"))["analysis_id"]
    assert ref["lufs_diff"] == pytest.approx(6.02, abs=0.05)
    assert list(ref["band_diff_db"]) == [name for name, _, _ in BANDS]
    for name, diff in ref["band_diff_db"].items():
        assert diff == pytest.approx(0.0, abs=0.05), name


def test_bright_vs_pink_reference_shows_the_shelf(audio_dir):
    ref = run(wav(audio_dir, "bright"), wav(audio_dir, "pink"))["reference"]
    assert ref["lufs_diff"] == pytest.approx(0.0, abs=0.1)
    d = ref["band_diff_db"]
    assert d["10k-20k"] - d["500-2k"] == pytest.approx(6.0, abs=0.3)
    assert d["5k-10k"] - d["500-2k"] > 4.0
    assert abs(d["150-500"] - d["500-2k"]) < 0.1


# --- limits and errors ---------------------------------------------------------------------------

def test_under_3_seconds_has_no_short_term(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    out = run(write(tmp_path / "short.wav", pink[: 2 * SR]))
    assert out["short_term_max_lufs"] is None and out["lra_lu"] is None
    assert out["integrated_lufs"] is not None and out["duration_s"] == pytest.approx(2.0)


def test_over_15_minutes_is_out_of_range(tmp_path):
    path = write(tmp_path / "long.wav", np.zeros(901 * 1000, dtype=np.int16), sr=1000, subtype="PCM_16")
    with pytest.raises(ToolError) as e:
        run(path)
    assert e.value.code == "OUT_OF_RANGE"


def test_under_one_gating_block_is_out_of_range(tmp_path):
    with pytest.raises(ToolError) as e:
        run(write(tmp_path / "blip.wav", np.zeros((SR // 10, 2))))
    assert e.value.code == "OUT_OF_RANGE"


def test_mono_file_has_null_correlation(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    out = run(write(tmp_path / "one_channel.wav", pink[: 5 * SR, 0]))
    assert out["correlation_mean"] is None and out["correlation_p5"] is None and out["side_mid_db"] is None
    assert out["integrated_lufs"] is not None


def test_silence_has_null_loudness(tmp_path):
    out = run(write(tmp_path / "silence.wav", np.zeros((4 * SR, 2))))
    assert out["integrated_lufs"] is None and out["correlation_mean"] is None


def test_more_than_two_channels_is_unsupported(tmp_path):
    with pytest.raises(ToolError) as e:
        run(write(tmp_path / "surround.wav", np.zeros((SR, 6))))
    assert e.value.code == "UNSUPPORTED"


@pytest.mark.parametrize("bad", ["relative/mix.wav", "/no/such/file.wav", ""])
def test_bad_paths_are_file_errors(bad):
    with pytest.raises(ToolError) as e:
        analyze_audio({"path": bad})
    assert e.value.code == "FILE_ERROR"


def test_unreadable_file_is_a_file_error(tmp_path):
    junk = tmp_path / "notes.wav"
    junk.write_text("not audio")
    with pytest.raises(ToolError) as e:
        run(junk)
    assert e.value.code == "FILE_ERROR"


def test_missing_reference_is_a_file_error(audio_dir):
    with pytest.raises(ToolError) as e:
        run(wav(audio_dir, "pink"), "/no/such/ref.wav")
    assert e.value.code == "FILE_ERROR"


def test_dispatcher_returns_analysis_and_errors(audio_dir):
    from mixassist.tools import call_tool

    out = call_tool("analyze_audio", {"path": str(wav(audio_dir, "pink"))})
    assert "error" not in out and out["integrated_lufs"] is not None
    err = call_tool("analyze_audio", {"path": "/no/such/file.wav"})
    assert err["error"]["code"] == "FILE_ERROR"
