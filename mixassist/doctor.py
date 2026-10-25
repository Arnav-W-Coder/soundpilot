"""Non-destructive SoundPilot system and virtual MIDI diagnostic.

Usage: python -m mixassist.doctor [--check] [--json]
No MIDI command is sent unless --check is specified.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from mixassist.fl.bridge import DEFAULT_FROM_PORT, DEFAULT_TO_PORT, find_port
from mixassist.fl.paths import fl_settings_candidates, find_fl_subdir


def diagnose(check: bool = False, port_reader=None, check_fn=None) -> dict:
    system = platform.system()
    fl_folder = find_fl_subdir("Hardware", platform=sys.platform)
    pianoroll = find_fl_subdir("Piano roll scripts", platform=sys.platform)
    script = fl_folder / "MixAssist" / "device_MixAssist.py" if fl_folder else None
    result = {
        "os": system, "python": sys.version.split()[0],
        "fl_script": str(script) if script and script.is_file() else None,
        "hardware_dir": str(fl_folder) if fl_folder else None,
        "piano_roll_scripts_dir": str(pianoroll) if pianoroll else None,
        "candidate_settings_dirs": [str(p) for p in fl_settings_candidates()],
        "ports": None, "bridge": "not checked",
        "instructions": [],
    }
    if not script or not script.is_file():
        result["instructions"].append(
            "Install fl_script/MixAssist/device_MixAssist.py into FL Studio's "
            "Settings/Hardware/MixAssist folder (see docs/windows-setup.md)."
        )
    try:
        if port_reader is None:
            from mixassist.fl.bridge import list_ports
            port_reader = list_ports
        result["ports"] = port_reader()
        # Python sends to an output port; it receives on an input port.
        missing = []
        if find_port(result["ports"]["outputs"], os.getenv("SOUNDPILOT_MIDI_TO_PORT", DEFAULT_TO_PORT)) is None:
            missing.append("MA to FL (Python output)")
        if find_port(result["ports"]["inputs"], os.getenv("SOUNDPILOT_MIDI_FROM_PORT", DEFAULT_FROM_PORT)) is None:
            missing.append("MA from FL (Python input)")
        if missing:
            result["instructions"].append(
                "Missing MIDI ports: " + ", ".join(missing) +
                ". Windows: launch loopMIDI, create both ports and leave it running. "
                "macOS: enable IAC Driver and create both ports."
            )
        elif check:
            try:
                if check_fn is None:
                    from mixassist.fl.bridge import BridgeTransport
                    from mixassist.fl.client import FLClient
                    def check_fn():
                        with BridgeTransport() as transport:
                            return FLClient(transport, "bridge").request("hello", timeout=2.0)
                reply = check_fn()
                result["bridge"] = "connected" if "api" in reply else "unexpected response"
                result["hello"] = reply
            except Exception as exc:
                result["bridge"] = "failed"
                result["instructions"].append(
                    "MIDI ports exist, but FL did not answer the hello request: " + str(exc) +
                    ". In FL Options > MIDI Settings, enable the 'MA to FL' input "
                    "with MixAssist controller type and match its port number to the "
                    "'MA from FL' output. Check View > Script output."
                )
        elif not missing:
            result["bridge"] = "ports detected; use --check to test FL"
    except Exception as exc:
        result["instructions"].append(
            "Unable to inspect MIDI ports: " + str(exc) +
            ". Install python-rtmidi in the active virtual environment."
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="send a non-mutating FL hello request")
    parser.add_argument("--json", action="store_true", help="machine-readable status")
    args = parser.parse_args(argv)
    data = diagnose(check=args.check)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"SoundPilot doctor: {data['os']} / Python {data['python']}")
        print("FL controller script:", data["fl_script"] or "not found")
        print("MIDI ports:", data["ports"] or "unavailable")
        print("Bridge:", data["bridge"])
        for hint in data["instructions"]:
            print("ACTION:", hint)
    return 1 if args.check and data["bridge"] != "connected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
