"""Deterministic judges of evals/theory_suite.py on hand-written good and bad answers."""
from types import SimpleNamespace

from evals.theory_suite import CASES, MU, judge, pcs


def _turn(*calls, proposals=()):
    return SimpleNamespace(attempted_calls=list(calls), invented_tools=[], writes_after_clarification=0,
                           proposals=[{"preview": {"summary": s}} for s in proposals])


def _play(pitches, starts=None, length=0.45, vel=None, chords=None, **extra):
    if chords is not None:
        notes = [{"pitch": p, "start_beat": 4 * i, "length_beats": 3.9, "velocity": 90}
                 for i, chord in enumerate(chords) for p in chord]
    else:
        starts = starts or [0.5 * i for i in range(len(pitches))]
        vel = vel or [100] * len(pitches)
        notes = [{"pitch": p, "start_beat": s, "length_beats": length, "velocity": v}
                 for p, s, v in zip(pitches, starts, vel)]
    return {"tool": "fl_play_midi", "args": {"notes": notes, **extra}}


def _case(i):
    return next(c for c in CASES if c.id == i)


def test_arpeggio_three_times():
    assert judge(_case(1), _turn(_play([64, 68, 71, 76] * 3)))[0]
    assert judge(_case(1), _turn(_play([64, 68, 71, 76, 71, 68] * 3 + [64])))[0]
    assert not judge(_case(1), _turn(_play([64, 68, 71, 76] * 4)))[0]  # 4 repetitions
    assert not judge(_case(1), _turn(_play([64, 67, 71, 76] * 3)))[0]  # G instead of G#
    assert not judge(_case(1), _turn(_play([64, 68, 71, 76] * 3, record=True)))[0]


def test_scales():
    assert judge(_case(2), _turn(_play([60, 62, 64, 65, 67, 69, 71, 72])))[0]
    assert not judge(_case(2), _turn(_play([60, 62, 64, 65, 67, 69, 71])))[0]
    assert judge(_case(3), _turn(_play([62, 64, 65, 67, 69, 71, 72, 74])))[0]
    assert not judge(_case(3), _turn(_play([62, 64, 65, 67, 69, 70, 72, 74])))[0]  # Bb is D minor, not dorian


def test_a_minor_melody():
    pitches = [69, 72, 71, 69, 64, 67, 65, 64, 69, 72, 74, 76, 74, 72, 71, 69]
    vel = [110, 80, 85, 80] * 4
    assert judge(_case(4), _turn(_play(pitches, starts=list(range(16)), length=0.9, vel=vel)))[0]
    assert not judge(_case(4), _turn(_play(pitches, starts=list(range(16)), length=0.9)))[0]  # flat velocity
    assert not judge(_case(4), _turn(_play([69, 68] * 4, vel=[100, 80] * 4)))[0]  # G#
    assert not judge(_case(4), _turn(_play(pitches, starts=[2 * i for i in range(16)], vel=vel)))[0]  # 32 beats


def test_ii_v_i_blocks_and_arpeggiated():
    assert judge(_case(5), _turn(_play(None, chords=[[50, 53, 57, 60], [55, 59, 62, 65], [48, 52, 55, 59]])))[0]
    assert judge(_case(5), _turn(_play([50, 53, 57, 55, 59, 62, 48, 52, 55])))[0]
    assert not judge(_case(5), _turn(_play(None, chords=[[53, 57, 60], [55, 59, 62], [48, 52, 55]])))[0]  # F, not Dm


def test_first_inversion_and_record():
    assert judge(_case(6), _turn(_play([59, 62, 67], starts=[0, 0, 0])))[0]
    assert not judge(_case(6), _turn(_play([55, 59, 62], starts=[0, 0, 0])))[0]
    assert judge(_case(7), _turn(_play([60, 64, 67, 72], record=True, start_bar=3)))[0]
    assert not judge(_case(7), _turn(_play([60, 64, 67, 72], record=True)))[0]
    late = _play([60, 64, 67, 72], starts=[8, 8.5, 9, 9.5], record=True, start_bar=3)  # start_bar already offsets
    assert not judge(_case(7), _turn(late))[0]


def test_mix_judges():
    carve = {"tool": "fl_set_track", "args": {"track": "Bass", "eq_band": "mid", "eq_gain_db": -3, "eq_freq_hz": 80}}
    widen = {"tool": "fl_set_track", "args": {"track": "Bass", "stereo_width_pct": 30}}
    assert judge(_case(8), _turn(carve, proposals=["Bass: mid EQ gain"]))[0]
    assert judge(_case(8), _turn())[0]
    assert not judge(_case(8), _turn(widen))[0]
    assert not judge(_case(8), _turn({"tool": "fl_set_track", "args": {"track": "Kick", "pan_pct": 20}}))[0]
    analyzed = [{"tool": "analyze_audio", "args": {"path": str(MU)}},
                {"tool": "diagnose_complaint", "args": {"complaint": "fighting", "analysis_id": "abc"}}]
    assert judge(_case(9), _turn(*analyzed))[0]
    assert not judge(_case(9), _turn(carve))[0]
    big = {"tool": "fl_set_track", "args": {"track": "Vocal", "volume_change_db": 6}}
    assert judge(_case(10), _turn({"tool": "fl_set_track", "args": {"track": "Vocal", "volume_change_db": 2}}))[0]
    assert not judge(_case(10), _turn(big))[0]
    assert not judge(_case(10), _turn({"tool": "fl_set_track", "args": {"track": "Vocal", "volume_db": 0}}))[0]


def test_pitch_class_helper():
    assert pcs("C", "E", "G#") == [0, 4, 8]
