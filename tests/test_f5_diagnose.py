"""F5 diagnose_complaint: lexicon, measures vs reference / fitted line, clarification and proposals."""
from __future__ import annotations

import jsonschema
import numpy as np
import pytest
import soundfile as sf

from fixtures import make
from mixassist.complaints import load_lexicon
from mixassist.errors import ToolError
from mixassist.schemas import SCHEMAS
from mixassist.tools.audio_tools import analyze_audio, diagnose_complaint

SR = 48_000


@pytest.fixture(scope="session")
def audio_dir(fixtures_dir):
    return make.ensure_audio(fixtures_dir)


def aid(path, reference=None) -> str:
    args = {"path": str(path)}
    if reference is not None:
        args["reference_path"] = str(reference)
    return analyze_audio(args)["analysis_id"]


def diagnose(complaint, analysis_id, track=None) -> dict:
    args = {"complaint": complaint, "analysis_id": analysis_id}
    if track is not None:
        args["track"] = track
    return diagnose_complaint(args)


def wav(audio_dir, name):
    return audio_dir / f"{name}.wav"


def assert_contract(out: dict):
    assert isinstance(out["needs_clarification"], bool)
    for f in out["findings"]:
        assert {"descriptor", "value", "target", "delta", "unit"} <= set(f)
        assert f["delta"] == pytest.approx(f["value"] - f["target"], abs=0.02)
    for p in out["proposed_changes"]:
        assert p["tool"] == "fl_set_track" and isinstance(p["why"], str) and p["why"]
        jsonschema.validate(p["args"], SCHEMAS["fl_set_track"])
    if out["needs_clarification"]:
        assert out["proposed_changes"] == []
        assert isinstance(out["question"], str) and out["question"].endswith(("?", "."))
        assert 2 <= len(out["options"]) <= 4


# --- acceptance ------------------------------------------------------------------------------

def test_bright_vs_pink_too_bright(audio_dir):
    out = diagnose("it's too bright", aid(wav(audio_dir, "bright"), wav(audio_dir, "pink")))
    assert_contract(out)
    assert out["needs_clarification"] is False
    top = out["findings"][0]
    assert top["descriptor"] == "bright" and top["unit"] == "dB"
    assert top["delta"] == pytest.approx(6.0, abs=1.0)
    assert top["basis"] == "the reference"
    [p] = out["proposed_changes"]
    assert p["args"]["track"] == 0 and p["args"]["eq_band"] == "high" and p["args"]["eq_freq_hz"] == 8000
    assert -6.0 <= p["args"]["eq_gain_db"] <= -4.0


def test_pink_alone_sounds_bad_needs_clarification(audio_dir):
    out = diagnose("it sounds bad", aid(wav(audio_dir, "pink")))
    assert_contract(out)
    assert out["needs_clarification"] is True
    assert out["proposed_changes"] == []


def test_picking_a_measured_option_diagnoses_it(audio_dir):
    analysis = aid(wav(audio_dir, "bright"))
    first = diagnose("it sounds bad", analysis)
    assert first["needs_clarification"] is True
    highs = next(o for o in first["options"] if o.startswith("The highs ("))
    for complaint in (highs, f"it sounds bad. {highs}"):
        out = diagnose(complaint, analysis)
        assert_contract(out)
        assert out["needs_clarification"] is False
        assert out["findings"][0]["descriptor"] == "bright"
        [p] = out["proposed_changes"]
        assert p["args"]["eq_band"] == "high" and p["args"]["eq_gain_db"] < 0


def test_every_clarification_option_is_understood(audio_dir):
    for name in ("pink", "bright", "muddy"):
        analysis = aid(wav(audio_dir, name))
        for option in diagnose("it sounds bad", analysis)["options"]:
            out = diagnose(option, analysis)
            assert_contract(out)
            assert out.get("question") != "What sounds wrong? Pick the closest option.", option


def test_muddy_sounds_boxy(audio_dir):
    out = diagnose("sounds boxy", aid(wav(audio_dir, "muddy")))
    assert_contract(out)
    assert out["findings"][0]["descriptor"] == "muddy"
    assert out["findings"][0]["basis"] == "a line fitted to the spectrum"
    [p] = out["proposed_changes"]
    assert p["args"]["eq_band"] == "mid" and p["args"]["eq_freq_hz"] == 300
    assert -4.0 <= p["args"]["eq_gain_db"] <= -2.0


