"""Audio fixtures for F4–F6 (spec §7): 48 kHz, 24-bit WAV, seed 1.

    python -m fixtures.make [--out fixtures]

writes <out>/audio/*.wav and then, if `fixtures.make_midi` is importable, <out>/midi/*.mid.

The EBU files are synthesised stand-ins for the official Tech 3341/3342 signals with the same
expected values. Cases 1–5 and 15–19 and the 3342 cases follow the Table 1 descriptions; the official
3341 cases 20–23 are programme-like inter-sample-peak signals, replaced here by four 0 dBTP signals
whose sample peaks sit 0.7–3 dB below their true peak.

The noise fixtures share one pink spectrum (exact 1/f power, random phase, 20 Hz–20 kHz, independent
L/R) and apply EQ curves in the frequency domain, so band levels differ only by the curve. Shelves are
logistic in log-frequency, centred on the stated frequency (10–90 % over ~0.4 octave); bells are
Gaussian in log-frequency with a 1-octave FWHM. Every EQ'd variant is gain-matched to pink's
integrated loudness, like a re-export that keeps the same loudness target, so only bright_fixed_loud
(bright_fixed +3 dB) differs in loudness.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48_000
SEED = 1
SUBTYPE = "PCM_24"
PINK_SECONDS = 30.0
PINK_RMS_DBFS = -20.0
DEFAULT_OUT = Path(__file__).resolve().parent

EBU3341_LUFS = {1: -23.0, 2: -33.0, 3: -23.0, 4: -23.0, 5: -23.0}
EBU3341_TP = {15: -6.0, 16: -6.0, 17: -6.0, 18: -6.0, 19: 3.0, 20: 0.0, 21: 0.0, 22: 0.0, 23: 0.0}
EBU3342_LRA = {1: 10.0, 2: 5.0, 3: 20.0, 4: 15.0}
NOISE_FILES = ("pink", "bright", "bright_fixed", "bright_fixed_loud", "muddy", "boomy", "thin", "mono", "antiphase")


def expected_files() -> list[str]:
    names = [f"ebu3341_{c:02d}" for c in (*EBU3341_LUFS, *EBU3341_TP)]
    names += [f"ebu3342_{c:02d}" for c in EBU3342_LRA]
    names += list(NOISE_FILES)
    return [n + ".wav" for n in names]


# --- tones -------------------------------------------------------------------------------------

def _db(x: float) -> float:
    return 10 ** (x / 20)


def _fade(x: np.ndarray, seconds: float = 0.01) -> np.ndarray:
    """Raised-cosine fade in/out, so file edges don't add band-limited overshoot to the true peak."""
    n = int(seconds * SR)
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def _tone_sequence(segments: list[tuple[float, float]], freq: float = 1000.0) -> np.ndarray:
    """Stereo 1 kHz sine, phase-continuous across (level_dbfs, seconds) segments."""
    counts = [int(round(s * SR)) for _, s in segments]
    t = np.arange(sum(counts)) / SR
    amp = np.concatenate([np.full(n, _db(lvl)) for (lvl, _), n in zip(segments, counts)])
    x = _fade(amp * np.sin(2 * np.pi * freq * t))
    return np.column_stack([x, x])


def _tp_tone(freq: float, phase_deg: float, true_peak_dbtp: float, seconds: float = 5.0) -> np.ndarray:
    n = np.arange(int(seconds * SR))
    x = _fade(_db(true_peak_dbtp) * np.sin(2 * np.pi * freq * n / SR + np.deg2rad(phase_deg)))
    return np.column_stack([x, x])


