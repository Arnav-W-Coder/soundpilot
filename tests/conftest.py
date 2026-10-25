from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mixassist.fl import set_fl
from mixassist.fl.client import FLClient
from mixassist.fl.fake import FakeTransport, default_fake

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REQUIRED_WAVS = ("pink.wav", "bright.wav", "bright_fixed.wav", "bright_fixed_loud.wav", "muddy.wav")


@pytest.fixture(autouse=True)
def fake_fl():
    """A fresh default FakeFL (Master + 12 named + 115 default inserts) as the active FL for every test."""
    fake = default_fake()
    set_fl(FLClient(FakeTransport(fake), "fake"))
    yield fake
    set_fl(None)


@pytest.fixture
def fl_client(fake_fl):
    from mixassist.fl import get_fl

    return get_fl()


def install_fake(**kwargs):
    """Replace the active FL with a FakeFL built from kwargs; returns (fake, client)."""
    from mixassist.fl.fake import FakeFL

    fake = FakeFL(**kwargs) if "tracks" in kwargs else default_fake(**kwargs)
    client = FLClient(FakeTransport(fake), "fake")
    set_fl(client)
    return fake, client


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """fixtures/ with generated WAVs (git-ignored) and MIDI files; runs `python -m fixtures.make` if needed."""
    audio = FIXTURES / "audio"
    if not all((audio / name).exists() for name in REQUIRED_WAVS):
        if not (FIXTURES / "make.py").exists():
            pytest.skip("fixtures/make.py is not available yet")
        proc = subprocess.run(
            [sys.executable, "-m", "fixtures.make", "--out", str(FIXTURES)],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            pytest.fail(f"fixtures.make failed:\n{proc.stdout}\n{proc.stderr}")
    return FIXTURES