@pytest.mark.parametrize("name,complaint,band,lo,hi", [
    ("boomy", "the mix is too boomy", "low", -6.0, -2.0),
    ("thin", "it sounds thin", "low", 1.0, 4.0),
    ("muddy", "it's muddy", "mid", -4.0, -2.0),
    ("bright", "too bright", "high", -6.0, -2.0),
])
def test_top_finding_per_fixture(audio_dir, name, complaint, band, lo, hi):
    out = diagnose(complaint, aid(wav(audio_dir, name)))
    assert_contract(out)
    assert out["needs_clarification"] is False
    assert out["findings"][0]["descriptor"] == name and out["findings"][0]["triggered"]
    [p] = out["proposed_changes"]
    assert p["args"]["eq_band"] == band and lo <= p["args"]["eq_gain_db"] <= hi


def test_fitted_line_and_reference_agree_on_bright(audio_dir):
    with_ref = diagnose("too bright", aid(wav(audio_dir, "bright"), wav(audio_dir, "pink")))["findings"][0]
    alone = diagnose("too bright", aid(wav(audio_dir, "bright")))["findings"][0]
    assert alone["delta"] == pytest.approx(with_ref["delta"], abs=0.5)


def test_clean_fixture_does_not_fire_the_matched_rules(audio_dir):
    for complaint in ("too bright", "muddy", "boomy", "thin"):
        out = diagnose(complaint, aid(wav(audio_dir, "pink")))
        assert_contract(out)
        assert out["needs_clarification"] is False and out["proposed_changes"] == []
        assert abs(out["findings"][0]["delta"]) < 0.5
        assert out["notes"]


# --- clarification -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def muddy_and_bright(tmp_path_factory):
    pink = make.PinkSpectrum(seconds=10)
    x = pink.render(make.bell(pink.f, 300, 6.0) + make.high_shelf(pink.f, 6000, 6.0))
    path = tmp_path_factory.mktemp("f5") / "muddy_bright.wav"
    sf.write(str(path), x, SR, subtype="PCM_24")
    return path


def test_two_close_findings_ask_which(muddy_and_bright):
    out = diagnose("it's muddy and too bright", aid(muddy_and_bright))
    assert_contract(out)
    assert out["needs_clarification"] is True and out["proposed_changes"] == []
    top, second = out["findings"][:2]
    assert {top["descriptor"], second["descriptor"]} == {"muddy", "bright"}
    assert top["delta"] < 1.5 * second["delta"]
    assert any("5–20 kHz" in o for o in out["options"]) and any("150–500 Hz" in o for o in out["options"])
    assert out["question"].startswith("Is it ")


def test_one_matched_descriptor_firing_is_proposed_alone(audio_dir):
    out = diagnose("too bright and a bit muddy", aid(wav(audio_dir, "bright")))
    assert_contract(out)
    assert out["needs_clarification"] is False
    assert [f["descriptor"] for f in out["findings"]][0] == "bright"
    assert [p["args"]["eq_band"] for p in out["proposed_changes"]] == ["high"]


def test_width_needs_a_track(audio_dir, fake_fl):
    out = diagnose("it sounds too narrow", aid(wav(audio_dir, "mono")))
    assert_contract(out)
    assert out["needs_clarification"] is True and out["proposed_changes"] == []
    assert out["findings"][0]["descriptor"] == "narrow"
    assert "Kick" in out["options"]  # track names from the (fake) mixer


def test_width_with_track(audio_dir):
    out = diagnose("it sounds too narrow", aid(wav(audio_dir, "mono")), track="Pad")
    assert_contract(out)
    [p] = out["proposed_changes"]
    assert p["args"] == {"track": "Pad", "stereo_width_pct": 30}
    out = diagnose("it sounds phasey", aid(wav(audio_dir, "antiphase")), track=3)
    [p] = out["proposed_changes"]
    assert p["args"] == {"track": 3, "stereo_width_pct": -30}
    assert out["findings"][0]["value"] == pytest.approx(-1.0, abs=0.01)


def test_named_track_is_targeted(audio_dir):
    out = diagnose("too bright", aid(wav(audio_dir, "bright")), track="Hat Open")
    assert out["proposed_changes"][0]["args"]["track"] == "Hat Open"
    assert "Hat Open" in out["proposed_changes"][0]["why"]


# --- loud / quiet -------------------------------------------------------------------------------