def _isp_pulses(seconds: float = 5.0) -> np.ndarray:
    """Half-band sinc pulses (fs/4 bandwidth) whose 1.0 peak falls halfway between two samples."""
    x = np.zeros(int(seconds * SR))
    k = np.arange(-128, 128)
    pulse = np.sinc((k - 0.5) / 2) * np.hanning(len(k) + 2)[1:-1]
    for start in range(SR // 4, len(x) - 256, SR // 4):
        x[start:start + len(k)] += pulse
    return np.column_stack([x, x])


def ebu3341(case: int) -> np.ndarray:
    seq = {
        1: [(-23, 20)],
        2: [(-33, 20)],
        3: [(-36, 10), (-23, 60), (-36, 10)],
        4: [(-72, 10), (-36, 10), (-23, 60), (-36, 10), (-72, 10)],
        5: [(-26, 20), (-20, 20.1), (-26, 20)],
    }
    if case in seq:
        return _tone_sequence(seq[case])
    tones = {
        15: (SR / 4, 0.0, -6.0),
        16: (SR / 4, 45.0, -6.0),
        17: (SR / 6, 60.0, -6.0),
        18: (SR / 8, 67.5, -6.0),
        19: (SR / 4, 45.0, 3.0),
        20: (SR / 4, 45.0, 0.0),
        21: (SR / 6, 0.0, 0.0),
        22: (SR / 8, 22.5, 0.0),
    }
    if case in tones:
        return _tp_tone(*tones[case])
    if case == 23:
        return _isp_pulses()
    raise ValueError(f"no EBU 3341 case {case}")


def ebu3342(case: int) -> np.ndarray:
    seq = {
        1: [(-20, 20), (-30, 20)],
        2: [(-20, 20), (-15, 20)],
        3: [(-40, 20), (-20, 20)],
        4: [(-50, 20), (-35, 20), (-20, 20), (-35, 20), (-50, 20)],
    }
    return _tone_sequence(seq[case])


# --- noise -------------------------------------------------------------------------------------

def _logistic(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def high_shelf(f: np.ndarray, fc: float, gain_db: float) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return gain_db * _logistic(np.log2(np.maximum(f, 1e-9) / fc) * 12)


def low_shelf(f: np.ndarray, fc: float, gain_db: float) -> np.ndarray:
    return gain_db - high_shelf(f, fc, gain_db)


def bell(f: np.ndarray, fc: float, gain_db: float, fwhm_oct: float = 1.0) -> np.ndarray:
    sigma = fwhm_oct / (2 * np.sqrt(2 * np.log(2)))
    return gain_db * np.exp(-0.5 * (np.log2(np.maximum(f, 1e-9) / fc) / sigma) ** 2)


class PinkSpectrum:
    """Two independent channels of exact-magnitude pink noise, shaped in the frequency domain."""

    def __init__(self, seconds: float = PINK_SECONDS, seed: int = SEED):
        self.n = int(seconds * SR)
        self.f = np.fft.rfftfreq(self.n, 1 / SR)
        rng = np.random.default_rng(seed)
        mag = np.zeros_like(self.f)
        band = (self.f >= 20) & (self.f <= 20_000)
        mag[band] = self.f[band] ** -0.5
        phase = rng.uniform(0, 2 * np.pi, size=(2, len(self.f)))
        self.spec = mag * np.exp(1j * phase)
        raw = np.fft.irfft(self.spec, n=self.n, axis=-1)
        self.scale = _db(PINK_RMS_DBFS) / np.sqrt(np.mean(raw ** 2))

    def render(self, gain_db: np.ndarray | float = 0.0) -> np.ndarray:
        g = 10 ** (np.asarray(gain_db) / 20)
        return (np.fft.irfft(self.spec * g, n=self.n, axis=-1) * self.scale).T


def _integrated(x: np.ndarray) -> float:
    import pyloudnorm

    return pyloudnorm.Meter(SR).integrated_loudness(x)


def noise_fixtures() -> dict[str, np.ndarray]:
    pink = PinkSpectrum()
    f = pink.f
    out = {"pink": pink.render()}
    target = _integrated(out["pink"])

    def eq(curve_db: np.ndarray) -> np.ndarray:
        x = pink.render(curve_db)
        return x * _db(target - _integrated(x))

    shelf_up = high_shelf(f, 6000, 6.0)
    out["bright"] = eq(shelf_up)
    out["bright_fixed"] = eq(shelf_up + high_shelf(f, 8000, -5.0))
    out["bright_fixed_loud"] = out["bright_fixed"] * _db(3.0)
    out["muddy"] = eq(bell(f, 300, 6.0))
    out["boomy"] = eq(bell(f, 100, 6.0))
    out["thin"] = eq(low_shelf(f, 250, -6.0))
    left = out["pink"][:, 0]
    out["mono"] = np.column_stack([left, left])
    out["antiphase"] = np.column_stack([left, -left])
    return out


# --- writing -----------------------------------------------------------------------------------

def _write(path: Path, x: np.ndarray) -> None:
    peak = float(np.max(np.abs(x)))
    if peak >= 1.0:
        raise ValueError(f"{path.name}: sample peak {peak:.4f} would clip in {SUBTYPE}")
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.tmp")
    sf.write(tmp, x, SR, subtype=SUBTYPE, format="WAV")
    os.replace(tmp, path)


def make_audio(audio_dir: Path) -> list[Path]:
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    written = []
    signals: dict[str, np.ndarray] = {}
    for c in (*EBU3341_LUFS, *EBU3341_TP):
        signals[f"ebu3341_{c:02d}"] = ebu3341(c)
    for c in EBU3342_LRA:
        signals[f"ebu3342_{c:02d}"] = ebu3342(c)
    signals.update(noise_fixtures())
    for name, x in signals.items():
        path = audio_dir / f"{name}.wav"
        _write(path, x)
        written.append(path)
    return written


def audio_is_current(audio_dir: Path) -> bool:
    paths = [Path(audio_dir) / n for n in expected_files()]
    if not all(p.exists() for p in paths):
        return False
    return min(p.stat().st_mtime for p in paths) >= Path(__file__).stat().st_mtime


def ensure_audio(out: Path | str = DEFAULT_OUT) -> Path:
    """Build <out>/audio if any WAV is missing or older than this script; returns the audio dir."""
    audio_dir = Path(out) / "audio"
    if not audio_is_current(audio_dir):
        make_audio(audio_dir)
    return audio_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixtures root (default: %(default)s)")
    ns = ap.parse_args(argv)
    paths = make_audio(ns.out / "audio")
    print(f"wrote {len(paths)} WAVs to {ns.out / 'audio'}")
    try:
        from fixtures import make_midi
    except ImportError:
        print("fixtures.make_midi not available; skipped MIDI fixtures")
    else:
        mids = make_midi.make_all(ns.out / "midi")
        print(f"wrote {len(mids)} MIDI files to {ns.out / 'midi'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
