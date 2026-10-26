# Demo: web tools (ollama)

- LLM provider: `ollama`, model `gemma4:31b-cloud`
- Started: 2026-10-26T00:42:53.149206+00:00
- Web backend: `ddgs`
- FL backend: FakeFL

## Checks

- PASS Gemma searched the web: `["web_search", "fl_play_midi"]`
- PASS at most 6 tool calls: `2`
- PASS reply states a key and a BPM: `key=C# major bpm=171 BPM`
- PASS reply cites a source: `'Blinding Lights' is in C# major at 171 BPM (Tunebat, songbpm.com). I've played a short C# major arpeggio (61, 65, 68, 72) to capture the synth riff idea.`
- PASS stated key appears in a source text: `reply=C# major sources=[('A', '', 'maj'), ('C', '#', 'maj')]`
- PASS Gemma played an arpeggio live with fl_play_midi: `[{"notes": [{"length_beats": 0.5, "pitch": 61, "start_beat": 0, "velocity": 110}, {"length_beats": 0.5, "pitch": 65, "start_beat": 0.5, "velocity": 90}, {"length_beats": 0.5, "pitch": 68, "start_beat": 1, "velocity": 100}, {"length_beats": `
- PASS all notes in C# major: `pitches=[61, 65, 68, 72, 68, 65, 61, 65] outside=[]`
- PASS no write proposals: `[]`
- PASS Gemma searched the web: `["web_search"]`
- PASS reply says -14 LUFS: `Spotify normalizes tracks to -14 LUFS (Loudness normalization on Spotify).`
- PASS reply cites a source: `Spotify normalizes tracks to -14 LUFS (Loudness normalization on Spotify).`
- PASS no write proposals

## Transcript

### POST /api/chat (8.12 s)

User: What key and tempo is 'Blinding Lights' by The Weeknd? Then play its main synth riff idea as a short arpeggio

Gemma: 'Blinding Lights' is in C# major at 171 BPM (Tunebat, songbpm.com). I've played a short C# major arpeggio (61, 65, 68, 72) to capture the synth riff idea.

1. `web_search` `{"query": "Blinding Lights The Weeknd key BPM"}`
   - [BPM and key for Blinding Lights by The Weeknd | Tempo for ...](https://songbpm.com/@the-weeknd/blinding-lights): Blinding Lights is a song by The Weeknd with a tempo of 171 BPM. It can also be used half-time at 86 BPM. The track runs 3 minutes and 22 seconds long with a ke
   - [Key & BPM for Blinding Lights by The Weeknd | Tunebat](https://tunebat.com/Info/Blinding-Lights-The-Weeknd/0sf12qNH5qcw8qpgymFOqD): Nov 29, 2019 · “ Blinding Lights ” by The Weeknd is in C♯ major at 171 BPM, with a Camelot key of 3B. The tracks below are recommended for harmonic mixing based
   - [BPM and key for Blinding Lights by The Weeknd | Tempo for ...](https://songbpm.com/@the-weeknd/blinding-lights-s70xz): Blinding Lights is a somber song by The Weeknd with a tempo of 171 BPM. It can also be used half-time at 86 BPM. The track runs 3 minutes and 22 seconds long wi
   - [Blinding Lights (The Weeknd) BPM and Key Finder | SFR BeatsThe Weeknd - Blinding Lights — BPM, Key & Camelot | ACAPELLAS.euBlinding Lights Key & BPM …](https://sfrbeats.com/tools/bpm/the-weeknd/blinding-lights): Find the accurate 85 BPM, Fm key, and scale for Blinding Lights by The Weeknd. Search, analyze, and key-match millions of tracks instantly. BPM accuracy is typi
2. `fl_play_midi` `{"notes": [{"length_beats": 0.5, "pitch": 61, "start_beat": 0, "velocity": 110}, {"length_beats": 0.5, "pitch": 65, "start_beat": 0.5, "velocity": 90}, {"length_beats": 0.5, "pitch": 68, "start_beat": 1, "velocity": 100}, {"length_beats": 0.5, "pitch": 72, "start_beat": 1.5, "velocity": 110}, {"leng`
   - `{"mode": "live", "bpm": 120.0, "ppq": 96, "notes_sent": 8, "duration_s": 2.0, "timing_lateness_ms": {"mean": 0.004, "p95": 0.006, "max": 0.006}}`

### POST /api/chat (5.68 s)

User: What loudness does Spotify normalize to?

Gemma: Spotify normalizes tracks to -14 LUFS (Loudness normalization on Spotify).

1. `web_search` `{"query": "Spotify loudness normalization target LUFS"}`
   - [Loudness normalization on Spotify](https://support.spotify.com/us/artists/article/loudness-normalization/): How we adjust loudness We adjust tracks to -14 dB LUFS, according to the ITU 1770 standard. Loudness normalization is applied during playback. We measure loudne
   - [Spotify LUFS Analysis: How Loud Your Mix Should Be in 2026](https://soundcamps.com/blog/spotify-lufs/): Apr 7, 2026 · Spotify measures your track's integrated LUFS (loudness over the full duration of the file) and compares it to a target level. On the default "Nor
   - [Streaming Loudness Targets 2026 — Spotify, Apple Music LUFS ...](https://trackgleam.com/tools/streaming-loudness-targets/): The 2026 mastering loudness reference chart: every streaming platform's LUFS and true-peak target in one table. Spotify -14 LUFS, Apple Music -16 LUFS, YouTube,
   - [Mastering for Spotify: LUFS & Loudness Explained (2026)](https://audiomixingmastering.com/blog/mastering-for-spotify): Spotify turns everyone’s music up or down to a similar loudness, which changes the old “louder wins” rule completely. Here’s how loudness normalization actually
