# Real-FL probe checklist (spec §5 P1–P8, §8.3, §8.4)

**Status: no probe on this page has been run on real FL Studio.** One real-FL observation is recorded under P1 (tempo units). Every result row says "not run" until a person runs the step on the target machine and fills it in. Don't copy expected values into the results columns.

Everything the backend assumes about real FL that isn't in the API stubs is marked PROBE in the code. These checks confirm or replace those assumptions:

- `fl_script/MixAssist/device_MixAssist.py`: frame cap `MAX_FRAME` (P2), `getCurrentTempo()` ×1000 units (P1, observed on real FL), undo order (§8.3)
- `mixassist/fl/curves.py`: volume and EQ tables (P3, P4)
- `mixassist/tools/fl_tools.py`: readback delay (P5)

## Where to record results

1. Fill in the results table at the bottom of this file: date, result, and the measured numbers.
2. Paste the raw console output under "Raw logs" for that probe.
3. Commit this file. W4 copies the status column into `evals/report.md`.
4. If a probe fails, apply the fallback in its section and note the commit that does it.

Record once per session: OS version, FL version (Help > About), FL API version (printed by P1), python-rtmidi version (`.venv/bin/python -c "import rtmidi; print(rtmidi.__version__)"`).

## What's on this Mac (Oct 25, 2026; observed, not probed)

- FL Studio: `/Applications/FL Studio 2026.app`. Its bundled Python is 3.12 (`Contents/Resources/FL/Shared/Python`).
- User hardware scripts folder exists: `~/Documents/Image-Line/FL Studio/Settings/Hardware/`. It has Image-Line's bundled scripts in it. The MixAssist script has **not** been copied there.
- `python -m mixassist.fl.bridge --list` showed no MIDI ports at all, so the IAC Driver is offline or has no ports. Do setup step 1 first.
- Run every command below from a normal Terminal in the repo root. Inside the Cursor agent sandbox, creating a CoreMIDI client aborts the Python process. That is a sandbox restriction, not an FL result.

## Setup (once per machine)

### 1. Create the two virtual MIDI ports

macOS (IAC Driver):

1. Open **Audio MIDI Setup** (Applications > Utilities).
2. Choose **Window > Show MIDI Studio**.
3. Double-click **IAC Driver**.
4. Tick **Device is online**.
5. Under Ports, click **+** twice and rename the ports to exactly `MA to FL` and `MA from FL`.
6. Click Apply and close the window.

CoreMIDI shows them as `IAC Driver MA to FL` and `IAC Driver MA from FL`. The bridge matches by substring, so that works.

Windows (loopMIDI):

1. Install loopMIDI (Tobias Erichsen).
2. Create a port named `MA to FL` and another named `MA from FL`.
3. Keep loopMIDI running. Names like `MA to FL 1` are fine.

Check that Python sees the ports:

```bash
.venv/bin/python -m mixassist.fl.bridge --list
```

The output list should contain `MA to FL`, and the input list should contain `MA from FL`.

### 2. Install the device script (don't skip this; nothing is installed yet)

macOS:

```bash
mkdir -p ~/Documents/Image-Line/FL\ Studio/Settings/Hardware/MixAssist
cp fl_script/MixAssist/device_MixAssist.py ~/Documents/Image-Line/FL\ Studio/Settings/Hardware/MixAssist/
```

