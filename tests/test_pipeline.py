import time
from pathlib import Path

import numpy as np
import pytest

from utwatch import config
from utwatch.pipeline.engine import CameraEngine
import synth


def model_free_settings(**overrides):
    settings = config.defaults()
    settings.update(detector="motion", temporal="none", audio="none")
    settings.update(overrides)
    return settings


def run(scenario, tmp_path, **over):
    s = model_free_settings(**over)
    cam = {"id": 1, "name": "Test cam", "calibration": None, "speed_limit_kmh": None}
    eng = CameraEngine(cam, s, tmp_path / "rec")
    alerts, recs = [], []
    for t, frame in synth.frames(scenario):
        r = eng.process(t, frame)
        alerts += r["alerts"]
        recs += r["recordings"]
    eng.close()
    recs += eng.recorder.pop_finished()
    return eng, alerts, recs


def stages(alerts):
    return [(round(a["ts"], 1), a["type"], a["stage"]) for a in alerts]


def test_overlap_no_longer_creates_accident_alerts(tmp_path):
    eng, alerts, recs = run("collision", tmp_path)
    print(stages(alerts))
    assert not [a for a in alerts if a["type"] == "accident"], stages(alerts)


def test_event_recording_is_sixty_seconds(tmp_path):
    import cv2
    import numpy as np
    from utwatch.pipeline.recorder import EventRecorder
    recorder = EventRecorder(1, "Gate", tmp_path / "rec", config.defaults())
    frame = np.full((80, 120, 3), 80, np.uint8)
    for t in range(61):
        recorder.push(float(t), frame)
        if t == 30:
            recorder.start_event("incident-30", float(t))
        recorder.tick(float(t))
    recorder.flush()
    ev = [r for r in recorder.pop_finished() if r["kind"] == "event"]
    assert len(ev) == 1
    cap = cv2.VideoCapture(ev[0]["path"])
    assert cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) > 20
    assert Path(ev[0]["thumb"]).exists() and Path(ev[0]["path"] + ".json").exists()
    assert ev[0]["duration"] == pytest.approx(60.0, abs=1.0)


def test_incident_snapshot_is_a_bounded_scene_and_class_visibility_is_display_only(tmp_path):
    import numpy as np
    from types import SimpleNamespace
    from utwatch.pipeline.engine import CLASS_COLOURS
    settings = model_free_settings()
    engine = CameraEngine({"id": 8, "name": "Gate", "source": "0"}, settings, tmp_path / "rec")
    engine.last_boxes_scale = 1.0
    engine.last_tracks = [SimpleNamespace(id=1, label="car", box=(10, 10, 40, 40), speed_kmh=None)]
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    visible = engine._draw(frame.copy(), 1.0, 0)
    assert tuple(visible[10, 10]) == CLASS_COLOURS["car"]
    engine.hidden_classes = {"car"}
    hidden = engine._draw(frame.copy(), 1.0, 0)
    assert not hidden.any() and len(engine.last_tracks) == 1

    alert = {"incident_id": "8-1", "details": {"scene_bbox": [10, 10, 40, 40]}}
    engine._capture_incident_scene(alert, np.full((100, 100, 3), 80, np.uint8), 1.0)
    import cv2
    crop = cv2.imread(alert["details"]["snapshot_path"])
    assert crop is not None and crop.shape[0] < frame.shape[0] and crop.shape[1] < frame.shape[1]


def test_no_accident_when_cars_pass(tmp_path):
    eng, alerts, recs = run("pass", tmp_path)
    assert not [a for a in alerts if a["type"] == "accident"], stages(alerts)


def test_stationary_obstacle_warning(tmp_path):
    eng, alerts, recs = run("obstacle", tmp_path)
    assert [a for a in alerts if a["type"] == "stationary"], stages(alerts)
    assert not [a for a in alerts if a["type"] == "accident"]


def test_cooldown_limits_repeats(tmp_path):
    eng, alerts, recs = run("obstacle", tmp_path)
    assert len([a for a in alerts if a["type"] == "stationary"]) == 1


def test_manual_recording(tmp_path):
    s = model_free_settings()
    cam = {"id": 2, "name": "Manual", "calibration": None, "speed_limit_kmh": None}
    eng = CameraEngine(cam, s, tmp_path / "rec")
    it = synth.frames("empty", 3.0)
    t, f = next(it)
    eng.process(t, f)
    eng.recorder.start_manual(f, t)
    for t, f in it:
        eng.process(t, f)
    eng.recorder.stop_manual()
    recs = eng.recorder.pop_finished()
    assert len(recs) == 1 and recs[0]["kind"] == "manual" and recs[0]["size"] > 0


def test_fusion_and_rule_changes_incident_rate(tmp_path):
    # with AND fusion the same video-only collision must not raise an accident
    eng, alerts, recs = run("collision", tmp_path, fusion_rule="and")
    assert not [a for a in alerts if a["type"] == "accident"], stages(alerts)


