"""The music theory / mixing reference in the system prompt: every number re-derived from first principles."""
import re
import tomllib
from pathlib import Path

import pytest

from mixassist.agent import BASE_PROMPT, KNOWLEDGE_DIR, KNOWLEDGE_FILES, SYSTEM_PROMPT, load_knowledge
from mixassist.midi.analysis import MAJOR_STEPS, MINOR_STEPS

THEORY = (KNOWLEDGE_DIR / "music_theory.md").read_text(encoding="utf-8")
MIXING = (KNOWLEDGE_DIR / "mixing.md").read_text(encoding="utf-8")

NATURALS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def pc(name: str) -> int:
    return (NATURALS[name[0]] + name[1:].count("#") - name[1:].count("b")) % 12


def midi(note: str) -> int:
    m = re.fullmatch(r"([A-G][#b]?)(-?\d+)", note)
    return 12 * (int(m[2]) + 1) + pc(m[1])


def line(text: str, prefix: str) -> str:
    found = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


def cumulative(steps) -> list[int]:
    out = [0]
    for s in steps[:-1]:
        out.append(out[-1] + s)
    return out


MAJOR_WH = [2, 2, 1, 2, 2, 2, 1]


def mode(rotation: int) -> list[int]:
    return cumulative(MAJOR_WH[rotation:] + MAJOR_WH[:rotation])


def expected_scales() -> dict[str, list[int]]:
    major, minor = mode(0), mode(5)
    return {
        "major": major,
        "natural minor": minor,
        "harmonic minor": minor[:6] + [minor[6] + 1],
        "melodic minor up": minor[:5] + [minor[5] + 1, minor[6] + 1],
        "dorian": mode(1),
        "phrygian": mode(2),
        "lydian": mode(3),
        "mixolydian": mode(4),
        "major pentatonic": [major[i] for i in (0, 1, 2, 4, 5)],
        "minor pentatonic": [minor[i] for i in (0, 2, 3, 4, 6)],
        "blues": sorted([minor[i] for i in (0, 2, 3, 4, 6)] + [6]),
    }


def stacked(*thirds: int) -> list[int]:
    return cumulative(list(thirds) + [0])


EXPECTED_CHORDS = {
    "maj": stacked(4, 3), "min": stacked(3, 4), "dim": stacked(3, 3), "aug": stacked(4, 4),
    "sus2": [0, 2, 7], "sus4": [0, 5, 7],
    "maj7": stacked(4, 3, 4), "7": stacked(4, 3, 3), "m7": stacked(3, 4, 3), "m7b5": stacked(3, 3, 4),
}
QUALITY = {tuple(v): k for k, v in EXPECTED_CHORDS.items()}


def formulas(text_line: str) -> dict[str, list[int]]:
    body = text_line.split(":", 1)[1]
    out = {}
    for part in body.split("|"):
        m = re.fullmatch(r"\s*(.*?) ((?:\d+ ?)+)\s*", part)
        assert m, part
        out[m[1]] = [int(x) for x in m[2].split()]
    return out


def diatonic(scale: list[int], degree: int, size: int = 3) -> list[int]:
    """Stack scale thirds on a degree (0-based); intervals above that degree's root."""
    root = scale[degree]
    return [(scale[(degree + 2 * k) % 7] - root) % 12 for k in range(size)]


def chord_name(tonic_pc: int, scale: list[int], degree: int, size: int = 3) -> str:
    quality = QUALITY[tuple(diatonic(scale, degree, size))]
    suffix = {"maj": "", "min": "m", "dim": "dim"}.get(quality, quality)
    return SHARP_NAMES[(tonic_pc + scale[degree]) % 12] + suffix


def roman(scale: list[int], degree: int) -> str:
    numeral = ("I", "II", "III", "IV", "V", "VI", "VII")[degree]
    quality = QUALITY[tuple(diatonic(scale, degree))]
    return numeral if quality == "maj" else numeral.lower() + ("°" if quality == "dim" else "")


# ------------------------------------------------------------------ prompt wiring
def test_knowledge_is_in_the_system_prompt():
    assert SYSTEM_PROMPT.startswith(BASE_PROMPT)
    assert SYSTEM_PROMPT.endswith(load_knowledge())
    for name in KNOWLEDGE_FILES:
        assert (KNOWLEDGE_DIR / name).read_text(encoding="utf-8").strip() in SYSTEM_PROMPT
    for rule in ("Talk in units only", "Never propose writes alongside a clarification question",
                 "measure first: analyze_audio", "record=false"):
        assert rule in BASE_PROMPT


def test_knowledge_ships_as_package_data():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert "knowledge/*.md" in pyproject["tool"]["setuptools"]["package-data"]["mixassist"]


def test_prompt_stays_small():
    # AI Studio's free tier allows 16k input tokens/min; Gemma splits digits into single tokens, so this
    # reference costs ~1 token per 2.3 chars (measured 1,561 tokens for the addition with Ollama gemma4).
    assert len(SYSTEM_PROMPT) - len(BASE_PROMPT) <= 3800


# ------------------------------------------------------------------ pitch numbers
def test_pitch_formula_and_reference_notes():
    assert "pitch = 12*(octave+1) + pc" in THEORY
    assert midi("C4") == 60 and "C4 = 60" in THEORY
    assert midi("A4") == 69 and "A4 = 69" in THEORY
    assert '"C5"' in THEORY and "may mean 60" in THEORY  # FL's octave labels are one higher


