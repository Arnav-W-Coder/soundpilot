"""Copy the MixAssist FL Studio controller script to the selected user folder.

No files are written until --install is specified. FL Studio must be restarted or
its controller scripts refreshed after copying.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import sys

from mixassist.fl.paths import fl_settings_candidates

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fl_script" / "MixAssist" / "device_MixAssist.py"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", help="FL Studio Settings directory (parent of Hardware)")
    p.add_argument("--install", action="store_true", help="perform the copy")
    args = p.parse_args(argv)
    candidates = fl_settings_candidates()
    if args.path:
        settings = Path(args.path).expanduser()
    else:
        existing = [d for d in candidates if d.is_dir()]
        if len(existing) != 1:
            print("Couldn't uniquely locate FL Studio Settings. Candidates:")
            for folder in candidates:
                print("  ", folder, "(exists)" if folder.is_dir() else "")
            print("Specify --path to the correct Settings folder.")
            return 2
        settings = existing[0]
    dest = settings / "Hardware" / "MixAssist" / SOURCE.name
    print("Source:", SOURCE)
    print("Destination:", dest)
    if not SOURCE.is_file():
        print("Controller script is missing from this checkout.")
        return 2
    if not args.install:
        print("Dry run only. Add --install to copy the script.")
        return 0
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            backup = dest.with_name(dest.name + ".soundpilot.bak")
            shutil.copy2(dest, backup)
            print("Backed up existing script to:", backup)
        shutil.copy2(SOURCE, dest)
    except OSError as exc:
        print("Could not install FL controller script:", exc)
        return 1
    print("Installed. In FL Studio: Options > MIDI Settings, choose controller type MixAssist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
