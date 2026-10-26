# Internal interfaces (swarm contract)

Several workers build the backend in parallel. These Python interfaces are the seams between them. The frozen tool contracts are in [mixassist-v1-spec.md](mixassist-v1-spec.md) §2–§3; the HTTP API and agent rules are in [build-brief.md](build-brief.md). If you must change anything here, change this file in the same commit and say so in your final report.

## Ownership

| Worker | Owns (only `git add` these paths) |
| --- | --- |
| Core | `pyproject.toml`, `mixassist/{__init__,__main__,errors,schemas,server,agent,api}.py`, `mixassist/tools/__init__.py`, `mixassist/tools/fl_tools.py`, `mixassist/fl/{__init__,protocol,client,fake,curves,tracks}.py`, `mixassist/llm/`, `tests/conftest.py`, `tests/test_{protocol,fake,f1,f2,f3,schemas,converter,agent,api,mcp_server}*.py`, `evals/` (later phase), `README.md` |
| Analysis | `mixassist/analysis/`, `mixassist/complaints/`, `mixassist/tools/audio_tools.py`, `fixtures/make.py`, `fixtures/__init__.py`, `tests/test_{f4,f5,f6,fixtures}*.py` |
| MIDI | `mixassist/midi/`, `mixassist/tools/midi_tools.py`, `fixtures/make_midi.py`, `fixtures/midi/*.mid`, `tests/test_{f7,f8,pyscript}*.py` |
| Bridge | `mixassist/fl/bridge.py`, `fl_script/MixAssist/device_MixAssist.py`, `internal/probes.md`, `tests/test_{bridge,device_script}*.py` |
| UI | `web/` |

Never `git add -A` or `git add .`. Retry if `.git/index.lock` exists. Don't push.

## Errors (`mixassist/errors.py`, exists)

`ToolError(code, message, candidates=None, hint=None)` with `.to_dict()` → §3.1 JSON. `error_dict(...)`, `is_error(result)`. Tool functions raise `ToolError`; they never return error dicts themselves and never let other exceptions escape (the dispatcher converts stray exceptions to `FL_ERROR`, or `FILE_ERROR` for `OSError`/soundfile errors).

## Tool functions

Signature: `def tool_name(args: dict) -> dict`. The dispatcher has already validated `args` against the §3.2 schema and filled schema `default`s, and has coerced numeric strings for `track` to `int`. The function returns the §3.2 output dict (units only, per §2).

| Module | Functions |
| --- | --- |
| `mixassist/tools/fl_tools.py` (Core) | `fl_get_mix_overview`, `fl_set_track`, `fl_undo`, `fl_watch_peaks` |
| `mixassist/tools/audio_tools.py` (Analysis) | `analyze_audio`, `diagnose_complaint`, `verify_fix` |
| `mixassist/tools/midi_tools.py` (MIDI) | `analyze_midi`, `fix_midi`, `fl_play_midi` |

`mixassist/schemas.py` (Core) holds the 10 input schemas verbatim from §3.2 as `SCHEMAS: dict[str, dict]` plus a one-paragraph `DESCRIPTIONS: dict[str, str]` per tool (used by MCP and the LLM).

`mixassist/tools/__init__.py` (Core): `TOOLS: dict[str, Callable]`, `READ_TOOLS`, `WRITE_TOOLS`, and `call_tool(name: str, args: dict) -> dict` (validate with jsonschema → `OUT_OF_RANGE` for numeric bound violations, `NOT_FOUND` for an unknown tool, `FL_ERROR`/`OUT_OF_RANGE` with the validator message otherwise; apply defaults; call; convert `ToolError` → dict). `call_tool` never raises. Until a sibling module exists, Core may register a stub that raises `ToolError("UNSUPPORTED", ...)`.

The fl_play_midi top-level `oneOf` (path xor notes) is enforced inside `fl_play_midi` too (raise `OUT_OF_RANGE` with a clear message), because the Gemini converter drops it.

