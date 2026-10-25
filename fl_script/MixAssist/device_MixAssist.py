# name=MixAssist
# url=https://github.com/IL-Group/FL-Studio-API-Stubs
# supportedDevices=MixAssist
#
# MixAssist v1 device script (spec section 3.3).
#
# Talks to the MixAssist server over SysEx: requests arrive on the input port
# ("MA to FL"), replies leave through device.midiOutSysex on the linked output
# port ("MA from FL"). Only SysEx frames that start with F0 7D 4D 41 are
# handled. Notes and CC are left unhandled so FL plays and records them on the
# selected channel [ST-DOC].
#
# Constraints: ASCII only, no threads, sockets, files or OnIdle [BY] [PF].
# Everything is event-driven from OnSysEx. Values stay normalized here; the
# server converts to dB / Hz / percent.
#
# Install: copy this folder to
#   <Documents>/Image-Line/FL Studio/Settings/Hardware/MixAssist/
# then in FL: Options > MIDI Settings, enable input "MA to FL" with controller
# type "MixAssist" and a port number N, and set output "MA from FL" to the
# same port N. See internal/probes.md.

import mixer
import transport
import channels
import general
import device
import plugins
import ui

PREFIX = (0xF0, 0x7D, 0x4D, 0x41)
SYSEX_END = 0xF7

# PROBE P2: maximum frame size (F0 ... F7 inclusive). Lower it if P2 fails.
MAX_FRAME = 1024
MAX_ERR_MSG = 200

# midi.PIM_None [ST:midi/__pickup_modes.py]; FL's bundled midi.py does not
# export it, so the literal is used.
PIM_NONE = 0
# ui.showWindow index widPianoRoll [ST:ui/__windows.py]
WID_PIANO_ROLL = 3
# transport.getSongPos / setSongPos mode SONGLENGTH_ABSTICKS [ST:transport/init.py]
SONGLENGTH_ABSTICKS = 2
# mixer.getTrackPeaks mode 2 = max of left and right [ST:mixer/__tracks.py]
PEAK_MAX_LR = 2
# general.saveUndo flag UF_None [ST:general/__undo.py]
UF_NONE = 0
# Built-in EQ functions exist from API 35 [ST:mixer/__eq.py] (PROBE P1/P4)
EQ_MIN_API = 35

MAX_TRACKS_PER_OP = 8
MAX_PEAKS_PER_OP = 32
MAX_CHANNELS_PER_OP = 16
MAX_FX_SLOTS = 10

_SAFE = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
_HEX = "0123456789ABCDEF"


# ---------------------------------------------------------------- codec ----

def _esc(text):
    out = []
    for b in text.encode("utf-8"):
        if b in _SAFE:
            out.append(chr(b))
        else:
            out.append("%" + _HEX[b >> 4] + _HEX[b & 15])
    return "".join(out)


def _unesc(text):
    raw = bytearray()
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "%":
            hx = text[i + 1:i + 3].upper()
            if len(hx) != 2 or hx[0] not in _HEX or hx[1] not in _HEX:
                raise ValueError("bad percent escape")
            raw.append(_HEX.index(hx[0]) * 16 + _HEX.index(hx[1]))
            i += 3
        else:
            raw.extend(c.encode("utf-8"))
            i += 1
    return raw.decode("utf-8", "replace")


def _enc_value(v):
    if v is None:
        return "n"
    if v is True or v is False:
        return "b1" if v else "b0"
    if isinstance(v, int):
        return "i%d" % v
    if isinstance(v, float):
        if v != v or v - v != 0.0:
            return "n"  # non-finite (e.g. -inf dB at silence) goes as null
        return "f" + repr(v)
    if isinstance(v, str):
        return "s" + _esc(v)
    raise ValueError("cannot encode %s" % type(v).__name__)


def _dec_value(tv):
    if not tv:
        raise ValueError("empty value")
    t = tv[0]
    v = tv[1:]
    if t == "i":
        return int(v)
    if t == "f":
        return float(v)
    if t == "b":
        if v == "1":
            return True
        if v == "0":
            return False
        raise ValueError("bad bool %r" % v)
    if t == "s":
        return _unesc(v)
    if t == "n":
        return None
    raise ValueError("unknown type %r" % t)


