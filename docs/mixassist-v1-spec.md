# FL Mixing Assistant v1: build spec
Status: frozen for the 4-hour build. Change a contract only by editing this file and telling every workstream. Background and rationale: [fl-mixing-assistant-feasibility.md](fl-mixing-assistant-feasibility.md). Where tool names differ, this spec replaces that doc's MVP tool list. In tables, \| inside JSON means a literal |. Nothing here has been run on real FL yet. Items marked PROBE are checked in §5 before feature work depends on them.

0. Sources (cited as [tag])
Tag	Source
[ST:path]	IL-Group FL-Studio-API-Stubs @ 9b4c7fa, https://github.com/IL-Group/FL-Studio-API-Stubs/blob/9b4c7fa8fd2ac62af3478cdb2faa29f3f7c04e4c/src/midi_controller_scripting/<path> (piano roll: src/piano_roll_scripting/flpianoroll/<path>)
[ST-DOC]	same repo, docs/midi_controller_scripting/index.md: unhandled notes are processed by FL normally
[MAN-EXP]	https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/fformats_save_export.htm (command line /R /E /O /M)
[MAN-PR]	https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/pianoroll_scripting_api.htm
[PF]	https://github.com/synopsys0/postfader-fl-studio-mcp/blob/main/docs/fl-constraints.md (pickup mode, same-tick readback, no files/sockets)
[RS-EQ] [RS-NB]	rosasynthesiz/flstudio-mcp (MIT): src/fl_studio_mcp/music/eq_curves.py, docs/NOTE_BRIDGE_SETUP.md, src/fl_studio_mcp/pyscript_gen.py
[BY]	https://github.com/Boyan253/fl-studio-2025-ai-bridge/blob/main/docs/FL2025_SANDBOX.md (FL 25.2.5 sandbox, pyscript file I/O blocked)
[BS]	ITU-R BS.1770-4 (2015): K-weighting, −0.691 offset, 400 ms blocks with 75% overlap, gates at −70 LKFS and −10 LU, true peak by 4× oversampling with a 12.04 dB attenuation guard
[E41] [E42]	EBU Tech 3341 (2023) Table 1 and Tech 3342 Table 1: https://tech.ebu.ch/docs/tech/tech3341.pdf, https://tech.ebu.ch/docs/tech/tech3342.pdf, signals at http://tech.ebu.ch/loudness
[U1]–[U5]	User wording: [U1] reddit.com/r/FL_Studio/comments/hiar0q ("thin and brittle… hi-hats and cymbals… harsh"). [U2] …/1k62szv ("muddy", "boxy build-up" 200–500 Hz). [U3] …/y5rt9m ("sounds very out of key", "notes are clashing"). [U4] …/ew24z5 ("sound so robotic and unnatural"). [U5] forum.image-line.com/viewtopic.php?t=49716 ("sounds drowning out one another")
1. Architecture and workstreams
ollmcp (HIL on) -> gemma4:31b-cloud (Ollama cloud) -> MCP stdio -> mixassist server (Python 3.11+)
  analysis/ (numpy, scipy, soundfile, pyloudnorm), midi/ (mido), complaints/ (lexicon + rules)
  fl/bridge.py (python-rtmidi) <-SysEx + notes-> loopMIDI "MA to FL" / "MA from FL" <-> FL device_MixAssist.py
  fl/fake.py implements the same op table in memory (§3.4) and is used by every test
Workstream	Owns	Hands off
W1 FL bridge	fl_script/device_MixAssist.py, fl/bridge.py, fl/fake.py, probes P1–P6, P8	op table §3.3 working on fake by 0:45
W2 Analysis	analysis/, complaints/, fixtures fixtures/audio/	analyze_audio, diagnose_complaint, verify_fix
W3 MIDI	midi/, piano roll generator, fixtures fixtures/midi/, probe P7	analyze_midi, fix_midi, fl_play_midi scheduler
W4 Ollama/eval	server.py registration, system prompt, ollmcp config, eval suite, MCP Inspector, listening test	demo script
Repo layout: mixassist/{server.py, errors.py, fl/, analysis/, complaints/, midi/}, fl_script/MixAssist/device_MixAssist.py, fixtures/, tests/, evals/.