def test_pitch_class_table():
    table = line(THEORY, "pc:")
    pairs = re.findall(r"\b([A-G]#?)(\d+)\b", table.split(";")[0])
    assert [n for n, _ in pairs] == list(SHARP_NAMES)
    assert all(pc(n) == int(v) for n, v in pairs)
    flat, sharp, value = re.search(r"\(([A-G]b) = ([A-G]#) = (\d+)\)", table).groups()
    assert pc(flat) == pc(sharp) == int(value) and pc(flat[0]) - 1 == pc(flat)


def test_named_ranges_match_their_numbers():
    found = re.findall(r"(\d+)-(\d+) \(([A-G]#?\d)-([A-G]#?\d)\)", THEORY)
    assert len(found) == 2
    for lo, hi, lo_name, hi_name in found:
        assert (midi(lo_name), midi(hi_name)) == (int(lo), int(hi))
    assert (midi("C3"), midi("C5")) == (48, 72)  # "48-72" for chords/pads


# ------------------------------------------------------------------ scales and chords
def test_scale_formulas():
    assert formulas(line(THEORY, "Scales")) == expected_scales()
    assert tuple(expected_scales()["major"]) == MAJOR_STEPS
    assert tuple(expected_scales()["natural minor"]) == MINOR_STEPS


def test_chord_formulas():
    assert formulas(line(THEORY, "Chords")) == EXPECTED_CHORDS


def test_diatonic_chords():
    text = line(THEORY, "Diatonic")
    major, minor = expected_scales()["major"], expected_scales()["natural minor"]
    assert "major key " + " ".join(roman(major, d) for d in range(7)) in text
    assert "Minor key " + " ".join(roman(minor, d) for d in range(7)) in text
    assert "C: " + " ".join(chord_name(pc("C"), major, d) for d in range(7)) in text
    assert "Am: " + " ".join(chord_name(pc("A"), minor, d) for d in range(7)) in text
    harmonic = expected_scales()["harmonic minor"]
    assert chord_name(pc("A"), harmonic, 4) == "E" and chord_name(pc("A"), harmonic, 4, 4) == "E7"
    assert "harmonic minor makes V major (E, E7)" in text


def test_progressions():
    text = line(THEORY, "Progressions")
    major, minor = expected_scales()["major"], expected_scales()["natural minor"]
    ii_v_i = " ".join(chord_name(pc("C"), major, d, 4) for d in (1, 4, 0))
    assert ii_v_i == "Dm7 G7 Cmaj7" and f"ii-V-I (C: {ii_v_i})" in text
    i_vi_iii_vii = " ".join(chord_name(pc("A"), minor, d) for d in (0, 5, 2, 6))
    assert i_vi_iii_vii == "Am F C G" and f"i-VI-III-VII ({i_vi_iii_vii})" in text
    blues = re.search(r"12-bar blues ((?:IV|I|V)(?: (?:IV|I|V)\b)*)", text)[1].split()
    assert len(blues) == 12 and blues[:4] == ["I"] * 4 and blues[4:6] == ["IV", "IV"] and blues[8] == "V"


def test_chord_and_inversion_examples():
    text = line(THEORY, "Chord pitches")
    bb3 = midi("Bb3")
    assert f"Bbmaj7 on Bb3 = {bb3}: " + " ".join(str(bb3 + i) for i in EXPECTED_CHORDS["maj7"]) in text
    root = [midi("A3") + i for i in EXPECTED_CHORDS["min"]]
    first, second = root[1:] + [root[0] + 12], root[2:] + [root[0] + 12, root[1] + 12]
    assert min(first) % 12 == pc("C") and min(second) % 12 == pc("E")
    assert "Am " + " ".join(map(str, root)) in text
    assert "1st inversion (3rd lowest) " + " ".join(map(str, first)) in text
    assert "2nd (5th lowest) " + " ".join(map(str, second)) in text


def test_arpeggio_example():
    text = line(THEORY, "Arpeggio")
    pitches = [int(x) for x in re.search(r"in 8ths: ((?:\d+ )+\d+)", text)[1].split()]
    figure = pitches[:4]
    assert pitches == figure * 2 and figure == sorted(figure)
    assert [p % 12 for p in figure] == [pc("A"), pc("C"), pc("E"), pc("A")] and figure[3] - figure[0] == 12
    assert "start_beat 0, 0.5 .. 3.5" in text and len(pitches) * 0.5 == 4  # 8 eighths fill one bar


def test_rhythm_values():
    text = line(THEORY, "Beats")
    values = dict((k, float(v)) for k, v in re.findall(r"(whole|half|quarter|8th triplet|8th|16th) ([\d.]+)", text))
    assert values.pop("8th triplet") == pytest.approx(2 / 3 * 0.5, abs=5e-4)
    assert values == {"whole": 4, "half": 2, "quarter": 1, "8th": 0.5, "16th": 0.25}
    assert "4 bars = 16 beats" in text and "beat 4(n-1)" in text


def test_velocity_and_mix_numbers_in_range():
    for lo, hi in re.findall(r"(\d+)-(\d+)", line(THEORY, "Velocity")):
        assert 1 <= int(lo) < int(hi) <= 127
    assert "-1 dBTP" in MIXING and "-14 LUFS" in MIXING
    assert "never widen or pan kick, bass or sub" in MIXING
    assert "no high-pass" in MIXING