def encode(msg_id, op, args=None):
    pairs = []
    if args:
        for k in args:
            pairs.append(k + "=" + _enc_value(args[k]))
    payload = "%d|%s|%s" % (msg_id, op, ";".join(pairs))
    return bytes(PREFIX) + payload.encode("ascii") + bytes((SYSEX_END,))


def is_ours(data):
    return len(data) >= 5 and tuple(data[:4]) == PREFIX


def _payload(data):
    data = bytes(data)
    if not is_ours(data) or data[-1] != SYSEX_END:
        raise ValueError("not a MixAssist frame")
    body = data[4:-1]
    for b in body:
        if b > 0x7F:
            raise ValueError("payload is not 7-bit ASCII")
    return body.decode("ascii")


def decode(data):
    parts = _payload(data).split("|", 2)
    if len(parts) < 2:
        raise ValueError("payload needs id|op")
    msg_id = int(parts[0])
    if msg_id < 1 or msg_id > 999999:
        raise ValueError("id out of range")
    op = parts[1]
    if not op:
        raise ValueError("empty op")
    args = {}
    if len(parts) == 3 and parts[2]:
        for item in parts[2].split(";"):
            if not item:
                continue
            k, sep, tv = item.partition("=")
            if not sep or not k:
                raise ValueError("bad pair %r" % item)
            args[k] = _dec_value(tv)
    return msg_id, op, args


# ------------------------------------------------------------- helpers -----

class OpError(Exception):
    pass