2. Conventions (all tools)
Units in tool I/O are only these: dB (gain, level), dBFS (sample peak), dBTP, LUFS, LU, Hz, beats (quarter notes), seconds, percent (−100…100 for pan and width). Normalized 0–1 values never leave fl/.
Mixer track index: 0 = master. trackCount() includes master and the "current" track [ST:mixer/__properties.py]; v1 excludes the last index ("current").
Channel index: always global (useGlobalIndex=True) [ST:channels/__properties.py].
track arguments accept an int index or a name. Names match case-insensitively: exact first, then unique prefix. Several matches give AMBIGUOUS_TARGET.
Every write passes pickup mode 0 (midi.PIM_None = 0) [ST:midi/__pickup_modes.py], [PF]. It verifies on a later op, never the same one [PF].
File paths are absolute. Analysis results get an analysis_id (sha1 of path, mtime and options, first 12 hex characters), cached in memory.
3. Frozen interface contracts
3.1 Error format (every tool, on failure)
{"type":"object","required":["error"],"properties":{"error":{"type":"object","required":["code","message"],
 "properties":{"code":{"enum":["FL_NOT_CONNECTED","FL_TIMEOUT","FL_ERROR","NOT_FOUND","AMBIGUOUS_TARGET",
 "OUT_OF_RANGE","UNSUPPORTED","FILE_ERROR","NEEDS_CLARIFICATION","VERIFY_FAILED"]},
 "message":{"type":"string"},"candidates":{"type":"array","items":{"type":"string"}},"hint":{"type":"string"}}}}}
Errors come back as an MCP tool result with isError: true and this JSON as text. Never raise through the transport.

3.2 MCP tools (10). Input is JSON Schema; output lists required fields
T = {"oneOf":[{"type":"integer","minimum":0},{"type":"string","minLength":1}]} (track reference).

