# Demo: web tools (vertex)

- LLM provider: `vertex`, model `gemma-4-26b-a4b-it-maas`
- Started: 2026-10-26T00:42:38.714467+00:00
- Web backend: `ddgs`
- FL backend: FakeFL

## Checks

- PASS Gemma searched the web: `["web_search", "fl_play_midi"]`
- PASS at most 6 tool calls: `2`
- PASS reply states a key and a BPM: `key=C# major bpm=171 BPM`
- PASS reply cites a source: `'Blinding Lights' is in C# major at 171 BPM (Tunebat, songbpm.com). I've played a short arpeggio based on that key.`
- PASS stated key appears in a source text: `reply=C# major sources=[('A', '', 'maj'), ('C', '#', 'maj')]`
- PASS Gemma played an arpeggio live with fl_play_midi: `[{"start_bar": 2, "notes": [{"pitch": 61, "length_beats": 0.5, "start_beat": 0, "velocity": 100}, {"pitch": 65, "velocity": 90, "length_beats": 0.5, "start_beat": 0.5}, {"length_beats": 0.5, "pitch": 68, "velocity": 110, "start_beat": 1}, {`
- PASS all notes in C# major: `pitches=[61, 65, 68, 65, 61, 65, 68, 65] outside=[]`
- PASS no write proposals: `[]`
- PASS Gemma searched the web: `["web_search"]`
- PASS reply says -14 LUFS: `Spotify normalizes to -14 LUFS integrated on its default "Normal" setting (Spotify Support, Critical Listening Lab, Trackgleam, Soundcamps). For the best results, they recommend keeping your True Peak`
- PASS reply cites a source: `Spotify normalizes to -14 LUFS integrated on its default "Normal" setting (Spotify Support, Critical Listening Lab, Trackgleam, Soundcamps). For the best results, they recommend keeping your True Peak`
- PASS no write proposals

## Transcript

### POST /api/chat (7.39 s)

User: What key and tempo is 'Blinding Lights' by The Weeknd? Then play its main synth riff idea as a short arpeggio

Gemma: 'Blinding Lights' is in C# major at 171 BPM (Tunebat, songbpm.com). I've played a short arpeggio based on that key.

1. `web_search` `{"query": "Blinding Lights The Weeknd key and tempo"}`
   - [BPM and key for Blinding Lights by The Weeknd | Tempo for ...](https://songbpm.com/@the-weeknd/blinding-lights): Blinding Lights is a song by The Weeknd with a tempo of 171 BPM. It can also be used half-time at 86 BPM. The track runs 3 minutes and 22 seconds long with a ke
   - [BPM and key for Blinding Lights by The Weeknd | Tempo for ...](https://songbpm.com/@the-weeknd/blinding-lights-iWFrxW70EI): Blinding Lights is a somber song by The Weeknd with a tempo of 171 BPM. It can also be used half-time at 86 BPM. The track runs 3 minutes and 22 seconds long wi
   - [Key & BPM for Blinding Lights by The Weeknd | Tunebat](https://tunebat.com/Info/Blinding-Lights-The-Weeknd/0sf12qNH5qcw8qpgymFOqD): Nov 29, 2019 · “ Blinding Lights ” by The Weeknd is in C♯ major at 171 BPM, with a Camelot key of 3B. The tracks below are recommended for harmonic mixing based
   - [Song Key of Blinding Lights (The Weeknd) - GetSongKEY](https://getsongkey.com/song/blinding-lights/57633B): Find similar songs (100) that will sound good when mixed with Blinding Lights by The Weeknd. You'll find below a list of songs having similar tempos and adjacen
2. `fl_play_midi` `{"start_bar": 2, "notes": [{"pitch": 61, "length_beats": 0.5, "start_beat": 0, "velocity": 100}, {"pitch": 65, "velocity": 90, "length_beats": 0.5, "start_beat": 0.5}, {"length_beats": 0.5, "pitch": 68, "velocity": 110, "start_beat": 1}, {"velocity": 90, "start_beat": 1.5, "length_beats": 0.5, "pitc`
   - `{"mode": "live", "bpm": 120.0, "ppq": 96, "notes_sent": 8, "duration_s": 2.0, "timing_lateness_ms": {"mean": 0.048, "p95": 0.18, "max": 0.263}}`

### POST /api/chat (6.25 s)

User: What loudness does Spotify normalize to?

Gemma: Spotify normalizes to -14 LUFS integrated on its default "Normal" setting (Spotify Support, Critical Listening Lab, Trackgleam, Soundcamps). For the best results, they recommend keeping your True Peak below -2 dBTP to avoid distortion during encoding (Spotify Support).

1. `web_search` `{"query": "Spotify normalization loudness target LUFS"}`
   - [Loudness normalization on Spotify](https://support.spotify.com/us/artists/article/loudness-normalization/): If your master is louder than -14dB integrated LUFS, keep True Peak below -2dB to avoid extra distortion. Louder tracks are more susceptible to extra distortion
   - [Spotify Loudness Standard — Target LUFS & Normalization](https://www.criticallisteninglab.com/en/learn/loudness/spotify): With normalization on, Normal targets -14 LUFS; Premium listeners can also choose Loud (-11 LUFS) or Quiet (-19 LUFS). In Normal mode, quieter tracks can receiv
   - [Streaming Loudness Targets 2026 — Spotify, Apple Music LUFS ...](https://trackgleam.com/tools/streaming-loudness-targets/): The 2026 mastering loudness reference chart: every streaming platform's LUFS and true-peak target in one table. Spotify -14 LUFS, Apple Music -16 LUFS, YouTube,
   - [Spotify LUFS Analysis: How Loud Your Mix Should Be in 2026](https://soundcamps.com/blog/spotify-lufs/): Apr 7, 2026 · Spotify measures your track's integrated LUFS (loudness over the full duration of the file) and compares it to a target level. On the default "Nor