def _int_arg(args, key, default=None):
    v = args.get(key, default)
    if v is None:
        raise OpError("missing arg %s" % key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise OpError("arg %s must be an int" % key)
    return int(v)


def _num_arg(args, key, lo, hi):
    if key not in args or args[key] is None:
        return None
    v = args[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise OpError("arg %s must be a number" % key)
    v = float(v)
    if v < lo or v > hi:
        raise OpError("arg %s=%s outside %s..%s" % (key, v, lo, hi))
    return v


def _usable_tracks():
    # trackCount() includes master (0) and the "current" track (last index);
    # v1 excludes "current" [ST:mixer/__properties.py].
    return mixer.trackCount() - 1


def _track_arg(args):
    t = _int_arg(args, "track")
    if t < 0 or t >= _usable_tracks():
        raise OpError("track %d out of range 0..%d" % (t, _usable_tracks() - 1))
    return t


def _range_args(args, limit):
    start = _int_arg(args, "start", 0)
    count = _int_arg(args, "count", limit)
    if start < 0:
        raise OpError("start must be >= 0")
    if count < 1 or count > limit:
        raise OpError("count must be 1..%d" % limit)
    return start, count


def _require_eq():
    if general.getVersion() < EQ_MIN_API:
        raise OpError("built-in EQ needs API >= %d" % EQ_MIN_API)


# ----------------------------------------------------------------- ops -----

def op_hello(args):
    return {"api": int(general.getVersion()), "port": int(device.getPortNumber())}


def op_tracks(args):
    start, count = _range_args(args, MAX_TRACKS_PER_OP)
    # total is the raw trackCount(); the server drops the last ("current")
    # index itself. Per-track keys are only sent for usable tracks.
    out = {"total": int(mixer.trackCount())}
    for i in range(start, min(start + count, _usable_tracks())):
        k = str(i)
        out["n" + k] = str(mixer.getTrackName(i))
        out["v" + k] = float(mixer.getTrackVolume(i))
        out["vd" + k] = float(mixer.getTrackVolume(i, 1))
        out["p" + k] = float(mixer.getTrackPan(i))
        out["s" + k] = float(mixer.getTrackStereoSep(i))
        out["m" + k] = bool(mixer.isTrackMuted(i))
        out["so" + k] = bool(mixer.isTrackSolo(i))
    return out


def op_fx(args):
    t = _track_arg(args)
    out = {}
    for s in range(MAX_FX_SLOTS):
        if mixer.isTrackPluginValid(t, s):
            k = str(s)
            out["s" + k] = True
            out["n" + k] = str(plugins.getPluginName(t, s))
    return out


def op_eq_get(args):
    _require_eq()
    t = _track_arg(args)
    bands = int(mixer.getEqBandCount())
    out = {"bands": bands}
    for b in range(bands):
        k = str(b)
        out["g" + k] = float(mixer.getEqGain(t, b))
        out["gd" + k] = float(mixer.getEqGain(t, b, 1))
        out["f" + k] = float(mixer.getEqFrequency(t, b))
        out["fh" + k] = float(mixer.getEqFrequency(t, b, 1))
        out["w" + k] = float(mixer.getEqBandwidth(t, b))
    return out


def op_set(args):
    # A "pk" arg from the server is accepted and ignored: writes always use
    # pickup mode 0 (spec section 2).
    t = _track_arg(args)
    v = _num_arg(args, "v", 0.0, 1.0)
    p = _num_arg(args, "p", -1.0, 1.0)
    s = _num_arg(args, "s", -1.0, 1.0)
    if v is None and p is None and s is None:
        raise OpError("set needs v, p or s")
    # Undo point before the edit, as FL's bundled scripts do (PROBE: confirm
    # Ctrl+Z restores the previous value, internal/probes.md section 8.3).
    general.saveUndo("MixAssist", UF_NONE)
    # Pickup mode 0 so FL applies jumps > 0.05 instead of waiting for pickup
    # [PF]. Readback happens on a later op, never this one [PF] (PROBE P5).
    if v is not None:
        mixer.setTrackVolume(t, v, PIM_NONE)
    if p is not None:
        mixer.setTrackPan(t, p, PIM_NONE)
    if s is not None:
        mixer.setTrackStereoSep(t, s, PIM_NONE)
    return {}


def op_eq_set(args):
    _require_eq()
    t = _track_arg(args)
    band = _int_arg(args, "band")
    bands = int(mixer.getEqBandCount())
    if band < 0 or band >= bands:
        raise OpError("band %d out of range 0..%d" % (band, bands - 1))
    g = _num_arg(args, "g", 0.0, 1.0)
    f = _num_arg(args, "f", 0.0, 1.0)
    w = _num_arg(args, "w", 0.0, 1.0)
    if g is None and f is None and w is None:
        raise OpError("eq_set needs g, f or w")
    if g is not None:
        mixer.setEqGain(t, band, g)
    if f is not None:
        mixer.setEqFrequency(t, band, f)
    if w is not None:
        mixer.setEqBandwidth(t, band, w)
    return {}


def op_peaks(args):
    start, count = _range_args(args, MAX_PEAKS_PER_OP)
    total = _usable_tracks()
    out = {}
    for i in range(start, min(start + count, total)):
        out["p" + str(i)] = float(mixer.getTrackPeaks(i, PEAK_MAX_LR))
    return out


def _transport_state():
    return {
        "playing": bool(transport.isPlaying()),
        "recording": bool(transport.isRecording()),
        "pos_ticks": int(transport.getSongPos(SONGLENGTH_ABSTICKS)),
        "loop_mode": int(transport.getLoopMode()),
        # PROBE P1: confirm getCurrentTempo() returns BPM as a float.
        "bpm": float(mixer.getCurrentTempo()),
        "ppq": int(general.getRecPPQ()),
    }


def op_transport(args):
    cmd = args.get("cmd")
    if cmd not in ("play", "stop", "rec_on", "rec_off", "pos"):
        raise OpError("cmd must be play, stop, rec_on, rec_off or pos")
    if args.get("pos_ticks") is not None:
        ticks = _int_arg(args, "pos_ticks")
        if ticks < 0:
            raise OpError("pos_ticks must be >= 0")
        transport.setSongPos(ticks, SONGLENGTH_ABSTICKS)
    # transport.start() toggles play/pause and transport.record() toggles
    # recording [ST:transport/init.py], so check the state first.
    if cmd == "play":
        if not transport.isPlaying():
            transport.start()
    elif cmd == "stop":
        transport.stop()
    elif cmd == "rec_on":
        if not transport.isRecording():
            transport.record()
    elif cmd == "rec_off":
        if transport.isRecording():
            transport.record()
    return _transport_state()


def op_channels(args):
    start, count = _range_args(args, MAX_CHANNELS_PER_OP)
    total = int(channels.channelCount(True))
    out = {"total": total}
    for i in range(start, min(start + count, total)):
        out["n" + str(i)] = str(channels.getChannelName(i, True))
    return out


def op_select(args):
    ch = _int_arg(args, "channel")
    total = int(channels.channelCount(True))
    if ch < 0 or ch >= total:
        raise OpError("channel %d out of range 0..%d" % (ch, total - 1))
    channels.selectOneChannel(ch, True)
    ui.showWindow(WID_PIANO_ROLL)
    return {}


def op_pad(args, msg_id):
    # Probe-only (P2): reply padded to exactly `size` bytes so the server can
    # test how large a reply survives the port. Not used by any tool.
    size = _int_arg(args, "size")
    base = len(encode(msg_id, "ok", {"pad": ""}))
    if size < base or size > MAX_FRAME:
        raise OpError("size must be %d..%d" % (base, MAX_FRAME))
    return {"pad": "x" * (size - base)}


OPS = {
    "hello": op_hello,
    "tracks": op_tracks,
    "fx": op_fx,
    "eq_get": op_eq_get,
    "set": op_set,
    "eq_set": op_eq_set,
    "peaks": op_peaks,
    "transport": op_transport,
    "channels": op_channels,
    "select": op_select,
    "pad": op_pad,
}


# ------------------------------------------------------------ dispatch -----

def _err(msg_id, text):
    return encode(msg_id, "err", {"msg": text[:MAX_ERR_MSG]})


def _salvage_id(data):
    try:
        head = _payload(data).split("|", 1)[0]
        msg_id = int(head)
    except (ValueError, IndexError):
        return None
    if 1 <= msg_id <= 999999:
        return msg_id
    return None


def handle_frame(data):
    """Return the reply frame for a request frame, or None to stay silent."""
    if not is_ours(data):
        return None
    if len(data) > MAX_FRAME:
        msg_id = _salvage_id(data)
        if msg_id is None:
            return None
        return _err(msg_id, "request is %d bytes, over the %d-byte cap" % (len(data), MAX_FRAME))
    try:
        msg_id, op, args = decode(data)
    except ValueError as e:
        msg_id = _salvage_id(data)
        if msg_id is None:
            return None
        return _err(msg_id, "bad frame: %s" % e)
    handler = OPS.get(op)
    if handler is None:
        return _err(msg_id, "unknown op %s" % op)
    try:
        if handler is op_pad:
            out = handler(args, msg_id)
        else:
            out = handler(args)
        reply = encode(msg_id, "ok", out)
    except OpError as e:
        return _err(msg_id, str(e))
    except Exception as e:
        return _err(msg_id, "%s failed: %s: %s" % (op, type(e).__name__, e))
    if len(reply) > MAX_FRAME:
        return _err(msg_id, "reply is %d bytes, over the %d-byte cap; request fewer items" % (len(reply), MAX_FRAME))
    return reply


# ----------------------------------------------------------- callbacks -----

def OnInit():
    try:
        print("MixAssist: loaded, API %d, port %d" % (general.getVersion(), device.getPortNumber()))
        if not device.isAssigned():
            print("MixAssist: no output port linked; set output 'MA from FL' to this port number")
    except Exception as e:
        print("MixAssist: init warning: %s" % e)


def OnDeInit():
    pass


def OnSysEx(msg):
    data = bytes(msg.sysex)
    if not is_ours(data):
        return
    msg.handled = True
    reply = handle_frame(data)
    if reply is not None:
        device.midiOutSysex(reply)