Tool (feature)	Input schema	Output (required fields)
fl_get_mix_overview (F1)	{"type":"object","properties":{"include_empty":{"type":"boolean","default":false}}}	fl_api:int, bpm:number, tracks:[{index:int, name:str, volume_db:number, pan_pct:number, stereo_width_pct:number, muted:bool, solo:bool, eq:[{band:"low"|"mid"|"high", gain_db:number, freq_hz:number}], plugins:[{slot:int, name:str}]}]
fl_set_track (F2, F5)	{"type":"object","required":["track"],"properties":{"track":T,"volume_db":{"type":"number","minimum":-60,"maximum":5},"volume_change_db":{"type":"number","minimum":-24,"maximum":12},"pan_pct":{"type":"number","minimum":-100,"maximum":100},"stereo_width_pct":{"type":"number","minimum":-100,"maximum":100},"eq_band":{"enum":["low","mid","high"]},"eq_gain_db":{"type":"number","minimum":-12,"maximum":12},"eq_freq_hz":{"type":"number","minimum":20,"maximum":20000}}}	change_id:str, track:{index,name}, before:{…same keys}, after:{…readback}, verified:bool
fl_undo (F2)	{"type":"object","properties":{"change_id":{"type":"string"}}} (default: last)	change_id, restored:{…}, verified:bool
fl_watch_peaks (F3)	{"type":"object","properties":{"seconds":{"type":"number","minimum":2,"maximum":300,"default":30},"start_playback":{"type":"boolean","default":true},"from_bar":{"type":"integer","minimum":1,"default":1}}}	polls:int, poll_interval_ms:number, tracks:[{index,name,max_peak_dbfs:number|null, clip_count:int}], clipping:[index], suggestions:[{track:int, volume_change_db:number, reason:str}]
analyze_audio (F4)	{"type":"object","required":["path"],"properties":{"path":{"type":"string"},"reference_path":{"type":"string"}}}	analysis_id, duration_s, sample_rate, integrated_lufs, short_term_max_lufs, lra_lu, true_peak_dbtp, sample_peak_dbfs, crest_db, correlation_mean, correlation_p5, side_mid_db, centroid_hz, bands_db:{"20-60","60-150","150-500","500-2k","2k-5k","5k-10k","10k-20k"}, third_octave_db:[{hz,db}], reference?:{analysis_id, lufs_diff, band_diff_db:{…same keys}}
diagnose_complaint (F5)	{"type":"object","required":["complaint","analysis_id"],"properties":{"complaint":{"type":"string"},"analysis_id":{"type":"string"},"track":T}}	needs_clarification:bool, question?:str, options?:[str], findings:[{descriptor:str, value:number, target:number, delta:number, unit:str}], proposed_changes:[{tool:"fl_set_track", args:{…}, why:str}]
verify_fix (F6)	{"type":"object","required":["complaint","before_analysis_id","after_path"],"properties":{"complaint":{"type":"string"},"before_analysis_id":{"type":"string"},"after_path":{"type":"string"}}}	passed:bool, after_analysis_id, checks:[{descriptor, before, after, rule:str, passed:bool}]
analyze_midi (F7)	{"type":"object","required":["path"],"properties":{"path":{"type":"string"},"track":{"type":"integer","minimum":0}}}	key:{tonic:str, mode:"major"|"minor", confidence:number}, note_count, out_of_key:[{index, pitch, beat}], timing:{grid_beats:number, median_offset_ms, max_offset_ms}, velocity:{min,max,std}, density_notes_per_beat:number, flags:[str]
fix_midi (F7)	{"type":"object","required":["path","fix"],"properties":{"path":{"type":"string"},"fix":{"enum":["snap_to_scale","quantize","humanize"]},"key":{"type":"string","pattern":"^[A-G][#b]? (major|minor)$"},"grid_beats":{"enum":[0.125,0.25,0.5,1]},"strength_pct":{"type":"number","minimum":0,"maximum":100,"default":100}}}	fixed_mid_path, pyscript_path, changed:[{index, before:{pitch,beat,velocity}, after:{…}}], user_action:str, reanalysis:{…analyze_midi output}
fl_play_midi (F8)	{"type":"object","properties":{"path":{"type":"string"},"notes":{"type":"array","maxItems":5000,"items":{"type":"object","required":["pitch","start_beat","length_beats"],"properties":{"pitch":{"type":"integer","minimum":0,"maximum":127},"start_beat":{"type":"number","minimum":0},"length_beats":{"type":"number","exclusiveMinimum":0},"velocity":{"type":"integer","minimum":1,"maximum":127,"default":100}}}},"channel":{"type":"integer","minimum":0},"record":{"type":"boolean","default":false},"start_bar":{"type":"integer","minimum":2,"default":2}},"oneOf":[{"required":["path"]},{"required":["notes"]}]}	mode:"live"|"record", bpm, notes_sent, duration_s, timing_lateness_ms:{mean,p95,max}, channel:{index,name}, warnings:[str]
3.3 SysEx wire format (server ↔ device script)
Frame: F0 7D 4D 41 <payload ASCII> F7. 7D is the non-commercial manufacturer ID, 4D 41 = "MA". FL drops SysEx without F0…F7 [ST:device/__device.py midiOutSysex]. Each message is at most 1024 bytes (PROBE P2).
Payload: <id>|<op>|k=<t><v>;k=<t><v>, where id is 1–999999 and t is i int, f float, b 0/1, s string (percent-escaped UTF-8), or n null. The reply reuses id, with op ok or err (msg=s…).
Ops. All values stay normalized here; the server converts. "Readback" means read on the next op [PF].
op	args	reply	FL calls
hello	–	api, port	general.getVersion() [ST:general/__fl_state.py], device.getPortNumber() [ST:device/__device.py]
tracks	start, count (≤8)	per track k: nK, vK (norm), vdK (dB), pK, sK, mK, soK	mixer.getTrackName/getTrackVolume(i)/getTrackVolume(i,1)/getTrackPan/getTrackStereoSep/isTrackMuted/isTrackSolo [ST:mixer/__tracks.py]
fx	track	sK, nK for loaded slots 0–9	mixer.isTrackPluginValid(i,s) [ST:mixer/__events.py], plugins.getPluginName(i,s) [ST:plugins/init.py]
eq_get	track	gB, gdB, fB, fhB, wB for B in 0..2	mixer.getEqBandCount/getEqGain(i,b[,1])/getEqFrequency(i,b[,1])/getEqBandwidth (API ≥35) [ST:mixer/__eq.py]
set	track, optional v, p, s (norm/−1…1)	ok	mixer.setTrackVolume(i,v,0), setTrackPan(i,p,0), setTrackStereoSep(i,s,0) [ST:mixer/__tracks.py]; general.saveUndo("MixAssist",0) [ST:general/__undo.py]
eq_set	track, band, optional g, f, w (norm)	ok	mixer.setEqGain/setEqFrequency/setEqBandwidth [ST:mixer/__eq.py]
peaks	start, count (≤32)	pK = max(L,R) linear (1.0 = 0 dBFS)	mixer.getTrackPeaks(i,2) [ST:mixer/__tracks.py]
transport	cmd ∈ play, stop, rec_on, rec_off, pos; optional pos_ticks	playing, recording, pos_ticks, loop_mode, bpm, ppq; optional queued (changes waiting for OnIdle), qerr (why a queued change was dropped)	transport.start/stop/record/isPlaying/isRecording/setSongPos(t,2)/getSongPos(2)/getLoopMode [ST:transport/init.py]; mixer.getCurrentTempo() [ST:mixer/__properties.py] (returns BPM × 1000 on FL API 45; the script reports true BPM); general.getRecPPQ(). Changes FL refuses in OnSysEx ("Operation unsafe at current time", observed for transport.record() on FL API 45), or any change when the event lacks PME_System [ST:midi/__pme_flags.py], are queued in order and run from OnIdle; the reply's state is read before they run.
channels	start, count (≤16)	total, nK	channels.channelCount(True), getChannelName(i,True)
select	channel	ok	channels.selectOneChannel(i,True) [ST:channels/__properties.py]; ui.showWindow(3) (widPianoRoll) [ST:ui/__windows.py]
The device script handles SysEx only in OnSysEx and sets msg.handled = True [ST:callbacks/init.py], [ST:fl_classes].
Note and CC messages are left unhandled, so FL plays and records them on the selected channel [ST-DOC].
No threads, sockets or files [BY], [PF]. The file is ASCII-only [PF].
OnIdle only runs queued transport changes (see the transport row). PostFader runs all its commands from OnIdle on FL 2026 [PF], but OnIdle may not fire on some FL 2025 builds [BY], so the server confirms every transport change by a later transport pos readback and never trusts the reply to the change itself.
3.4 Fake FL backend (fl/fake.py), used by every test
class FakeFL: handle(frame: bytes) -> bytes | None implements the same op table. note(status, d1, d2, t) records notes when recording and playing.
FakeFL(tracks: list[dict], eq_curves=..., vol_curve=..., peak_script={track: [(t_s, linear)]}).
Quirks it must reproduce (tests fail if code relies on their absence):
Pickup: any setter given pickupMode != 0 is ignored when the change is more than 0.05 normalized [PF].
Delayed readback: a write takes effect only after the next op is handled. A get in the same op returns the old value [PF].
Built-in EQ is exactly 3 bands. band 3 or more gives err. Its normalized↔dB/Hz mapping is a monotonic table, not a formula, so the server must invert it by search.
Volume curve: a monotonic table with 0.8 → 0.0 dB [ST:mixer/__tracks.py, "default value is 0.8"]. Other points are placeholders until P3 replaces them.
Peaks are instantaneous and sampled at poll time from peak_script. Peaks between polls are invisible.
Transport clock: pos_ticks = start + (now − t_start) · ppq · bpm/60 + 5 ms lag. In pattern mode, the position wraps at the pattern length.
Optional tempo_x1000=True: the transport reply's bpm is BPM × 1000, the raw getCurrentTempo() value on real FL. FLClient divides any bpm above 1000 by 1000.
Optional transport_unsafe=N: the next N transport changes reply err "transport failed: RuntimeError: Operation unsafe at current time" (an outdated script on real FL). Optional transport_defer_ops=N: changes apply only after N more ops and replies carry queued (the current script waiting for OnIdle); a huge N means OnIdle never fires.
4. Decision records
ADR-1 Transport: SysEx over two loopMIDI or IAC ports, handled event-driven in OnSysEx.
Why: FL 2025 controller scripts can't use sockets or threads, and OnIdle may not fire [BY]. PostFader ships SysEx in production [PF].
Exception: FL refused transport.record() inside OnSysEx on FL API 45 ("Operation unsafe at current time"), so refused transport changes are queued for OnIdle (§3.3 transport row).
Two ports avoid loopback feedback.
Rejected: TCP, file-RPC, Flapi (unmaintained), the FL 2026 built-in MCP (reverse-engineered).
ADR-2 Piano roll write path: the server generates MCP_Apply.pyscript with notes baked in, in …/Settings/Piano roll scripts.
The user runs it from the Scripting menu once per session, then presses Ctrl+Alt+Y, which re-reads the file [RS-NB].
Why: the controller API can't edit the score [PF]; pyscript file I/O is blocked on 25.2.5 [BY], [RS-NB]; no API runs a pyscript [RS-NB].
Notes are read from a user-exported .mid file.
ADR-3 Built-in EQ only: EQ actions use the mixer's built-in 3-band EQ (API ≥35) [ST:mixer/__eq.py].
Why: every track has it, while plugins can't be inserted from a script [PF] and plugin parameter curves vary by build [RS-EQ].
Fruity Parametric EQ 2 is v2.
ADR-4 Offline analysis: loudness, spectrum and stereo come from user-exported WAVs.
Why: scripting exposes no audio buffers or rendering [PF]. The command-line render FL.exe /R /Ewav works only on saved projects [MAN-EXP].
Live meters (getTrackPeaks) are used only for clipping (F3).
5. Probe tickets (W1 and W3 run these on the target FL build in hour 1; log results to internal/)
ID	Check	Pass	Fallback if it fails
P1	Script loads, hello round trip	reply < 100 ms, api ≥ 35 logged	API < 35: disable eq_*, F5 proposes level/pan only. No reply at all: vendor rosasynthesiz's bridge (MIT)
P2	1024-byte reply reaches the server	intact at 1024 B	lower the per-message cap (fewer tracks per tracks op)
P3	Fader curve: setTrackVolume 0…1 in 0.05 steps, read getTrackVolume(i,1)	monotonic, 0.8 → 0.0 ±0.1 dB	write the table into fl/curves.py either way; unexpected shape means search-only inversion
P4	EQ: getEqBandCount(), Hz per band, gain range, set/readback	3 bands ordered low < mid < high in Hz	other order: map by sorted Hz; missing: as P1 fallback
P5	Same-op readback vs next-op readback	next-op returns the new value	add a 1-op delay plus retry ×3
P6	getTrackPeaks(i,2) while playing	> 0 on an active track; ≥ 20 polls/s for 32 tracks	poll fewer tracks per tick, report poll_interval_ms
P7	MCP_Apply from the menu, then Ctrl+Alt+Y after rewriting the file	the second run applies the new notes	the user runs it from the menu each time
P8	Notes on the script's port reach the selected channel and record when rec_on+play; repeat with FL unfocused	recorded; unfocused noted	require FL focused (warning in F8)
6. Features
Every feature has the same definition of done:

