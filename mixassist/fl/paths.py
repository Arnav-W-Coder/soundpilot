"""Resolve FL Studio user data folders on macOS and Windows.

Uses Windows' per-user 'Personal' known-folder registry path to support OneDrive
Documents redirection. Overrides are intentionally explicit and deterministic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def documents_candidates(platform: str | None = None, environ=None, home: Path | None = None,
                         registry_personal: str | None = None) -> list[Path]:
    """Return ordered candidate Documents folders without creating any directories."""
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    home = Path(home) if home is not None else Path.home()
    candidates: list[Path] = []

    def add(path):
        if path and Path(path) not in candidates:
            candidates.append(Path(path))

    if platform == "win32":
        personal = registry_personal
        if personal is None:
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                    personal, _ = winreg.QueryValueEx(key, "Personal")
            except (ImportError, OSError):
                personal = None
        if personal:
            import re
            expanded = re.sub(r"%([^%]+)%", lambda m: env.get(m.group(1), m.group(0)), personal)
            add(os.path.expanduser(expanded.replace("\\\\", os.sep)))
        if env.get("OneDrive"):
            add(Path(env["OneDrive"]) / "Documents")
        if env.get("OneDriveCommercial"):
            add(Path(env["OneDriveCommercial"]) / "Documents")
        if env.get("OneDriveConsumer"):
            add(Path(env["OneDriveConsumer"]) / "Documents")
        add(Path(env.get("USERPROFILE", home)) / "Documents")
    else:
        add(home / "Documents")
    return candidates


def fl_settings_candidates(platform: str | None = None, environ=None, home=None,
                           registry_personal: str | None = None) -> list[Path]:
    return [
        docs / "Image-Line" / "FL Studio" / "Settings"
        for docs in documents_candidates(platform, environ, home, registry_personal)
    ]


def find_fl_subdir(relative_path: str, *, platform: str | None = None,
                   environ=None, home=None, registry_personal: str | None = None) -> Path | None:
    for base in fl_settings_candidates(platform, environ, home, registry_personal):
        candidate = base / relative_path
        if candidate.is_dir():
            return candidate
    return None
