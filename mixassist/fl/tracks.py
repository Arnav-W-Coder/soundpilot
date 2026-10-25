"""Track reference resolution (spec §2): int index or name; exact case-insensitive, then unique prefix."""
from __future__ import annotations

import difflib

from mixassist.errors import ToolError

MAX_CANDIDATES = 10


def resolve_track(ref: int | str, tracks: list[dict]) -> dict:
    """Return the track dict (with at least ``index`` and ``name``) that ``ref`` names."""
    if isinstance(ref, str) and ref.strip().lstrip("-").isdigit():
        ref = int(ref.strip())
    if isinstance(ref, bool):
        raise ToolError("NOT_FOUND", f"invalid track reference {ref!r}")
    if isinstance(ref, int):
        for track in tracks:
            if track["index"] == ref:
                return track
        raise ToolError("NOT_FOUND", f"no mixer track with index {ref}", hint=f"valid indices are 0..{len(tracks) - 1}")

    wanted = str(ref).strip().lower()
    if not wanted:
        raise ToolError("NOT_FOUND", "empty track name")
    exact = [t for t in tracks if t["name"].strip().lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ToolError(
            "AMBIGUOUS_TARGET",
            f"{len(exact)} tracks are named {ref!r}",
            candidates=[f"{t['index']}: {t['name']}" for t in exact[:MAX_CANDIDATES]],
            hint="use the track index",
        )
    prefix = [t for t in tracks if t["name"].strip().lower().startswith(wanted)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        raise ToolError(
            "AMBIGUOUS_TARGET",
            f"{ref!r} matches {len(prefix)} tracks",
            candidates=[t["name"] for t in prefix[:MAX_CANDIDATES]],
            hint="ask the user which track they mean",
        )
    names = [t["name"] for t in tracks]
    close = difflib.get_close_matches(str(ref), names, n=3, cutoff=0.5)
    raise ToolError(
        "NOT_FOUND",
        f"no mixer track named {ref!r}",
        candidates=close or None,
        hint="call fl_get_mix_overview to list track names",
    )