unit tests pass on FakeFL and fixtures;
tool visible in MCP Inspector with a valid schema;
one demo step run in ollmcp with Gemma, transcript saved to evals/demos/.
F1 Mix overview (W1)
Story: "As a beginner, I ask what's going on in my mix and get every used track's level, pan, width, EQ and plugins in plain units."
Calls: hello, tracks (batches of 8), fx, eq_get (§3.3).
Acceptance:
Given FakeFL with 12 named tracks and 115 default tracks named Insert N (no plugins, volume 0.8), when called with include_empty=false, then it returns 13 tracks (master + 12) with volume_db within ±0.1 dB of the fake's table, in < 0.5 s.
Given real FL (P3 done), volume_db matches the dB shown in FL's mixer hint within ±0.1 dB on 3 tracks.
Limits / non-goals: a snapshot, not change events. Plugin names only, no presets or parameters. "Current" track excluded.
Depends on: §3.3, P1–P4.
Demo: "What's on my mixer?"
F2 Change by name, with undo (W1)
Story: "I say kick down 2 dB or hats 30% left and it happens, reads back, and can be undone."
Calls: set, then the next-op tracks readback. Volume dB→normalized by bisection on the P3 table, then a readback search on getTrackVolume(i,1) until within ±0.25 dB (≤ 8 round trips).
Acceptance:
Given track "Kick" at 0.0 dB, when volume_change_db=-2, then after.volume_db = −2.0 ±0.25 and verified=true.
When fl_undo runs, then the volume returns to 0.0 ±0.1 dB.
Given tracks "Hat Open" and "Hat Closed", when track:"hat", then AMBIGUOUS_TARGET with both names.
When pan_pct=-30, then the readback is −30 ±1.
volume_db=+9 gives OUT_OF_RANGE (schema max 5; true max from P3).
Limits: fader, pan and stereo separation only. Undo history is in memory and lost on restart. Edits the user makes in between aren't detected, so undo restores the stored value anyway.
Depends on: F1. Demo: "Turn the kick down 2 dB", then "undo that".
F3 Clipping and headroom watch (W1 + W2 rules)
Story: "I press play and it tells me which tracks clip or run too hot, with trims to fix it."
Calls: transport play from from_bar, peaks in batches of 32 every ≥ 50 ms for seconds, then transport stop. Peak (1.0 = 0 dBFS, > 1.0 = clipping) [ST:mixer/__tracks.py] is converted with dBFS = 20·log10(p).
Rules:
clip_count = polls with p > 1.0.
Clipping or max_peak_dbfs > −1 on a non-master track gives a suggestion volume_change_db = −6 − max_peak_dbfs, rounded to 0.5.
Master > −1 dBFS gives a suggestion to trim the loudest contributing tracks, not the master.
Acceptance:
Given a fake track with a 200 ms burst at 1.12 linear (+0.98 dBFS), when watched for 10 s at 50 ms polls, then clipping contains it and max_peak_dbfs ≥ +0.9.
A track peaking at 0.5 (−6.0 dBFS) is not flagged.
The suggestion for +0.98 dBFS is −7.0 dB.
Limits: sample peak, not true peak, not LUFS. Transients shorter than the poll interval can be missed, and the output says so. Uses the real transport; it doesn't render.
Depends on: F1, P6. Demo: "Is anything clipping?"
F4 Mix check from a WAV (W2)
Story: "I export my mix, optionally with a reference track, and get loudness, peaks, balance and width as numbers."
Method:
soundfile reads; stereo is required for the correlation fields.
Integrated, short-term and LRA loudness use pyloudnorm per [BS] (gates −70/−10, 400 ms blocks at 75% overlap), with LRA per [E42].
True peak: resample_poly ×4 then max abs [BS Annex 2].
Bands: Welch PSD (Hann, 8192 points) integrated per band. Third-octave bands use IEC centres 25 Hz–16 kHz.
Correlation: Pearson per 400 ms window; report the mean and 5th percentile.
side_mid_db = 10·log10(E[S²]/E[M²]).
Crest = sample peak dBFS − RMS dBFS.
Reference: the reference is loudness-matched to the mix before computing band_diff_db.
Acceptance: fixture results in §7 within the stated tolerances, and Youlean cross-check within ±0.3 LU / ±0.3 dB (§8).
Limits:
Whole-file averages; no per-section analysis in v1. Mono files have correlation_* = null.
Under 3 s gives short_term_max_lufs=null. Over 15 min gives OUT_OF_RANGE.
Depends on: nothing. Demo: "Analyze mix.wav against ref.wav."
F5 Complaint → fix (W2 lexicon and rules, W4 prompt)
Story: "I say it's too bright or it's muddy and get the measured cause and a concrete change to approve."
Lexicon (complaints/lexicon.yaml; synonyms drawn from [U1]–[U5]):
Descriptor	Phrases (non-exhaustive)	Measure (vs reference, else vs a line fitted to third_octave_db 50 Hz–16 kHz)	Proposed change
bright	too bright, brittle, tinny, piercing, sizzly	mean excess of 5k-10k and 10k-20k ≥ 2 dB	eq_band=high, eq_gain_db = −clamp(excess, 2, 6), eq_freq_hz=8000 on the master or track
harsh	harsh, harsh hi-hats/cymbals, ear-piercing	2k-5k excess ≥ 2 dB	eq_band=mid, eq_freq_hz=3500, eq_gain_db = −clamp(excess,2,5)
muddy	muddy, boxy, cloudy, cluttered, drowning out	150-500 excess ≥ 2 dB	eq_band=mid, eq_freq_hz=300, eq_gain_db = −clamp(excess,2,4)
boomy	boomy, too much bass, woofy	60-150 excess ≥ 2 dB	eq_band=low, eq_gain_db = −clamp(excess,2,6)
thin	thin, weak, no body, no meat	mean deficit of 60-150 and 150-500 ≥ 2 dB	eq_band=low, eq_gain_db = +clamp(deficit,1,4)
loud	too loud, distorted, clipping, crunchy	true_peak_dbtp > −1, or crest < 6 dB	volume_change_db = −(true_peak + 1) on the loudest tracks (F3 data if present)
quiet	too quiet, quieter than other songs	reference.lufs_diff < −2 LU	user guidance only (no limiter in v1)
narrow / phasey	narrow, mono, hollow, phasey	correlation_mean > 0.95 / correlation_p5 < 0	stereo_width_pct +30 / −30 on track
Clarification: needs_clarification=true when any of these holds:
no lexicon match;
2 or more descriptors match with the top excess less than 1.5× the second;
a track is required but missing, for width changes. It returns one question and 2–4 options built from the findings, e.g. "Is it the highs (+4.1 dB at 5–20 kHz) or the low mids (+2.6 dB)?". Writes are never proposed alongside a question.
Acceptance:
Given bright.wav with pink.wav as reference, when "it's too bright", then the top finding is bright with delta 6 ±1 dB and a proposed eq_gain_db in [−6, −4].
Given pink.wav alone, when "it sounds bad", then needs_clarification=true with no proposals.
Given muddy.wav, when "sounds boxy", then the top finding is muddy.
Limits:
3-band EQ: no high-pass or narrow notches.
Locates problems on the master or a named track only; no stem masking in v1.
Excess thresholds are heuristics; §8 validates them.
Depends on: F4, plus F2 for applying. Demo: "My mix is too bright", then approve the change.
F6 Before/after verify (W2)
Story: "After re-exporting, I ask did that help? and get a pass or fail from measurements, not opinions."
Rules:
The after file is loudness-matched to before for spectral checks.
Pass when the complaint's descriptor moves the right way by at least 50% of the proposed change (e.g. a −4 dB high cut needs a high-band drop ≥ 2.0 dB), and |ΔLUFS| ≤ 1.0 LU unless the complaint was loud or quiet, and true peak doesn't rise above −1 dBTP.
Acceptance:
Given bright.wav and bright_fixed.wav (shelf −5 dB), then passed=true.
Given bright.wav twice, then passed=false with code VERIFY_FAILED.
Given bright_fixed_loud.wav (+3 LU), then passed=false on the loudness check.
Limits: needs a re-export. The thresholds aren't perceptual.
Depends on: F4, F5. Demo: "Did the brightness fix work?"
F7 MIDI wrong-note and timing fix (W3)
Story: "My melody sounds out of key or robotic. It finds the bad notes or stiff timing and fixes them in my piano roll."
Method:
The user exports the pattern (piano roll menu → Export as MIDI file); mido parses it.
Key: Krumhansl–Kessler profiles × duration-weighted pitch-class histogram. Report the best of 24 keys and confidence = Pearson r.
Out of key: pitch class not in the scale (natural minor for minor keys).
snap_to_scale: move each out-of-key note to the nearest scale tone, ties toward the previous note's direction.
quantize: onset → nearest grid × strength.
humanize: onset ± uniform(0, 10 ms) and velocity ± uniform(0, 12), with a fixed seed of 7.
Output: a new .mid plus a generated MCP_Apply.pyscript (template in [RS-NB]/pyscript_gen.py) that calls flp.score.clearNotes(True) (positional on 25.2.5, per [RS-NB]), then flp.score.addNote(Note) for each note, using number, time, length, velocity in ticks from flp.score.PPQ [ST:flpianoroll/__score.py, __note.py], [MAN-PR].
user_action text: "In FL: open this channel's piano roll → Scripting → MCP Apply (first time this session), afterwards press Ctrl+Alt+Y."
Acceptance:
Given melody_c_major_3wrong.mid (32 notes, 3 planted F#/C#/G#), then key = C major or A minor, and out_of_key holds those 3 indices.
When snap_to_scale, the reanalysis has 0 out-of-key and changed holds exactly 3 notes, each moved 1 semitone.
Given robotic.mid (all velocities 100, all on grid), then flags contains robotic (velocity std < 3 and median offset < 1 ms).
When humanize, velocity std ≥ 6 and median abs offset 2–10 ms.
Given sloppy.mid (±40 ms jitter on a 1/8 grid), when quantize at 100%, the median offset ≤ 1 tick.
The generated pyscript passes py_compile, and against a fake flpianoroll module it writes exactly the fixed notes.
Limits: one pattern or channel per file. No chord or clash analysis in v1. Destructive, so the HIL approval in ollmcp is required. The note write can't be confirmed automatically (ADR-2). Swing or triplet material gets misread unless grid_beats is set.
Depends on: P7. Demo: "This melody sounds off" with melody.mid, then "fix the wrong notes".
F8 Play and record MIDI into the selected instrument (W3 scheduler, W1 ops)
Story: "Play this phrase or .mid on my synth, or record it into the piano roll."
Method:
select (if channel), then transport to read bpm and ppq. Beats are converted to seconds at FL's tempo. A bpm outside 10..999 is FL_ERROR before any note is sent: an un-normalized getCurrentTempo() value (BPM×1000) would make every note ~1000× too short to sound. Notes shorter than 10 ms at FL's tempo add a warning.
Live: send note on/off messages on the "MA to FL" port at their times.
Record:
transport rec_on, pos_ticks=(start_bar−2)·4·ppq, play. A transport err that says "unsafe" is retried once after 0.1 s, then FL_ERROR with a hint (update the script, focus FL, close dialogs, or arm Record by hand).
Poll transport pos until it reads recording and playing (≤ 1 s). Otherwise FL_ERROR before any note: CC123, stop and rec_off are still sent, and the message includes queued / qerr.
During the one-bar pre-roll, run 6 pos polls and fit ticks = a + rate·t using the round-trip midpoint, taking the median of a.
Send each note when FL reaches its tick, then send note-off for every sounding note plus CC123, and stop plus rec_off.
Live note routing to the selected channel: [ST-DOC]. Precise sleep: sleep until 2 ms before the target, then spin.
Acceptance:
Given FakeFL at 120 BPM, PPQ 96, when 64 notes are recorded, then FakeFL's recorded ticks are within ±10 ms of the targets at p95 and none are missing.
After the call returns, sounding notes = 0.
Given real FL (P8), when an 8-note scale is recorded and exported to .mid, onsets are within ±20 ms of the grid at p95.
Loop mode 0 (pattern) produces a warning in warnings.
Limits:
One instrument per take.
Constant tempo, 4/4 assumed.
A few ms of jitter, so quantize after recording.
Pattern mode loops at the pattern length.
May require FL to be focused (P8).
On Windows it needs loopMIDI.
Depends on: P1, P8. Demo: "Play melody.mid on the selected synth and record it at bar 2."
7. Test fixtures (W2: fixtures/audio/, W3: fixtures/midi/; generated by fixtures/make.py, 48 kHz, 24-bit, seed 1)
File	Content	Expected
ebu3341_01…05.wav	[E41] cases 1–5 (1 kHz stereo at −23/−33 dBFS, gated tone sequences)	integrated_lufs −23.0 or −33.0 ±0.1
ebu3341_15…23.wav	[E41] true-peak cases (fs/4, fs/6, fs/8 tones; inter-sample peak cases 20–23)	true_peak_dbtp −6.0 / +3.0 / 0.0 within +0.2/−0.4
ebu3342_01…04.wav	[E42] tone pairs (−20/−30, −20/−15, −40/−20, five-step sequence)	lra_lu 10 / 5 / 20 / 15 ±1
pink.wav	30 s stereo pink noise, independent L/R, −20 dBFS RMS	third-octave bands flat ±1 dB from 63 Hz–16 kHz; abs(correlation_mean) < 0.05
bright.wav / bright_fixed.wav / bright_fixed_loud.wav	pink plus a +6 dB high shelf at 6 kHz / then −5 dB at 8 kHz / then +3 dB gain	F5, F6 criteria
muddy.wav, boomy.wav, thin.wav	pink +6 dB bell at 300 Hz / +6 dB bell at 100 Hz / −6 dB low shelf at 250 Hz	top finding muddy / boomy / thin
mono.wav, antiphase.wav	L = R / L = −R pink	correlation_mean 1.00 / −1.00 ±0.01
clip_track (FakeFL peak script)	a 200 ms burst at 1.12	F3 criteria
melody_c_major_3wrong.mid, robotic.mid, sloppy.mid	as in F7	F7 criteria
scale_8.mid	C major, 8 eighth notes	F8 recording test
The official EBU signal files should be used where available (download from [E41] ref [7]). Synthesised versions can stand in, with the same expected values.

8. External validation (W4 owns; reported in evals/report.md)
MCP Inspector: npx @modelcontextprotocol/inspector python -m mixassist --fake.
All 10 tools list, each schema validates, and each tool is called once with valid and once with invalid arguments.
Invalid calls must return the §3.1 error, not a crash.
Gemma tool-call suite: evals/gemma_suite.py sends 20 prompts to Ollama /api/chat with the 10 tool schemas at temperature 0, model gemma4:31b-cloud (Ollama cloud; no local model), against FakeFL.
Prompts are those in the feasibility doc §4, item 7, with tool names mapped to §3.2.
Pass: ≥ 16/20 correct tool with arguments satisfying their predicates, 0 invented tool names, and 0 write calls after a needs_clarification=true result.
Also record the Vertex model gemma-4-26b-a4b-it-maas for comparison. Local Ollama models are not used.
Real-FL read-back: on the target build, F1 and F2 values match FL's mixer hints for 3 tracks, and F8 recorded onsets meet the ±20 ms p95 criterion.
Loudness cross-check: 3 real exported mixes measured in Youlean Loudness Meter 2 (free) or FL's Wave Candy loudness view agree within ±0.3 LU integrated and ±0.3 dB true peak.
Blind listening test (≥ 5 producers, 15 minutes):
4 pairs: bright, muddy, clipping, wrong-note MIDI. Each pair is before vs after, loudness-matched, in random order, labelled A/B.
Each listener answers the complaint-specific question ("which is less bright?") and gives an overall preference.
Pass: ≥ 80% pick "after" on the specific question and ≥ 70% prefer "after" overall.
9. Work breakdown (4 hours, dependency-ordered)
Time	W1 FL bridge	W2 Analysis	W3 MIDI	W4 Ollama/eval
0:00–0:45	device script + bridge + FakeFL (§3.3–3.4); P1, P2, P5 on real FL	fixtures/make.py, F4 loudness + true peak against EBU fixtures	analyze_midi, MIDI fixtures	server skeleton, 10 tool stubs returning fake data; Inspector green; ollmcp + Gemma wired
0:45–1:00	Tracer milestone: "turn the kick down 2 dB" through Gemma → FL fader → readback		P7 on real FL	run the tracer
1:00–2:00	F1, F2 (P3, P4 tables), F3 (P6)	F4 bands, correlation, reference; F5 lexicon + rules	F7 fixers + pyscript generator	eval harness; system prompt (clarification rule)
2:00–3:00	F8 ops, P8; support F5 apply	F6; real-mix Youlean cross-check	F8 scheduler + clock sync on FakeFL, then real FL	full 20-prompt run; fix schema and description issues
3:00–3:40	real-FL read-back validation (§8.3)	threshold tuning on fixtures only	F7/F8 real-FL demo	listening-test clips, recorded demos
3:40–4:00	freeze	freeze	freeze	evals/report.md, README
Cut order if late: F8 record mode → F3 suggestions → F7 humanize → reference comparison in F4. F6 is never cut.

10. Hand-off self-test (run on F4)
A new agent given only this file can build F4 without asking questions, because it specifies:

the exact tool name and input/output schema (§3.2);
the units (§2) and the error format (§3.1);
the algorithm with parameters: BS.1770 gates, ×4 true-peak oversampling, Welch 8192-point Hann, band edges, 400 ms correlation windows, loudness-matched reference;
the input limits (stereo, 3 s, 15 min);
the fixtures with numeric expectations and tolerances (§7, citing [E41]/[E42]);
an external cross-check (§8.4);
the owner and time slot (§9);
the definition of done (§6).
No FL access is needed, because F4 has no dependencies.

Open gaps, deliberately left to probes rather than guessed:

the fader dB range and curve (P3);
built-in EQ band order and gain range (P4);
SysEx size limit (P2);
whether MIDI reaches FL when it's unfocused (P8).