Windows: copy `fl_script\MixAssist\device_MixAssist.py` to `%USERPROFILE%\Documents\Image-Line\FL Studio\Settings\Hardware\MixAssist\`.

Copy the file again after any change to the script, then reload it in FL (step 3, or restart FL).

### 3. Enable the script in FL

1. Open **Options > MIDI Settings**.
2. In the **Input** list, select `MA to FL` (IAC: `IAC Driver MA to FL`). Tick **Enable**, set **Controller type** to `MixAssist` (user scripts may be listed with a "(user)" suffix), and set **Port** to a free number, for example `10`.
3. In the **Output** list, select `MA from FL` and set its **Port** to the same number (`10`). `device.midiOutSysex` sends to the output port linked by port number [ST:device/__device.py getPortNumber].
4. Leave the input `MA from FL` disabled and the output `MA to FL` with no port. Otherwise FL hears its own replies (the loopback that ADR-1 avoids).
5. Open **View > Script output** and choose the MixAssist tab. You should see `MixAssist: loaded, API <n>, port 10`. If it says `no output port linked`, redo step 3.

Use a scratch project for P3–P6 and P8. Those probes move faders, play, and record. P3, P4 and P5 restore the values they change, but don't run them on a project you care about.

## Probes

Commands print measurements and the pass criterion. They never decide pass or fail; you do. Default ports are `MA to FL` and `MA from FL` (override with `--to-port` and `--from-port`). The default reply timeout is 1 s (`--timeout`).

### P1: script loads, hello round trip

Steps: finish setup, then run

```bash
.venv/bin/python -m mixassist.fl.bridge --check
.venv/bin/python -m mixassist.fl.bridge --probe p1
MIXASSIST_REALFL=1 .venv/bin/python -m pytest tests/test_bridge.py -m realfl -s
```

Record: median, max and min round trip; FL API version; script port; the `bpm` value from `--probe p6` or `fl_get_mix_overview` (it should equal the project tempo).

**Observed on FL API 45, Oct 25 2026: `getCurrentTempo()` returned 130000 for 130 BPM.** This is a real-FL observation (user's FL Studio over the IAC bridge, `GET /api/mix` showed `"bpm": 130000.0`), not a full P1 run. The stubs only say `getCurrentTempo(asInt=False) -> float`, "current tempo", with no units; the value matches the `REC_Tempo` storage form (`1000 * tempo`). Fix: the device script divides values above 1000 by 1000 (`TEMPO_X1000_ABOVE`), and `FLClient` does the same for any `transport` reply, logging one warning, so an outdated installed script still gives true BPM. `FakeFL(tempo_x1000=True)` mimics the raw value in tests. When P1 is run, check that `bpm` now equals the project tempo, including a fractional tempo such as 128.5.

- **Pass:** reply in under 100 ms, and api ≥ 35 logged.
- **Fallback:** API < 35: disable `eq_*`, and F5 proposes level and pan only. The device already answers `eq_get`/`eq_set` with `err` below 35. No reply at all: check Script output for errors, then vendor rosasynthesiz's bridge (MIT).

### P2: a 1024-byte reply reaches the server

The probe-only device op `pad` pads a reply to an exact size. No tool uses it.

```bash
.venv/bin/python -m mixassist.fl.bridge --probe p2
```

Record: the result for 256, 512, 768, 1000 and 1024 B ("intact", "corrupted", or the error).

- **Pass:** intact at 1024 B.
- **Fallback:** lower `MAX_FRAME` in `device_MixAssist.py` and `MAX_FRAME` in `mixassist/fl/protocol.py` (Core) to the largest intact size, and send fewer tracks per `tracks` op.

### P3: fader curve

Sets `setTrackVolume` from 0 to 1 in 0.05 steps on one track and reads `getTrackVolume(i,1)` one op later. The fader is restored afterwards. Pick an unused insert, for example 50.

```bash
.venv/bin/python -m mixassist.fl.bridge --probe p3 --track 50
```

Record: the printed `VOLUME_TABLE = [...]`, whether it's monotonic, and the dB at 0.8.

- **Pass:** monotonic, and 0.8 → 0.0 ±0.1 dB.
- **Either way:** paste the table into `mixassist/fl/curves.py` (Core). An unexpected shape means inverting by search only.

### P4: built-in EQ

Dumps `getEqBandCount()` and each band's gain, frequency and width, then sets band 0 gain to 0, 0.25, 0.75 and 1 with readback. The band is restored afterwards.

```bash
.venv/bin/python -m mixassist.fl.bridge --probe p4 --track 50
```

Record: band count, the Hz of each band in order, and the gain range in dB (from the readback at g=0 and g=1).

- **Pass:** 3 bands, ordered low < mid < high in Hz.
- **Fallback:** a different order means mapping bands by sorted Hz in `fl_tools.py`. If the EQ is missing, use the P1 fallback.

### P5: same-op vs next-op readback

Writes the fader with `set`, then reads it on the next three ops. The fader is restored afterwards.

```bash
.venv/bin/python -m mixassist.fl.bridge --probe p5 --track 50
```

Record: the three readback values and whether each shows the new value. The op table can't read within the same op as the write, so P5 checks next-op only.

- **Pass:** the next op returns the new value.
- **Fallback:** add a 1-op delay plus retry ×3. FakeFL already models a 1-op delay (`FLClient.sync()`).

### P6: peaks while playing

Load a project where at least one track makes sound, then run

```bash
.venv/bin/python -m mixassist.fl.bridge --probe p6 --seconds 5
```

It presses play, polls `peaks` for tracks 0–31 as fast as replies come back, then stops.

Record: polls per second, and the tracks with a peak above 0 (with the max value).

- **Pass:** > 0 on an active track, and ≥ 20 polls/s for 32 tracks.
- **Fallback:** poll fewer tracks per tick, and report `poll_interval_ms`.

### P7: piano roll MCP_Apply, then re-run with Ctrl+Alt+Y (MIDI worker)

1. Generate the script (it writes `MCP_Apply.pyscript` into `~/Documents/Image-Line/FL Studio/Settings/Piano roll scripts/`):

   ```bash
   .venv/bin/python -c "import json; from mixassist.tools import call_tool; print(json.dumps(call_tool('fix_midi', {'path': '$PWD/fixtures/midi/melody_c_major_3wrong.mid', 'fix': 'snap_to_scale'}), indent=1)[:1500])"
   ```

2. In FL, open a channel's piano roll and choose **Scripting > MCP Apply**. The fixed melody should appear.
3. Rewrite the file with a different fix:

   ```bash
   .venv/bin/python -c "import json; from mixassist.tools import call_tool; print(call_tool('fix_midi', {'path': '$PWD/fixtures/midi/sloppy.mid', 'fix': 'quantize', 'grid_beats': 0.125})['pyscript_path'])"
   ```

4. With the piano roll focused, press **Ctrl+Alt+Y**. Note which shortcut works on macOS, since it may differ.

Record: whether step 2 applied the notes, whether step 4 applied the new notes, and the shortcut used.

- **Pass:** the second run applies the new notes.
- **Fallback:** the user runs it from the menu each time. Update the `user_action` text.

### P8: notes reach the selected channel and record

1. Open a scratch project in song mode at 120 BPM.
2. Add an instrument on channel rack index 0, and route it to a mixer track.
3. Run

   ```bash
   .venv/bin/python -m mixassist.fl.bridge --probe p8 --channel 0
   ```

   It selects the channel and opens the piano roll, then turns recording on and plays from tick 0. After one bar it sends 8 eighth notes (C major scale) on the bridge port, then sends CC123, stops, and turns recording off.
4. Check the piano roll for 8 notes starting at bar 2.
5. Repeat with FL **unfocused** (click another app first, run the command from Terminal). Optionally repeat with `--no-record` to hear live play-through only.

Record: notes heard (focused and unfocused), notes recorded (focused and unfocused), and whether any were missing or stuck.

- **Pass:** recorded; the unfocused behaviour is noted.
- **Fallback:** require FL to be focused, with a warning in F8.

## §8.3 Real-FL read-back checks (run after P1–P5 and P8)

Use the bridge backend through the real tools:

```bash
export SOUNDPILOT_FL=bridge
```

1. **F1 mixer values.** Set 3 tracks to distinct levels and pans in FL, then run

   ```bash
   .venv/bin/python -c "import json; from mixassist.tools import call_tool; print(json.dumps(call_tool('fl_get_mix_overview', {}), indent=1))"
   ```

   Compare `volume_db` and `pan_pct` with FL's mixer hint bar (hover the fader or knob). **Pass:** within ±0.1 dB on 3 tracks.
2. **F2 change and undo.** Name a track `Kick` at 0.0 dB, then run

   ```bash
   .venv/bin/python -c "import json; from mixassist.tools import call_tool; r = call_tool('fl_set_track', {'track': 'Kick', 'volume_change_db': -2}); print(json.dumps(r, indent=1)); print(json.dumps(call_tool('fl_undo', {'change_id': r.get('change_id')}), indent=1))"
   ```

   Check FL's hint after the set (−2.0 ±0.25 dB, `verified: true`) and after the undo (0.0 ±0.1 dB). Also press Ctrl+Z (Cmd+Z) in FL after a set: the device saves the undo point `MixAssist` *before* writing (as Image-Line's bundled scripts do). Confirm that FL's undo restores the previous value. PROBE.
3. **F8 recorded onsets.** At 120 BPM, record `scale_8.mid` into channel 0 at bar 2:

   ```bash
   .venv/bin/python -c "import json; from mixassist.tools import call_tool; print(json.dumps(call_tool('fl_play_midi', {'path': '$PWD/fixtures/midi/scale_8.mid', 'channel': 0, 'record': True}), indent=1))"
   ```

   Export the pattern from the piano roll (Export as MIDI file) to `/tmp/rec.mid`, then measure the onset error against an eighth-note grid:

   ```bash
   .venv/bin/python - /tmp/rec.mid 120 <<'EOF'
   import sys, mido
   mf = mido.MidiFile(sys.argv[1]); bpm = float(sys.argv[2]); tpb = mf.ticks_per_beat; grid = tpb / 2
   ons, t = [], 0
   for msg in mido.merge_tracks(mf.tracks):
       t += msg.time
       if msg.type == "note_on" and msg.velocity > 0:
           ons.append(t)
   err = sorted(abs(x - round(x / grid) * grid) / tpb * 60000 / bpm for x in ons)
   p95 = err[min(len(err) - 1, int(round(0.95 * (len(err) - 1))))]
   print(f"{len(ons)} notes, p95 onset error {p95:.1f} ms, max {err[-1]:.1f} ms")
   EOF
   ```

   **Pass:** 8 notes, and p95 within ±20 ms.

## §8.4 Loudness cross-check (Youlean)

For each of 3 real exported mixes (WAV, 24-bit):

1. Measure integrated LUFS and true peak in Youlean Loudness Meter 2 (free) or FL's Wave Candy loudness view.
2. Run

   ```bash
   .venv/bin/python -c "import json,sys; from mixassist.tools import call_tool; r = call_tool('analyze_audio', {'path': sys.argv[1]}); print(r.get('integrated_lufs'), r.get('true_peak_dbtp'))" /abs/path/mix.wav
   ```

3. **Pass:** agreement within ±0.3 LU integrated and ±0.3 dB true peak on all 3 mixes.

## Results

| Check | Date | Result | Measured | Fallback applied |
| --- | --- | --- | --- | --- |
| P1 script loads, hello < 100 ms, api ≥ 35 | | not run | | |
| P2 1024-byte reply intact | | not run | | |
| P3 fader curve monotonic, 0.8 → 0.0 ±0.1 dB | | not run | | |
| P4 EQ 3 bands, low < mid < high Hz | | not run | | |
| P5 next-op readback | | not run | | |
| P6 peaks > 0 while playing, ≥ 20 polls/s × 32 | | not run | | |
| P7 MCP_Apply then Ctrl+Alt+Y re-run | | not run | | |
| P8 notes reach selected channel and record (focused / unfocused) | | not run | | |
| §8.3 F1 volume_db vs mixer hint ±0.1 dB on 3 tracks | | not run | | |
| §8.3 F2 −2 dB ±0.25, undo ±0.1 dB, FL Ctrl+Z restores | | not run | | |
| §8.3 F8 recorded onsets p95 ±20 ms | | not run | | |
| §8.4 Youlean ±0.3 LU / ±0.3 dBTP on 3 mixes | | not run | | |

Session info: OS: not recorded. FL version: not recorded. FL API: not recorded. python-rtmidi: not recorded.

## Raw logs

Paste the console output for each probe here, under a heading with the probe ID and date.