def test_audio_and_video_corroborate(tmp_path):
    from utwatch import plugins
    class AlwaysAccident:
        name = "test_always_accident"
        def predict(self, frames): return 1.0
    plugins.register_temporal("test_always_accident", lambda weights="": AlwaysAccident())
    s = model_free_settings()
    s["fusion_rule"] = "and"
    s["temporal"] = "test_always_accident"
    cam = {"id": 3, "name": "Both", "calibration": None, "speed_limit_kmh": None}
    eng = CameraEngine(cam, s, tmp_path / "rec")
    alerts = []
    for t, frame in synth.frames("collision"):
        if 7.0 <= t <= 10.0:
            eng.audio_input(t, 0.9)           # a crash sound near the touch
        alerts += eng.process(t, frame)["alerts"]
    eng.close()
    assert [a for a in alerts if a["stage"] == "suspected"], stages(alerts)
    assert [a for a in alerts if a["stage"] == "confirmed"], stages(alerts)


def test_timing_budget_on_this_machine(tmp_path):
    eng, alerts, recs = run("pass", tmp_path)
    assert eng.analysed > 20
    print("timings ms", {k: round(v, 1) for k, v in eng.timings.items()})
    assert eng.timings["detect"] < 500          # SRS NFR01.2 on this sandbox; re-measure on the real laptop


def test_bundled_models_load_from_default_paths():
    from utwatch import plugins
    settings = config.defaults()
    detector, warning = plugins.create_detector(settings["detector"], settings["detector_weights"])
    assert warning is None
    assert detector.model.names[7] == "accident"
    assert detector.detect(np.zeros((120, 160, 3), dtype=np.uint8)) == []

    temporal, warning = plugins.create_temporal(settings["temporal"], settings["temporal_weights"])
    assert warning is None
    score = temporal.predict([np.zeros((80, 120, 3), dtype=np.uint8) for _ in range(2)])
    assert 0.0 <= score <= 1.0

    audio, warning = plugins.create_audio(
        settings["audio"], settings["audio_weights"], settings["audio_scaler_weights"])
    assert warning is None
    assert audio.predict(np.zeros(16000, dtype=np.float32), 16000) == 0.0


def test_yolo_accident_class_creates_a_scene_bounded_suspicion(tmp_path):
    from utwatch.models import Detection
    settings = model_free_settings()
    engine = CameraEngine({"id": 9, "name": "Gate", "source": "0"}, settings, tmp_path / "rec")

    class AccidentDetector:
        def detect(self, _frame):
            return [Detection(30, 20, 90, 75, "accident", 0.9)]

    engine.detector = AccidentDetector()
    result = engine.process(1.0, np.zeros((120, 160, 3), dtype=np.uint8))
    suspected = next(alert for alert in result["alerts"] if alert["stage"] == "suspected")
    assert suspected["details"]["scene_bbox"] == [30, 20, 90, 75]
    assert suspected["details"]["snapshot_path"]
    engine.close()


def test_overspeed_alert_needs_calibration_and_limit(tmp_path):
    from utwatch.pipeline.calibration import Calibration
    s = model_free_settings()
    # 640x360 frame = 64 m x 36 m (10 px per metre): the passing cars move 100 px/s = 10 m/s = 36 km/h
    cal = Calibration.from_points([(0, 0), (640, 0), (640, 360), (0, 360)], 64.0, 36.0, (640, 360))
    cam = {"id": 5, "name": "Speed", "calibration": cal.to_json(), "speed_limit_kmh": 20.0}
    eng = CameraEngine(cam, s, tmp_path / "rec")
    alerts = []
    for t, frame in synth.frames("pass"):
        alerts += eng.process(t, frame)["alerts"]
    eng.close()
    over = [a for a in alerts if a["type"] == "overspeed"]
    assert over, stages(alerts)
    kmh = over[0]["details"]["kmh"]
    assert 25 <= kmh <= 50, kmh          # about 36 km/h
    # same scene, limit above the cars' speed, gives none
    cam["speed_limit_kmh"] = 80.0
    eng = CameraEngine(cam, s, tmp_path / "rec2")
    alerts = []
    for t, frame in synth.frames("pass"):
        alerts += eng.process(t, frame)["alerts"]
    eng.close()
    assert not [a for a in alerts if a["type"] == "overspeed"]
    # no calibration: no speed alerts and no km/h
    cam2 = {"id": 6, "name": "Uncal", "calibration": None, "speed_limit_kmh": 20.0}
    eng = CameraEngine(cam2, s, tmp_path / "rec3")
    alerts = []
    for t, frame in synth.frames("pass"):
        alerts += eng.process(t, frame)["alerts"]
    eng.close()
    assert not [a for a in alerts if a["type"] == "overspeed"]
