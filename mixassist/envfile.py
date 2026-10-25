"""Load the git-ignored repo-root `.env` (KEY=VALUE lines) into os.environ without overriding existing vars."""
from __future__ import annotations

import os
from pathlib import Path

ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def load_env(path: str | Path | None = None) -> list[str]:
    """Set each non-empty variable from the file that isn't already in os.environ; returns the names set."""
    path = Path(path) if path is not None else ROOT_ENV
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded = []
    for key, value in parse_env(text).items():
        if value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
