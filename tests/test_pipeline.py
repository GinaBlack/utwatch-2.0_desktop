import time
from pathlib import Path

import pytest

from utwatch import config
from utwatch.pipeline.engine import CameraEngine
import synth


def run(scenario, tmp_path, **over):
    s = config.defaults()
    s.update(over)
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


def test_collision_gives_suspected_then_confirmed(tmp_path):
    eng, alerts, recs = run("collision", tmp_path)
    print(stages(alerts))
    sus = [a for a in alerts if a["stage"] == "suspected"]
    conf = [a for a in alerts if a["stage"] == "confirmed"]
    assert len(sus) == 1, stages(alerts)
    assert len(conf) == 1, stages(alerts)
    assert sus[0]["ts"] < conf[0]["ts"]
    assert 7.0 <= sus[0]["ts"] <= 9.5, "suspected alert should follow the overlap that starts at about 7.4 s"
    assert conf[0]["severity"] == 4 and sus[0]["severity"] == 3
    assert conf[0]["incident_id"] == sus[0]["incident_id"]


def test_recording_contains_pre_roll_and_aftermath(tmp_path):
    import cv2
    eng, alerts, recs = run("collision", tmp_path)
    ev = [r for r in recs if r["kind"] == "event"]
    assert len(ev) == 1
    cap = cv2.VideoCapture(ev[0]["path"])
    assert cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) > 20
    assert Path(ev[0]["thumb"]).exists() and Path(ev[0]["path"] + ".json").exists()
    assert ev[0]["duration"] > 5.0, "clip should span pre-roll, the window and the post-roll"


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
    s = config.defaults()
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
    s = config.defaults()
    s["fusion_rule"] = "and"
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


def test_overspeed_alert_needs_calibration_and_limit(tmp_path):
    from utwatch.pipeline.calibration import Calibration
    s = config.defaults()
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
