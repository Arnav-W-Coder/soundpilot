"""Spec §3.1 error format shared by every tool."""
from __future__ import annotations

ERROR_CODES = (
    "FL_NOT_CONNECTED",
    "FL_TIMEOUT",
    "FL_ERROR",
    "NOT_FOUND",
    "AMBIGUOUS_TARGET",
    "OUT_OF_RANGE",
    "UNSUPPORTED",
    "FILE_ERROR",
    "NEEDS_CLARIFICATION",
    "VERIFY_FAILED",
)


class ToolError(Exception):
    def __init__(self, code: str, message: str, candidates: list[str] | None = None, hint: str | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.candidates = candidates
        self.hint = hint

    def to_dict(self) -> dict:
        return error_dict(self.code, self.message, self.candidates, self.hint)


def error_dict(code: str, message: str, candidates: list[str] | None = None, hint: str | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if candidates is not None:
        err["candidates"] = list(candidates)
    if hint is not None:
        err["hint"] = hint
    return {"error": err}


def is_error(result: dict) -> bool:
    return isinstance(result, dict) and set(result) == {"error"}
