"""analyze_audio core (spec F4): read a WAV, measure it, cache the result under an analysis_id."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from mixassist.errors import ToolError

from . import loudness, stereo
from .spectrum import BANDS, Spectrum, spectrum

MIN_SECONDS = 0.4    # one BS.1770 gating block
MAX_SECONDS = 15 * 60
CACHE_SIZE = 256


@dataclass
class Record:
    """Cached analysis: the tool output plus the PSD the complaint rules integrate over."""

    analysis_id: str
    path: str
    output: dict
    spectrum: Spectrum
    reference_path: str | None = None


_lock = threading.RLock()
_records: OrderedDict[str, Record] = OrderedDict()
_measured: dict[tuple[str, int, int], tuple[dict, Spectrum]] = {}


def make_analysis_id(path: str, mtime_ns: int, options: dict) -> str:
    key = json.dumps({"path": path, "mtime_ns": mtime_ns, "options": options}, sort_keys=True)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _check_path(path: str, what: str = "path") -> Path:
    if not isinstance(path, str) or not path:
        raise ToolError("FILE_ERROR", f"{what} must be a non-empty string")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ToolError("FILE_ERROR", f"{what} must be absolute: {path}",
                        hint="Pass the full path of the exported WAV, e.g. /Users/me/Music/mix.wav")
    if not p.is_file():
        raise ToolError("FILE_ERROR", f"file not found: {p}",
                        hint="Export the mix from FL (File > Export > WAV) and pass that file's path")
    return p


def _read(p: Path) -> tuple[np.ndarray, int]:
    try:
        info = sf.info(str(p))
    except (RuntimeError, OSError) as exc:
        raise ToolError("FILE_ERROR", f"can't read {p.name} as audio: {exc}",
                        hint="Export as WAV (or another format libsndfile reads)") from exc
    duration = info.frames / info.samplerate if info.samplerate else 0.0
    if duration > MAX_SECONDS:
        raise ToolError("OUT_OF_RANGE", f"{p.name} is {duration / 60:.1f} min; the limit is 15 min",
                        hint="Export a shorter section of the song")
    if duration < MIN_SECONDS:
        raise ToolError("OUT_OF_RANGE", f"{p.name} is {duration:.2f} s; at least {MIN_SECONDS} s is needed")
    if info.channels > 2:
        raise ToolError("UNSUPPORTED", f"{p.name} has {info.channels} channels; only mono or stereo is supported",
                        hint="Export a stereo mixdown")
    try:
        data, sr = sf.read(str(p), dtype="float64", always_2d=True)
    except (RuntimeError, OSError) as exc:
        raise ToolError("FILE_ERROR", f"can't read {p.name}: {exc}") from exc
    return data, int(sr)


def _r(x: float | None, nd: int) -> float | None:
    return None if x is None else round(float(x), nd)


def measure(data: np.ndarray, sr: int) -> tuple[dict, Spectrum]:
    """All F4 descriptors for an (n, channels) float array, without analysis_id or reference."""
    n = data.shape[0]
    spec = spectrum(data, sr)
    st_max, lra = loudness.short_term_max_and_lra(data, sr)
    peak = loudness.sample_peak(data)
    rms = float(np.sqrt(np.mean(data ** 2)))
    peak_db = loudness.lin_to_db(peak)
    corr_mean = corr_p5 = side_mid = None
    if data.shape[1] == 2:
        left, right = data[:, 0], data[:, 1]
        corr_mean, corr_p5 = stereo.correlation_stats(left, right, sr)
        side_mid = stereo.side_mid_db(left, right)
    out = {
        "duration_s": round(n / sr, 3),
        "sample_rate": sr,
        "channels": data.shape[1],
        "integrated_lufs": _r(loudness.integrated_lufs(data, sr), 2),
        "short_term_max_lufs": _r(st_max, 2),
        "lra_lu": _r(lra, 2),
        "true_peak_dbtp": _r(loudness.lin_to_db(loudness.true_peak(data)), 2),
        "sample_peak_dbfs": _r(peak_db, 2),
        "crest_db": _r(peak_db - loudness.lin_to_db(rms), 2) if rms > 0 else None,
        "correlation_mean": _r(corr_mean, 4),
        "correlation_p5": _r(corr_p5, 4),
        "side_mid_db": _r(side_mid, 2),
        "centroid_hz": _r(spec.centroid_hz(), 1),
        "bands_db": {k: _r(v, 2) for k, v in spec.bands_db().items()},
        "third_octave_db": [{"hz": b["hz"], "db": round(b["db"], 2)} for b in spec.third_octave_db()],
    }
    return out, spec


def _measure_file(p: Path) -> tuple[dict, Spectrum, int]:
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _measured.get(key)
    if hit is None:
        data, sr = _read(p)
        hit = measure(data, sr)
        with _lock:
            _measured[key] = hit
            while len(_measured) > CACHE_SIZE:
                _measured.pop(next(iter(_measured)))
    return copy.deepcopy(hit[0]), hit[1], st.st_mtime_ns


def reference_diff(mix: dict, ref: dict) -> dict:
    """Reference loudness-matched to the mix: band_diff = mix − (ref + (mix_lufs − ref_lufs))."""
    lm, lr = mix["integrated_lufs"], ref["integrated_lufs"]
    lufs_diff = None if lm is None or lr is None else lm - lr
    gain = lufs_diff or 0.0
    diffs = {}
    for name, _, _ in BANDS:
        a, b = mix["bands_db"][name], ref["bands_db"][name]
        diffs[name] = None if a is None or b is None else round(a - (b + gain), 2)
    return {"analysis_id": ref["analysis_id"], "lufs_diff": _r(lufs_diff, 2), "band_diff_db": diffs}


def analyze(path: str, reference_path: str | None = None) -> dict:
    p = _check_path(path)
    rp = _check_path(reference_path, "reference_path") if reference_path else None
    base, spec, mtime_ns = _measure_file(p)
    options: dict = {}
    if rp is not None:
        options = {"reference_path": str(rp), "reference_mtime_ns": rp.stat().st_mtime_ns}
    aid = make_analysis_id(str(p), mtime_ns, options)
    with _lock:
        if aid in _records:
            _records.move_to_end(aid)
            return copy.deepcopy(_records[aid].output)
    out = {"analysis_id": aid, **base}
    if rp is not None:
        out["reference"] = reference_diff(out, analyze(str(rp)))
    record = Record(analysis_id=aid, path=str(p), output=out, spectrum=spec,
                    reference_path=str(rp) if rp else None)
    with _lock:
        _records[aid] = record
        while len(_records) > CACHE_SIZE:
            _records.popitem(last=False)
    return copy.deepcopy(out)


def get_record(analysis_id: str) -> Record:
    with _lock:
        rec = _records.get(analysis_id)
    if rec is None:
        raise ToolError("NOT_FOUND", f"no analysis with id {analysis_id!r}",
                        hint="Call analyze_audio first and pass the analysis_id it returns")
    return rec


def get_analysis(analysis_id: str) -> dict:
    """The cached analyze_audio output for analysis_id (raises ToolError NOT_FOUND)."""
    return copy.deepcopy(get_record(analysis_id).output)


def clear_cache() -> None:
    with _lock:
        _records.clear()
        _measured.clear()
