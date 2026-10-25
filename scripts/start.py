"""Cross-platform SoundPilot setup, diagnostic, and server launcher.

Use the system Python to run 'setup'; it creates .venv and installs project deps.
Other commands delegate to the virtual environment's Python. Works in PowerShell,
Command Prompt, macOS Terminal, and Linux shells.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(args: list[str], *, cwd=ROOT, env=None):
    return subprocess.call([str(a) for a in args], cwd=cwd, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "doctor", "check", "backend", "fake", "tests"))
    parser.add_argument("--skip-install", action="store_true", help="setup: create venv only")
    args = parser.parse_args(argv)
    py = venv_python()
    if args.action == "setup":
        if not py.exists():
            code = run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
            if code:
                return code
        if args.skip_install:
            print("Virtual environment ready:", py)
            return 0
        return run([py, "-m", "pip", "install", "-e", ".[dev]"])
    if not py.exists():
        print(f"No virtual environment found at {py}. Run: python scripts/start.py setup", file=sys.stderr)
        return 2
    if args.action in ("doctor", "check"):
        return run([py, "-m", "mixassist.doctor", *(["--check"] if args.action == "check" else [])])
    if args.action == "tests":
        return run([py, "-m", "pytest", "-q"])
    if args.action == "fake":
        return run([py, "-m", "mixassist", "--http", "--fake"])
    return run([py, "-m", "mixassist", "--http", "--bridge"])


if __name__ == "__main__":
    raise SystemExit(main())
