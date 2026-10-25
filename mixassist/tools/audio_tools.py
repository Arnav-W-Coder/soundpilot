"""Audio tools (spec §3.2): analyze_audio (F4), diagnose_complaint (F5), verify_fix (F6).

Each takes the schema-validated args dict and returns the §3.2 output; failures raise ToolError.
"""
from __future__ import annotations

from mixassist import analysis


def analyze_audio(args: dict) -> dict:
    return analysis.analyze(args["path"], args.get("reference_path"))

