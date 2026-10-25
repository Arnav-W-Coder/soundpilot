import pytest

from mixassist.errors import ToolError
from mixassist.fl import protocol
from mixassist.fl.client import FLClient


def test_roundtrip_all_types():
    args = {"i": -3, "f": 0.8, "b": True, "b0": False, "s": "Hat|Open; a=b 100% ü", "n": None}
    frame = protocol.encode(42, "tracks", args)
    assert frame[:4] == bytes([0xF0, 0x7D, 0x4D, 0x41]) and frame[-1] == 0xF7
    assert all(b < 0x80 for b in frame[1:-1]), "SysEx data bytes must be 7-bit"
    assert protocol.decode(frame) == (42, "tracks", args)


def test_wire_format_literal():
    assert protocol.encode(7, "set", {"track": 1, "v": 0.5}) == b"\xf0\x7dMA7|set|track=i1;v=f0.5\xf7"
    assert protocol.encode(1, "hello", {}) == b"\xf0\x7dMA1|hello|\xf7"
    assert protocol.decode(b"\xf0\x7dMA1|ok\xf7") == (1, "ok", {})


def test_string_escaping():
    assert protocol.escape("a|b;c=d%e") == "a%7Cb%3Bc%3Dd%25e"
    assert protocol.escape("é") == "%C3%A9"
    assert protocol.unescape("%41%42c") == "ABc"


def test_non_finite_float_is_null():
    assert protocol.decode(protocol.encode(1, "ok", {"x": float("inf")}))[2] == {"x": None}


def test_max_frame_size():
    protocol.encode(1, "ok", {"s": "x" * 1000})
    with pytest.raises(ValueError):
        protocol.encode(1, "ok", {"s": "x" * 1100})


@pytest.mark.parametrize("bad", [b"", b"\xf0\x7dMA\xf7", b"\xf0\x00MA1|ok|\xf7", b"\xf0\x7dMA1|ok|x=q1\xf7", b"\xf0\x7dMA1|ok|"])
def test_decode_rejects_bad_frames(bad):
    with pytest.raises(ValueError):
        protocol.decode(bad)


class _Scripted:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send_sysex(self, frame):
        self.sent.append(frame)

    def recv_sysex(self, timeout):
        return self.replies.pop(0) if self.replies else None

    def send_short(self, *a):
        pass


def test_client_skips_stale_and_garbage_replies():
    client = FLClient(_Scripted([b"junk", protocol.encode(99, "ok", {"x": 1}), protocol.encode(1, "ok", {"api": 36})]), "fake")
    assert client.request("hello") == {"api": 36}


def test_client_err_reply_and_timeout():
    client = FLClient(_Scripted([protocol.encode(1, "err", {"msg": "nope"})]), "fake")
    with pytest.raises(ToolError) as e:
        client.request("hello")
    assert e.value.code == "FL_ERROR" and "nope" in e.value.message
    with pytest.raises(ToolError) as e:
        client.request("hello", timeout=0.05)
    assert e.value.code == "FL_TIMEOUT"
