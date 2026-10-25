# SoundPilot on Windows and macOS

This branch retains the same architecture on both operating systems:

React/Vite (`web/`) → FastAPI (`mixassist.api`) → Gemma provider → shared
MCP tool functions → `BridgeTransport` (python-rtmidi) → virtual MIDI →
`device_MixAssist.py` in FL Studio. The Python transport sends typed SysEx
and receives acknowledgements/readback on a separate virtual MIDI port.

**Status:** FakeFL tests are available, but the real FL Studio P1–P8 probes
have not yet been run and recorded on either OS. A successful Python unit test
is not evidence that FL Studio responded.

## 1. Dependencies

- Python 3.11 or newer, Node.js 20.19+ and FL Studio
- Windows: [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
- macOS: the built-in Audio MIDI Setup → IAC Driver
- An LLM provider (configure repo-root `.env` using `.env.example`)

Run from the repository root in PowerShell or Terminal:

```sh
python scripts/start.py setup
python scripts/start.py tests
```

The launcher creates `.venv` if needed and installs the Python dependencies
using the correct executable: `.venv/Scripts/python.exe` on Windows or
`.venv/bin/python` on macOS. The frontend is a separate Node.js application:

```sh
cd web
npm install
npm run dev
```

On Windows PowerShell, use `npm.cmd` instead of `npm` if the local execution
policy blocks `npm.ps1`. You do not have to run `npm install` each time.

## 2. Create virtual MIDI ports

**Windows:** Install and launch loopMIDI. Create *two* ports named
`MA to FL` and `MA from FL`. Keep loopMIDI open while using SoundPilot.

**macOS:** Audio MIDI Setup → Window → Show MIDI Studio → IAC Driver →
Device is online. Create the same two ports. CoreMIDI will often show a
prefix like `IAC Driver MA to FL` — the bridge matches names by substring.

Direction is from the Python backend's point of view:

| Path | Python opens | FL Studio config |
| --- | --- | --- |
| `MA to FL` | MIDI output | Enabled MIDI input, Controller type `MixAssist` |
| `MA from FL` | MIDI input | MIDI output on the same FL port number |

In FL Studio Options → MIDI Settings set both linked FL ports to a free
port number (for example 10). Do not enable the `MA from FL` input or
route `MA to FL` as an FL output.

If your driver adds a prefix/suffix that causes an ambiguous match, set
`SOUNDPILOT_MIDI_TO_PORT` and `SOUNDPILOT_MIDI_FROM_PORT` in the repo
root `.env`, or your shell, to the desired unique port-name fragments.
The existing bridge matches those names case-insensitively.

## 3. Install the controller script

The script must run inside FL Studio; do not attempt to import FL's
`mixer` module in external Python.

Preview the destination from the repository root:

```sh
python scripts/install_fl_script.py
```

Copy only after checking the path:

```sh
python scripts/install_fl_script.py --install
```

If FL Studio is installed with a custom User data folder, give the
correct **Settings** directory explicitly:

```sh
python scripts/install_fl_script.py --path "C:\\Users\\you\\OneDrive\\Documents\\Image-Line\\FL Studio\\Settings" --install
```

The installer creates `Hardware/MixAssist/device_MixAssist.py` and backs up
any previous script. Windows Documents may be redirected to OneDrive;
the installer and MIDI piano-roll script generator check that as well as
the normal user Documents directory.

Open/restart FL Studio and select `MixAssist` as the controller type for
the `MA to FL` input. Confirm that View → Script output reports it loaded.

## 4. Run non-destructive connection diagnostics

```sh
python scripts/start.py doctor
python scripts/start.py check
```

`doctor` enumerates MIDI ports and looks for the FL Studio device script.
`check` additionally sends a **read-only** SysEx `hello` to the running FL
Studio controller script and expects FL API and port data in reply.

- No ports: loopMIDI/IAC isn't running or python-rtmidi cannot see the driver.
- Ports found, no hello reply: check MIDI port number matching and FL script
  assignment in Options → MIDI Settings; check View → Script output.
- Hello reply received: move on to real-FL P1–P8 in
  [internal/probes.md](../internal/probes.md), and record the results.
- The `doctor` tool never changes mixer settings.

## 5. Start the application

```sh
python scripts/start.py backend
```

This starts the HTTP API at http://127.0.0.1:8000 with the real bridge.
For an offline demo/test without FL use:

```sh
python scripts/start.py fake
```

In a **second** terminal:

```sh
cd web
npm run dev
```

Open http://127.0.0.1:5173. The UI presents proposed edits for approval,
rather than performing silent writes.

## 6. Configure the LLM

Copy `.env.example` to `.env` in the repository root. Example Ollama
configuration (Ollama local gateway, cloud inference):

```dotenv
SOUNDPILOT_LLM=ollama
SOUNDPILOT_MODEL=gemma4:31b-cloud
OLLAMA_HOST=http://127.0.0.1:11434
SOUNDPILOT_FL=bridge
```

Ensure the Ollama app is running and signed in, and the cloud model is
registered. Other providers are documented in the main README. Never place
provider credentials in `web/.env`.

## 7. Windows shell examples

PowerShell from the repository root:

```powershell
py -3.12 scripts/start.py setup
py -3.12 scripts/start.py doctor
py -3.12 scripts/start.py check
py -3.12 scripts/start.py backend
```

On macOS use `python3 scripts/start.py ...`. The launcher internally picks
the correct .venv interpreter, so activating it or writing `.venv/bin` paths
is unnecessary.

### Real-FL limitations

The Python/MIDI transport is designed for both platforms; FL's fader
curves, maximum SysEx frame size, EQ semantics, delayed readback, MIDI
recording latency, and automation behavior remain subject to the
human-run real-FL tests. Do not test a write on a valuable production
project before the bridge and undo behavior are validated.
