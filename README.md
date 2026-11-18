# UTWatch 2.0 desktop (development build)

A Windows desktop application for real-time traffic monitoring and accident detection. It runs without a web server or
cloud connection. By default it loads the supplied YOLO detector, ConvLSTM accident classifier, audio classifier, and
audio scaler from `models\`; the motion-only detector and model-free alert fallbacks remain available for testing.

**Tested only in a Linux sandbox (Qt offscreen, synthetic videos). It has not been run on Windows yet.** Run the tests
and the benchmark on your laptop first.

## Run it (Windows 10, Python 3.11)

1. Install Python 3.11 from python.org (tick "Add python.exe to PATH").
2. Double-click `run_utwatch.bat` (creates `.venv`, installs `requirements.txt`, starts the app).
3. First run: the setup wizard creates the administrator, role signup keys, and optionally the first camera.
4. No camera? Make a demo video: `python tools/make_demo_video.py demo.mp4 collision`, then add `demo.mp4` as a camera source.
   The bundled models process the video; their accuracy should be validated on representative real footage before operational use.

Data (database, recordings, reports, logs) lives in `%LOCALAPPDATA%\UTWatch` (set `UTWATCH_HOME` to change it).

## Check it on your laptop

```
python -m pytest tests -q                      # 34 tests
python tools/bench.py demo.mp4 --seconds 17    # timings on YOUR cpu
```
`tools/bench.py` measures performance on your machine. The bundled YOLO and ConvLSTM models run on CPU by default;
profile before deciding how many cameras to run concurrently.

## What is built, row by row (the 34 rows of the capabilities checklist)

| Row | Feature | State |
|---|---|---|
| 1.1 | Object detection | `models\best.pt` is loaded by the Ultralytics detector by default, including its trained accident and road-object classes. |
| 1.2 | Visual incident classifier | `models\accident_classifier(1).pth` ResNet18 + ConvLSTM state dict is loaded by default. |
| 1.3 | Audio impact detection | `models\audio_model.pth` plus `models\scaler.pkl` classify 40 MFCC features; a working microphone is required for live audio. |
| 1.4 | Speed estimation | Built: calibration dialog and ground-plane speed. Maths and dialog tested with synthetic clicks. |
| 1.5 | Overspeeding alerts | Built, tested on a synthetic scene. |
| 1.6, 1.7, 1.8 | Sudden stop, collision, stationary object | Sudden-stop and box-overlap collision rules were removed. Stationary-object warnings remain. Accident detection requires configured temporal/audio models. |
| 2.1, 2.2 | Video display, event relay | In-process (no MJPEG, no WebSocket). Built, tested. A watchdog restarts a dead or stalled camera worker (tested). |
| 2.3 | Severity levels | Built (low, medium, high, critical). |
| 2.4 | Dashboard | Built: grid and single view, per-camera minute averages/peaks, congestion, alerts. Box visibility can be toggled by class without disabling detection. |
| 2.5 | Audio status indicator | Built; unplugging a microphone is untested. |
| 2.6 | Mute audible alerts | Built as a setting (Settings, Alerts and recording). |
| 2.7, 2.8 | Toasts, severe-incident dialog | Built. Severe alerts can be dismissed or acknowledged separately (tested). |
| 2.9 | Alert cooldown | Built, tested. |
| 3.1 | Traffic density | Counts, congestion level, one-minute buckets. Count accuracy against ground truth is not measured. |
| 3.2 | Cloud sync | **Not built.** Data remains local. Provider, remote storage, and retention design must be agreed before implementation. |
| 3.3 | Automated reports | Built: on demand, plus a daily PDF and a Monday weekly PDF in the reports folder. |
| 3.4 | Local alert log | Built: SQLite plus `alerts.jsonl` (rotated at 5 MB). |
| 3.5 | Alert history | Built (local) with filters and paging. |
| 3.6 | Export CSV and PDF | Built, tested. |
| 4.1 | Automatic recording | Incident clips are 60 seconds centered on initial suspicion (30 s before and after), with MP4, JSON sidecar and thumbnail. Manual start/stop, quota and retention are also available. |
| 4.2, 4.3 | Gallery, file management | Built: playback, protect, save a copy, delete (administrator). Playback and buttons are not exercised by tests. |
| 5.1 | Roles | Administrator, officer, responder. Permissions tested. |
| 5.2 | Settings | Built with validation (tested). |
| 5.3 | Setup wizard | Built, tested. |
| 5.4 | Security | Salted PBKDF2 hashing, lockout after 5 failures, administrator password reset (tested). **Email verification is not built.** Check the current OWASP iteration guidance before release. |
| 5.5 | Accounts | Users can register with a role-specific key. The first administrator can rotate the hashed keys; administrators can also create accounts using the matching role key. |
| 6.1, 6.2, 6.3 | Map | Offline: import a site map image and place camera pins; pins show status and flash on an incident. Coordinates are validated. No online map yet. |

## Model files

The model files in `models\` are selected by default in Settings > Models. You can override their paths there.
The checkpoint loader validates state-dictionary compatibility and reports a warning in Diagnostics if a model cannot be loaded.

- **Detector:** `best.pt` is an Ultralytics detection checkpoint. Its labels are normalized to the dashboard's road-object classes; the trained `accident` label produces a scene-bounded accident cue. Ultralytics is AGPL-3.0.
- **Visual classifier:** `accident_classifier(1).pth` is loaded as a ResNet18 + two-layer ConvLSTM binary classifier and evaluates the analysis window sampled to 40 frames.
- **Audio classifier:** `audio_model.pth` is a binary MLP. `scaler.pkl` standardizes the 40-dimensional mean-MFCC feature vector; `librosa` extracts the features from 16 kHz mono microphone windows.

## Known limits (read before trusting it)

- The YOLO detector and classifiers are trained models, but their accuracy and preprocessing assumptions have not been independently evaluated on real traffic. Validate against representative labeled footage before operational use.
- Box overlap and sudden-stop accident cues are disabled. Accident cues come from the trained YOLO `accident` class, the ConvLSTM classifier, and/or the audio classifier according to the fusion setting. Suspected incidents save a bounded-scene snapshot and a JSON report under `reports\incidents`.
- A nearly black image is reported as possible low light or lens obstruction, not as a disconnected camera. A stream connection/read failure is reported separately. Darkness alone cannot distinguish night from a covered lens.
- A stopped vehicle slowly fades into the background model (minutes, not seconds). Expect stationary-object warnings to end eventually.
- All tests use synthetic footage. No real traffic video, webcam, IP camera or microphone has been tried. The false-alert rate on real footage is unknown and is the number that matters.
- The 4 to 5 s analysis window defaults to 5 s (`window_seconds`). The plan's pilot should choose between 4 and 5.
- Windows packaging (`build_windows.bat`) is untested.
- Nothing listens on a network port, and nothing leaves the computer.

Object detections continue when their boxes are hidden in Settings > Object display. Class box colors are assigned independently of the current incident stage.

## Layout

`utwatch/pipeline` (tracker, rules, alerts, fusion, recorder, engine) has no Qt in it and is unit-tested; `utwatch/ui` is the
interface; `utwatch/db.py` is SQLite; `utwatch/plugins.py` loads the bundled models; `tests/` has 34 tests.
