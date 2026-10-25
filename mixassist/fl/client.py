"""FLClient: one §3.3 op round trip at a time over a transport (FakeTransport or BridgeTransport)."""
from __future__ import annotations

import logging
import threading
import time

from mixassist.errors import ToolError
from mixassist.fl import protocol

log = logging.getLogger(__name__)

# FL's mixer.getCurrentTempo() returns BPM * 1000 (observed: 130000.0 at 130 BPM, FL API 45).
# Device scripts installed before that fix pass it through; real tempos stay far below this.
TEMPO_X1000_ABOVE = 1000.0


class FLClient:
    def __init__(self, transport, backend: str):
        self.transport = transport
        self.backend = backend
        self._next_id = 0
        self._lock = threading.RLock()
        self._warned_tempo_x1000 = False
        self.undo_history: list[dict] = []  # fl_set_track changes, newest last (in memory only)

    def _new_id(self) -> int:
        self._next_id = self._next_id % 999999 + 1
        return self._next_id

    def request(self, op: str, args: dict | None = None, timeout: float = 1.0) -> dict:
        with self._lock:
            msg_id = self._new_id()
            try:
                frame = protocol.encode(msg_id, op, args or {})
            except ValueError as exc:
                raise ToolError("FL_ERROR", f"cannot encode {op}: {exc}")
            self.transport.send_sysex(frame)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError(
                        "FL_TIMEOUT",
                        f"FL did not answer {op!r} within {timeout:.1f} s",
                        hint="check that FL Studio is running with the MixAssist device script enabled",
                    )
                reply = self.transport.recv_sysex(remaining)
                if reply is None:
                    continue
                try:
                    reply_id, reply_op, reply_args = protocol.decode(reply)
                except ValueError:
                    log.debug("dropping undecodable frame %r", reply[:32])
                    continue
                if reply_id != msg_id:
                    log.debug("dropping stale reply id %s (want %s)", reply_id, msg_id)
                    continue
                if reply_op == "err":
                    raise ToolError("FL_ERROR", f"FL rejected {op!r}: {reply_args.get('msg', 'unknown error')}")
                if op == "transport":
                    self._normalize_bpm(reply_args)
                return reply_args

    def _normalize_bpm(self, reply: dict) -> None:
        bpm = reply.get("bpm")
        if not isinstance(bpm, (int, float)) or bpm <= TEMPO_X1000_ABOVE:
            return
        reply["bpm"] = bpm / 1000.0
        if not self._warned_tempo_x1000:
            self._warned_tempo_x1000 = True
            log.warning(
                "FL reported bpm=%g, the getCurrentTempo() x1000 form; using %g BPM. "
                "The installed MixAssist device script is outdated: copy fl_script/MixAssist/device_MixAssist.py "
                "into FL's Settings/Hardware/MixAssist folder and reload it.",
                bpm, reply["bpm"],
            )

    def sync(self) -> None:
        """Push pending writes through: FL applies a write only after a later op (spec §3.4 delayed readback)."""
        self.request("hello")

    def note(self, status: int, d1: int, d2: int) -> None:
        self.transport.send_short(status & 0xFF, d1 & 0x7F, d2 & 0x7F)

    def now(self) -> float:
        clock = getattr(self.transport, "clock", None)
        return clock() if callable(clock) else time.monotonic()

    def sleep(self, seconds: float) -> None:
        sleeper = getattr(self.transport, "sleep", None)
        if callable(sleeper):
            sleeper(seconds)
        elif seconds > 0:
            time.sleep(seconds)

    def connected(self) -> bool:
        try:
            self.request("hello", timeout=0.5)
            return True
        except ToolError:
            return False

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()


class DisconnectedTransport:
    """Placeholder used when the bridge can't open its ports: every op fails with FL_NOT_CONNECTED."""

    def __init__(self, error: ToolError):
        self.error = error

    def send_sysex(self, frame: bytes) -> None:
        raise ToolError(self.error.code, self.error.message, self.error.candidates, self.error.hint)

    def recv_sysex(self, timeout: float) -> bytes | None:
        return None

    def send_short(self, status: int, d1: int, d2: int) -> None:
        raise ToolError(self.error.code, self.error.message, self.error.candidates, self.error.hint)

    def close(self) -> None:
        pass
