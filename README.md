# UTWatch 2.0 desktop (development build)

A Windows desktop application for real-time traffic monitoring and accident detection. It runs with no web server, no
cloud and no trained model today. The detector, the temporal (ConvLSTM) classifier and the audio model are slots you fill
later; until then it uses a model-free moving-object detector, so the whole system can be run and tested end to end.

**Tested only in a Linux sandbox (Qt offscreen, synthetic videos). It has not been run on Windows yet.** Run the tests
and the benchmark on your laptop first.

## Run it (Windows 10, Python 3.11)

1. Install Python 3.11 from python.org (tick "Add python.exe to PATH").
2. Double-click `run_utwatch.bat` (creates `.venv`, installs `requirements.txt`, starts the app).
3. First run: the setup wizard creates the administrator and, optionally, the first camera.
4. No camera? Make a demo video: `python tools/make_demo_video.py demo.mp4 collision`, then add `demo.mp4` as a camera source.
   Two cars drive toward each other and collide about 7 s in. Expect "Suspected accident" about a second later and
   "Accident confirmed" a couple of seconds after that, with a clip saved under Recordings.

Data (database, recordings, reports, logs) lives in `%LOCALAPPDATA%\UTWatch` (set `UTWATCH_HOME` to change it).

## Check it on your laptop

```
python -m pytest tests -q                      # 29 tests, about 45 s
python tools/bench.py demo.mp4 --seconds 17    # timings on YOUR cpu
```
`tools/bench.py` is the measurement the project plan needs from the i5-7200U. Run it again after you add YOLO
(`--detector ultralytics`) to find out whether 2 analysed frames per second per camera is realistic.

## What is built, row by row (the 34 rows of the capabilities checklist)

| Row | Feature | State |
|---|---|---|
| 1.1 | Object detection | Placeholder motion detector, tested. YOLO11n plug-in written but untested (needs `pip install ultralytics`). |
| 1.2 | Visual incident classifier | Rule engine built and tested. ConvLSTM slot is empty until the retrained weights exist. |
| 1.3 | Audio impact detection | Slot plus a loudness-spike placeholder; needs `pip install sounddevice`; microphone not tested. YAMNet slot is empty. |
| 1.4 | Speed estimation | Built: calibration dialog and ground-plane speed. Maths and dialog tested with synthetic clicks. |
| 1.5 | Overspeeding alerts | Built, tested on a synthetic scene. |
| 1.6, 1.7, 1.8 | Sudden stop, collision, stationary object | Built and tested. |
| 2.1, 2.2 | Video display, event relay | In-process (no MJPEG, no WebSocket). Built, tested. A watchdog restarts a dead or stalled camera worker (tested). |
| 2.3 | Severity levels | Built (low, medium, high, critical). |
| 2.4 | Dashboard | Built: grid and single view, alert panel, statistics. |
| 2.5 | Audio status indicator | Built; unplugging a microphone is untested. |
| 2.6 | Mute audible alerts | Built as a setting (Settings, Alerts and recording). |
| 2.7, 2.8 | Toasts, severe-incident dialog | Built. The dialog cannot be dismissed until acknowledged (tested). |
| 2.9 | Alert cooldown | Built, tested. |
| 3.1 | Traffic density | Counts, congestion level, one-minute buckets. Count accuracy against ground truth is not measured. |
| 3.2 | Cloud sync | **Not built.** The setting exists and does nothing. |
| 3.3 | Automated reports | Built: on demand, plus a daily PDF and a Monday weekly PDF in the reports folder. |
| 3.4 | Local alert log | Built: SQLite plus `alerts.jsonl` (rotated at 5 MB). |
| 3.5 | Alert history | Built (local) with filters and paging. |
| 3.6 | Export CSV and PDF | Built, tested. |
| 4.1 | Automatic recording | Built and tested: 10 s before, until 10 s after confirmation or expiry, MP4 plus JSON sidecar and thumbnail, manual start and stop, quota and retention, recovery of clips left on disk after a crash. |
| 4.2, 4.3 | Gallery, file management | Built: playback, protect, save a copy, delete (administrator). Playback and buttons are not exercised by tests. |
| 5.1 | Roles | Administrator, officer, responder. Permissions tested. |
| 5.2 | Settings | Built with validation (tested). |
| 5.3 | Setup wizard | Built, tested. |
| 5.4 | Security | Salted PBKDF2 hashing, lockout after 5 failures, administrator password reset (tested). **Email verification is not built.** Check the current OWASP iteration guidance before release. |
| 5.5 | Accounts | The administrator creates and approves accounts; there is no self-registration. |
| 6.1, 6.2, 6.3 | Map | Offline: import a site map image and place camera pins; pins show status and flash on an incident. Coordinates are validated. No online map yet. |

## Adding the models later

Everything goes through `utwatch/plugins.py`; pick the plug-in under Settings, Models and restart cameras.

- **Detector:** `pip install ultralytics`, choose `ultralytics` (weights default to `yolo11n.pt`). COCO classes are mapped to car, truck, bus, motorcycle, person, animal. Note: Ultralytics is AGPL-3.0.
- **ConvLSTM:** implement `TorchConvLSTM` (load weights, take the 40-frame window, return a score from 0 to 1). The engine already samples each analysis window to 40 frames and calls it every hop.
- **Audio:** implement `YamnetAudio` (score the window, return a crash score from 0 to 1). The monitor already feeds it the last window of 16 kHz audio.

## Known limits (read before trusting it)

- The placeholder detector sees moving blobs, not vehicles. Two vehicles that touch merge into one blob; the rules treat a moving object vanishing into another as contact. Overtaking and heavy occlusion can therefore trigger false suspected accidents. A real detector reduces this but does not remove it.
- A stopped vehicle slowly fades into the background model (minutes, not seconds). Expect stationary-object warnings to end eventually.
- All tests use synthetic footage. No real traffic video, webcam, IP camera or microphone has been tried. The false-alert rate on real footage is unknown and is the number that matters.
- The 4 to 5 s analysis window defaults to 5 s (`window_seconds`). The plan's pilot should choose between 4 and 5.
- Windows packaging (`build_windows.bat`) is untested.
- Nothing listens on a network port, and nothing leaves the computer.

## Layout

`utwatch/pipeline` (tracker, rules, alerts, fusion, recorder, engine) has no Qt in it and is unit-tested; `utwatch/ui` is the
interface; `utwatch/db.py` is SQLite; `utwatch/plugins.py` holds the model slots; `tests/` has 29 tests.