## FL access (`mixassist/fl/`, Core)

```python
from mixassist.fl import get_fl, set_fl   # process-wide active client
fl = get_fl()                              # -> FLClient

class FLClient:
    backend: str                           # "fake" | "bridge"
    def request(self, op: str, args: dict | None = None, timeout: float = 1.0) -> dict
        # one §3.3 op round trip; returns decoded reply args (normalized values, as in §3.3);
        # raises ToolError FL_NOT_CONNECTED / FL_TIMEOUT / FL_ERROR (err reply -> FL_ERROR with msg)
    def note(self, status: int, d1: int, d2: int) -> None
        # send a 3-byte short MIDI message (note on/off, CC) on the "MA to FL" path
    def now(self) -> float                 # time.monotonic() (or the fake's clock)
    def connected(self) -> bool
```

Transports (implement `send_sysex(frame: bytes)`, `recv_sysex(timeout: float) -> bytes | None`, `send_short(status, d1, d2)`, `close()`):

- `FakeTransport(fake: FakeFL)` in `mixassist/fl/fake.py` (Core): `send_sysex` calls `fake.handle(frame)` and queues the reply; `send_short` calls `fake.note(status, d1, d2, fake.clock())`.
- `BridgeTransport(to_port="MA to FL", from_port="MA from FL")` in `mixassist/fl/bridge.py` (Bridge): python-rtmidi, SysEx enabled on input (`ignore_types(sysex=False)`), port names matched by substring so macOS IAC ("IAC Driver MA to FL") and Windows loopMIDI ("MA to FL 1") both work. Raises `ToolError("FL_NOT_CONNECTED", ..., hint=...)` if ports are missing.

Codec (`mixassist/fl/protocol.py`, Core): `encode(msg_id: int, op: str, args: dict) -> bytes` and `decode(frame: bytes) -> tuple[int, str, dict]`, exactly §3.3 (`F0 7D 4D 41 … F7`, `id|op|k=<t><v>;…`, types `i f b s n`, `s` percent-escaped UTF-8, max 1024 bytes). The device script re-implements the same codec in ASCII-only Python with no imports beyond FL modules; `tests/test_device_script.py` (Bridge) checks both codecs agree.

## FakeFL (`mixassist/fl/fake.py`, Core)

```python
FakeFL(tracks: list[dict], eq_curves=None, vol_curve=None, peak_script: dict[int, list[tuple[float, float]]] | None = None,
       bpm: float = 120.0, ppq: int = 96, loop_mode: int = 1, channels: list[str] | None = None,
       api: int = 36, clock: Callable[[], float] = time.monotonic, pattern_beats: float = 16.0, port: int = 0,
       tempo_x1000: bool = False,   # tempo_x1000: transport reports bpm × 1000, like raw getCurrentTempo() on real FL
       transport_unsafe: int = 0,   # next N transport changes reply err "... Operation unsafe at current time" (outdated script)
       transport_defer_ops: int | None = None)  # changes apply N ops later, replies carry `queued` (script waiting for OnIdle)
fake.handle(frame: bytes) -> bytes | None
fake.note(status: int, d1: int, d2: int, t: float) -> None
fake.recorded_notes   # list of dicts {pitch, velocity, tick, t} recorded while recording and playing (note-ons)
fake.sounding_notes   # set of (channel, pitch) currently on; CC123 clears it
default_fake(**overrides) -> FakeFL   # demo mix: Master + 12 named tracks (Kick, Snare, Hat Open, Hat Closed, Bass, ...) + 115 "Insert N" defaults + current track
```

All §3.4 quirks are mandatory (pickup ignore > 0.05 when pickupMode != 0, delayed readback, exactly 3 EQ bands with table mappings, 0.8 → 0.0 dB volume table, instantaneous peaks from `peak_script` (time measured from transport `play`), transport clock with 5 ms lag and pattern-mode wrap). The fake's `transport` reply includes `pos_ticks`, `bpm`, `ppq`, `loop_mode`, `playing`, `recording`.

