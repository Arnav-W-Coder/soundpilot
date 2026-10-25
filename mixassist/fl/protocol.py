"""Spec §3.3 SysEx codec.

Frame: F0 7D 4D 41 <payload ASCII> F7, at most 1024 bytes including the framing.
Payload: ``<id>|<op>|k=<t><v>;k=<t><v>`` (the trailing ``|`` is always present, the arg list may be empty).
Types: ``i`` int, ``f`` float, ``b`` 0/1, ``s`` percent-escaped UTF-8, ``n`` null (no value).

String escaping: every UTF-8 byte outside printable ASCII 0x20..0x7E, plus ``%`` ``|`` ``;`` ``=``,
is written as ``%XX`` (uppercase hex). Decoders accept any ``%XX``.
Floats are written with Python ``repr``; non-finite floats are sent as null.
"""
from __future__ import annotations

import math

HEADER = bytes([0xF0, 0x7D, 0x4D, 0x41])
END = 0xF7
MAX_FRAME = 1024
_SPECIAL = set(b"%|;=")


def escape(text: str) -> str:
    out = []
    for byte in text.encode("utf-8"):
        if byte < 0x20 or byte > 0x7E or byte in _SPECIAL:
            out.append("%%%02X" % byte)
        else:
            out.append(chr(byte))
    return "".join(out)


def unescape(text: str) -> str:
    raw = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%":
            if i + 3 > len(text):
                raise ValueError("truncated %-escape")
            raw += bytes([int(text[i + 1 : i + 3], 16)])
            i += 3
        else:
            raw += ch.encode("ascii")
            i += 1
    return raw.decode("utf-8")


def _encode_value(value) -> str:
    if value is None:
        return "n"
    if isinstance(value, bool):
        return "b1" if value else "b0"
    if isinstance(value, int):
        return "i%d" % value
    if isinstance(value, float):
        if not math.isfinite(value):
            return "n"
        return "f" + repr(value)
    if isinstance(value, str):
        return "s" + escape(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def _decode_value(token: str):
    if not token:
        raise ValueError("empty value")
    t, v = token[0], token[1:]
    if t == "n":
        return None
    if t == "i":
        return int(v)
    if t == "f":
        return float(v)
    if t == "b":
        if v not in ("0", "1"):
            raise ValueError(f"bad bool {v!r}")
        return v == "1"
    if t == "s":
        return unescape(v)
    raise ValueError(f"unknown type tag {t!r}")


def encode(msg_id: int, op: str, args: dict | None = None) -> bytes:
    if not 1 <= int(msg_id) <= 999999:
        raise ValueError("msg_id must be 1..999999")
    parts = []
    for key, value in (args or {}).items():
        if not key or any(c in key for c in "|;=%") or not key.isascii():
            raise ValueError(f"bad key {key!r}")
        parts.append(f"{key}={_encode_value(value)}")
    payload = f"{int(msg_id)}|{op}|" + ";".join(parts)
    frame = HEADER + payload.encode("ascii") + bytes([END])
    if len(frame) > MAX_FRAME:
        raise ValueError(f"frame is {len(frame)} bytes (max {MAX_FRAME})")
    return frame


def decode(frame: bytes) -> tuple[int, str, dict]:
    frame = bytes(frame)
    if len(frame) < 6 or frame[:4] != HEADER or frame[-1] != END:
        raise ValueError("not a MixAssist SysEx frame")
    if len(frame) > MAX_FRAME:
        raise ValueError("frame too long")
    payload = frame[4:-1].decode("ascii")
    pieces = payload.split("|", 2)
    if len(pieces) < 2:
        raise ValueError("payload needs id|op")
    msg_id = int(pieces[0])
    op = pieces[1]
    args: dict = {}
    rest = pieces[2] if len(pieces) == 3 else ""
    if rest:
        for item in rest.split(";"):
            if not item:
                continue
            key, sep, token = item.partition("=")
            if not sep:
                raise ValueError(f"bad arg {item!r}")
            args[key] = _decode_value(token)
    return msg_id, op, args
