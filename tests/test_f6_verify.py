"""F6 verify_fix: before/after checks on re-exported files."""
from __future__ import annotations

import shutil

import pytest
import soundfile as sf

from fixtures import make
from mixassist.analysis import get_analysis
from mixassist.errors import ToolError
from mixassist.tools.audio_tools import analyze_audio, verify_fix

SR = 48_000


@pytest.fixture(scope="session")
def audio_dir(fixtures_dir):
    return make.ensure_audio(fixtures_dir)


def wav(audio_dir, name):
    return audio_dir / f"{name}.wav"


def before_id(path, reference=None) -> str:
    args = {"path": str(path)}
    if reference is not None:
        args["reference_path"] = str(reference)
    return analyze_audio(args)["analysis_id"]


def verify(complaint, before, after) -> dict:
    return verify_fix({"complaint": complaint, "before_analysis_id": before, "after_path": str(after)})


def checks_by_name(out) -> dict:
    return {c["descriptor"]: c for c in out["checks"]}


def assert_contract(out):
    assert isinstance(out["passed"], bool) and isinstance(out["after_analysis_id"], str)
    assert out["checks"]
    for c in out["checks"]:
        assert {"descriptor", "before", "after", "rule", "passed"} <= set(c)
    assert out["passed"] == all(c["passed"] for c in out["checks"])


@pytest.fixture(scope="module")
def bright_before(audio_dir):
    return before_id(wav(audio_dir, "bright"), wav(audio_dir, "pink"))


# --- acceptance ------------------------------------------------------------------------------

def test_bright_to_bright_fixed_passes(audio_dir, bright_before):
    out = verify("it's too bright", bright_before, wav(audio_dir, "bright_fixed"))
    assert_contract(out)
    assert out["passed"] is True
    c = checks_by_name(out)
    assert set(c) == {"bright", "integrated_lufs", "true_peak_dbtp"}
    assert c["bright"]["after"] - c["bright"]["before"] <= -2.0  # ≥ 50 % of the proposed ≈ −5.4 dB cut
    assert get_analysis(out["after_analysis_id"])["integrated_lufs"] == pytest.approx(-16.3, abs=0.1)


def test_bright_twice_is_verify_failed(audio_dir, bright_before):
    with pytest.raises(ToolError) as e:
        verify("it's too bright", bright_before, wav(audio_dir, "bright"))
    assert e.value.code == "VERIFY_FAILED" and e.value.hint


def test_bright_twice_without_reference_and_via_a_copy_is_verify_failed(audio_dir, tmp_path):
    copy = tmp_path / "bright_reexport.wav"
    shutil.copy(wav(audio_dir, "bright"), copy)
    with pytest.raises(ToolError) as e:
        verify("too bright", before_id(wav(audio_dir, "bright")), copy)
    assert e.value.code == "VERIFY_FAILED"


def test_bright_fixed_loud_fails_the_loudness_check(audio_dir, bright_before):
    out = verify("it's too bright", bright_before, wav(audio_dir, "bright_fixed_loud"))
    assert_contract(out)
    assert out["passed"] is False
    c = checks_by_name(out)
    assert c["integrated_lufs"]["passed"] is False
    assert c["integrated_lufs"]["after"] - c["integrated_lufs"]["before"] == pytest.approx(3.0, abs=0.1)
    assert c["bright"]["passed"] is True and c["true_peak_dbtp"]["passed"] is True


def test_dispatcher_returns_verify_failed_error(audio_dir, bright_before):
    from mixassist.tools import call_tool

    out = call_tool("verify_fix", {"complaint": "too bright", "before_analysis_id": bright_before,
                                   "after_path": str(wav(audio_dir, "bright"))})
    assert out["error"]["code"] == "VERIFY_FAILED"
    ok = call_tool("verify_fix", {"complaint": "too bright", "before_analysis_id": bright_before,
                                  "after_path": str(wav(audio_dir, "bright_fixed"))})
    assert ok["passed"] is True


# --- other descriptors and directions ----------------------------------------------------------

def test_bright_fix_verified_against_a_before_without_reference(audio_dir):
    out = verify("too bright", before_id(wav(audio_dir, "bright")), wav(audio_dir, "bright_fixed"))
    assert out["passed"] is True


def test_wrong_direction_fails(audio_dir):
    out = verify("too bright", before_id(wav(audio_dir, "pink")), wav(audio_dir, "bright"))
    assert_contract(out)
    assert out["passed"] is False
    c = checks_by_name(out)["bright"]
    assert c["passed"] is False and c["after"] > c["before"]


@pytest.mark.parametrize("before,complaint", [("muddy", "sounds boxy"), ("boomy", "too boomy"), ("thin", "sounds thin")])
def test_removing_the_eq_curve_passes(audio_dir, before, complaint):
    out = verify(complaint, before_id(wav(audio_dir, before)), wav(audio_dir, "pink"))
    assert_contract(out)
    assert out["passed"] is True, out["checks"]


def test_thin_fix_must_rise(audio_dir):
    out = verify("sounds thin", before_id(wav(audio_dir, "pink")), wav(audio_dir, "thin"))
    assert out["passed"] is False and checks_by_name(out)["thin"]["passed"] is False


def test_loud_fix_skips_the_loudness_check(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    hot = tmp_path / "hot.wav"
    trimmed = tmp_path / "trimmed.wav"
    sf.write(str(hot), pink * 10 ** (6.87 / 20), SR, subtype="FLOAT")      # ≈ +0.5 dBTP
    sf.write(str(trimmed), pink * 10 ** (3.87 / 20), SR, subtype="FLOAT")  # −3 dB
    out = verify("it's clipping", before_id(hot), trimmed)
    assert_contract(out)
    c = checks_by_name(out)
    assert "integrated_lufs" not in c and c["loud"]["passed"] and out["passed"] is True


def test_true_peak_rising_above_ceiling_fails(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    hot = tmp_path / "hot_after.wav"
    sf.write(str(hot), pink * 10 ** (6.0 / 20), SR, subtype="FLOAT")
    out = verify("it's muddy", before_id(wav(audio_dir, "muddy")), hot)
    assert checks_by_name(out)["true_peak_dbtp"]["passed"] is False and out["passed"] is False


def test_narrow_fix(audio_dir):
    out = verify("sounds mono", before_id(wav(audio_dir, "mono")), wav(audio_dir, "pink"))
    c = checks_by_name(out)["narrow"]
    assert c["before"] == pytest.approx(1.0, abs=0.01) and c["passed"] is True


# --- errors -----------------------------------------------------------------------------------

def test_unknown_complaint_needs_clarification(audio_dir, bright_before):
    with pytest.raises(ToolError) as e:
        verify("did it get better?", bright_before, wav(audio_dir, "bright_fixed"))
    assert e.value.code == "NEEDS_CLARIFICATION" and "bright" in e.value.candidates


def test_midi_complaint_is_unsupported(audio_dir, bright_before):
    with pytest.raises(ToolError) as e:
        verify("the notes are clashing", bright_before, wav(audio_dir, "bright_fixed"))
    assert e.value.code == "UNSUPPORTED"


def test_unknown_before_id_is_not_found(audio_dir):
    with pytest.raises(ToolError) as e:
        verify("too bright", "aaaaaaaaaaaa", wav(audio_dir, "bright_fixed"))
    assert e.value.code == "NOT_FOUND"


def test_missing_after_file_is_a_file_error(bright_before):
    with pytest.raises(ToolError) as e:
        verify("too bright", bright_before, "/no/such/after.wav")
    assert e.value.code == "FILE_ERROR"