### Wire details (Core, implemented in `protocol.py` / `fake.py`; the device script must match)

- Payload is always `<id>|<op>|<args>` (trailing `|` present even with no args). String escaping: every UTF-8 byte outside 0x20..0x7E, plus `%` `|` `;` `=`, becomes `%XX` (uppercase hex); decoders accept any `%XX`. Floats use Python `repr`; non-finite floats are sent as `n`. Replies are `ok`/`err` with the request's id.
- Per-index reply keys (`tracks`, `peaks`, `channels`) use the ABSOLUTE index as K (`n12`, `v12`, …), not the offset within the batch.
- `tracks` and `channels` replies also include `total` (`mixer.trackCount()` incl. master and the current track / `channels.channelCount(True)`).
- `fx` reply: for each loaded slot K, `sK=b1` and `nK=s<name>`.
- `set` / `eq_set` accept an optional `pk` (pickup mode, default 0). The device script always passes 0 to FL; the fake uses it to emulate the pickup quirk.
- `transport cmd=pos` without `pos_ticks` is a pure read; with `pos_ticks` it sets the position. `play` with `pos_ticks` sets the position, then starts.
- `transport` replies may add `queued` (int: changes the device script queued for OnIdle because FL called them unsafe in OnSysEx) and `qerr` (str: why a queued change was dropped). Callers change the transport through `mixassist/fl/playback.py` (Core): `transport(fl, cmd, pos_ticks=None)` retries once on an "unsafe" err and then raises `FL_ERROR` with a hint; `wait_transport(fl, timeout_s, playing=..., recording=...)` polls `transport pos` until the readback matches; `describe(reply)` formats the readback plus `queued` / `qerr` for messages.
- `FLClient` also has `sync()` (one `hello` round trip so pending writes land; read back at least one op after it) and `sleep(seconds)` (advances a `VirtualClock` on the fake, real sleep otherwise). Use `fl.now()` / `fl.sleep()` in schedulers so tests can run on a virtual clock. `FakeFL(clock=VirtualClock())` is available in `mixassist/fl/fake.py`.
- `FakeFL.true_ticks(t)` is the real song position at time t (no 5 ms reporting lag); recorded notes use it. `fake.selected_channel` is set by `select`.

`mixassist/fl/curves.py` (Core): the placeholder volume and EQ tables plus `db_to_norm` / `norm_to_db` helpers (bisection on monotonic tables), marked PROBE P3/P4.

`mixassist/fl/tracks.py` (Core): `resolve_track(ref: int | str, tracks: list[dict]) -> dict` per §2 (exact case-insensitive, then unique prefix; `AMBIGUOUS_TARGET` with candidates; `NOT_FOUND`).

## Analysis cache (`mixassist/analysis/`, Analysis)

`analysis_id` = first 12 hex of sha1(path, mtime, options). `get_analysis(analysis_id) -> dict` returns the cached `analyze_audio` output (raises `ToolError("NOT_FOUND")`), used by `diagnose_complaint` and `verify_fix`.

`diagnose_complaint.proposed_changes[*]` = `{"tool": "fl_set_track", "args": {...fl_set_track args...}, "why": str}`. With no `track` argument, target the master (`"track": 0`). Never include proposals when `needs_clarification` is true.

## Fixtures

`fixtures/make.py` (Analysis): `python -m fixtures.make [--out fixtures]` writes `fixtures/audio/*.wav` (48 kHz, 24-bit, seed 1, §7) and then calls `fixtures.make_midi.make_all(out_dir / "midi")` if importable. WAVs are git-ignored; `tests/conftest.py` (Core) has a session fixture `fixtures_dir` that runs `fixtures.make` when files are missing. MIDI files (`fixtures/midi/*.mid`) are small and committed (MIDI worker).

## Agent / proposals (Core)

The agent and `/api/*` turn write calls into Proposals (shape in build-brief.md). `preview.before` comes from a fresh `fl_get_mix_overview` read.