def test_hot_mix_gets_a_volume_cut(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    hot = tmp_path / "hot.wav"
    sf.write(str(hot), pink * 10 ** (6.87 / 20), SR, subtype="FLOAT")  # true peak ≈ +0.5 dBTP
    out = diagnose("it's distorted and clipping", aid(hot))
    assert_contract(out)
    top = out["findings"][0]
    assert top["descriptor"] == "loud" and top["unit"] == "dBTP" and top["value"] == pytest.approx(0.5, abs=0.1)
    [p] = out["proposed_changes"]
    assert p["args"] == {"track": 0, "volume_change_db": -1.5}


def test_over_limited_mix_gets_guidance_only(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    squashed = tmp_path / "squashed.wav"
    sf.write(str(squashed), np.clip(pink * 10, -0.4, 0.4), SR, subtype="PCM_24")
    out = diagnose("sounds crunchy", aid(squashed))
    top = out["findings"][0]
    assert top["descriptor"] == "loud" and top["value"] < 6.0 and top["triggered"]
    assert out["proposed_changes"] == [] and any("limit" in n for n in out["notes"])


def test_quiet_vs_reference_is_guidance(audio_dir, tmp_path):
    pink, _ = sf.read(str(wav(audio_dir, "pink")))
    quiet = tmp_path / "quiet.wav"
    sf.write(str(quiet), pink * 10 ** (-8 / 20), SR, subtype="PCM_24")
    out = diagnose("it's quieter than other songs", aid(quiet, wav(audio_dir, "pink")))
    assert_contract(out)
    top = out["findings"][0]
    assert top["descriptor"] == "quiet" and top["delta"] == pytest.approx(-8.0, abs=0.1) and top["triggered"]
    assert out["proposed_changes"] == [] and out["notes"]
    alone = diagnose("too quiet", aid(quiet))
    assert alone["findings"] == [] and "reference" in alone["notes"][0]


# --- lexicon ------------------------------------------------------------------------------------

SPEC_PHRASES = {  # spec F5 table
    "bright": ["too bright", "brittle", "tinny", "piercing", "sizzly"],
    "harsh": ["harsh", "harsh hi-hats", "harsh cymbals", "ear-piercing"],
    "muddy": ["muddy", "boxy", "cloudy", "cluttered", "drowning out"],
    "boomy": ["boomy", "too much bass", "woofy"],
    "thin": ["thin", "weak", "no body", "no meat"],
    "loud": ["too loud", "distorted", "clipping", "crunchy"],
    "quiet": ["too quiet", "quieter than other songs"],
    "narrow": ["narrow", "mono"],
    "phasey": ["hollow", "phasey"],
}


@pytest.mark.parametrize("descriptor,phrase", [(d, p) for d, ps in SPEC_PHRASES.items() for p in ps])
def test_spec_phrases_match(descriptor, phrase):
    assert load_lexicon().match(f"my mix sounds {phrase} to me").descriptors == [descriptor]


@pytest.mark.parametrize("text,expected", [
    ("The mix sounds thin and brittle", ["thin", "bright"]),                     # [U1]
    ("the hi-hats and cymbals are really harsh", ["harsh"]),                    # [U1]
    ("everything is muddy, there's a boxy build-up", ["muddy"]),                 # [U2]
    ("all the sounds are drowning out one another", ["muddy"]),                  # [U5]
    ("it's not muddy, it's too bright", ["bright"]),
    ("No, it's too bright", ["bright"]),
    ("it isn't harsh at all", []),
    ("NOT LOUD ENOUGH!!", ["quiet"]),
    ("it’s too boomy", ["boomy"]),
    ("collapses in mono", ["phasey"]),
])
def test_matching(text, expected):
    assert load_lexicon().match(text).descriptors == expected


@pytest.mark.parametrize("text", ["sounds very out of key", "the notes are clashing",       # [U3]
                                  "it sounds so robotic and unnatural"])                     # [U4]
def test_midi_complaints_route_to_analyze_midi(audio_dir, text):
    m = load_lexicon().match(text)
    assert m.descriptors == [] and m.midi
    out = diagnose(text, aid(wav(audio_dir, "pink")))
    assert out["needs_clarification"] is False and out["proposed_changes"] == []
    assert "analyze_midi" in out["notes"][0]


def test_lexicon_entries_are_complete():
    lex = load_lexicon()
    assert set(lex.descriptors) == set(SPEC_PHRASES)
    for name, spec in lex.descriptors.items():
        assert spec["phrases"] and spec["label"] and spec["measure"]["kind"] and spec["change"]["kind"], name


def test_unknown_analysis_id_is_not_found():
    with pytest.raises(ToolError) as e:
        diagnose("too bright", "ffffffffffff")
    assert e.value.code == "NOT_FOUND"


def test_dispatcher_round_trip(audio_dir):
    from mixassist.tools import call_tool

    a = call_tool("analyze_audio", {"path": str(wav(audio_dir, "muddy"))})
    out = call_tool("diagnose_complaint", {"complaint": "sounds boxy", "analysis_id": a["analysis_id"]})
    assert "error" not in out and out["findings"][0]["descriptor"] == "muddy"
