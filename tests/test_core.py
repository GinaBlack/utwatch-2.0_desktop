import json
import time

import numpy as np
import pytest

from utwatch import config, db, models, security
from utwatch.pipeline import fusion
from utwatch.pipeline.analytics import StatsCollector, congestion_level
from utwatch.pipeline.calibration import Calibration
from utwatch.pipeline.tracker import IoUTracker, iou
from utwatch.models import Detection


def test_settings_validation():
    assert config.validate_setting("analysis_fps", "5") == 5.0
    with pytest.raises(ValueError):
        config.validate_setting("analysis_fps", 100)
    with pytest.raises(ValueError):
        config.validate_setting("fusion_rule", "xor")
    with pytest.raises(ValueError):
        config.validate_setting("nope", 1)
    assert config.validate_latlon("4.15", "9.24") == (4.15, 9.24)
    with pytest.raises(ValueError):
        config.validate_latlon("95", "0")


def test_password_rules_and_hashing():
    s = security.new_salt()
    h = security.hash_password("Secret123", s)
    assert security.verify_password("Secret123", s, h)
    assert not security.verify_password("secret123", s, h)
    with pytest.raises(ValueError):
        security.check_password_rules("short1A")
    with pytest.raises(ValueError):
        security.check_password_rules("alllowercase1")


def test_users_roles_lockout(tmp_path):
    d = db.Database(tmp_path / "t.db")
    d.create_user("admin", "Admin1234", "administrator")
    d.create_user("officer1", "Officer123", "officer", approved=False)
    assert d.authenticate("admin", "Admin1234")["role"] == "administrator"
    with pytest.raises(db.AuthError, match="approval"):
        d.authenticate("officer1", "Officer123")
    for _ in range(security.MAX_FAILED):
        with pytest.raises(db.AuthError):
            d.authenticate("admin", "wrong")
    with pytest.raises(db.AuthError, match="locked"):
        d.authenticate("admin", "Admin1234")
    uid = [u for u in d.list_users() if u["username"] == "admin"][0]["id"]
    d.reset_password(uid, "NewPass123")
    assert d.authenticate("admin", "NewPass123")["username"] == "admin"
    with pytest.raises(ValueError):
        d.create_user("admin", "Admin1234")


def test_permissions():
    assert models.can("administrator", "delete_clip")
    assert not models.can("officer", "delete_clip")
    assert not models.can("responder", "acknowledge")
    assert models.can("responder", "view")


def test_cameras_alerts_history(tmp_path):
    d = db.Database(tmp_path / "t.db")
    cid = d.add_camera("North gate", "0", 4.15, 9.24, 50)
    with pytest.raises(ValueError):
        d.add_camera("Bad", "0", 200, 0)
    with pytest.raises(ValueError):
        d.add_camera("North gate", "1")
    d.add_alert({"ts": 100.0, "camera_id": cid, "camera_name": "North gate", "type": "accident",
                 "severity": 4, "stage": "confirmed", "incident_id": "x", "message": "m"})
    d.add_alert({"ts": 200.0, "camera_id": cid, "camera_name": "North gate", "type": "stationary",
                 "severity": 2, "stage": "info", "message": "m"})
    assert len(d.query_alerts()) == 2
    assert len(d.query_alerts(min_severity=3)) == 1
    assert len(d.query_alerts(type_="stationary")) == 1
    assert len(d.query_alerts(since=150)) == 1
    d.set_setting("window_seconds", 4)
    assert d.get_settings()["window_seconds"] == 4.0
    with pytest.raises(ValueError):
        d.set_setting("window_seconds", 50)


def test_storage_quota_and_protection(tmp_path):
    d = db.Database(tmp_path / "t.db")
    ids = []
    for i in range(4):
        p = tmp_path / f"c{i}.mp4"
        p.write_bytes(b"x" * 1000)
        ids.append(d.add_recording({"path": str(p), "ts": time.time() - (4 - i) * 10, "size": 1000, "camera_id": 1}))
    d.set_protected(ids[0], True)
    deleted = d.enforce_storage(quota_gb=2500 / 1e9, retention_days=30)     # room for 2.5 files
    left = {r["id"] for r in d.recordings()}
    assert ids[0] in left, "protected clip must survive"
    assert deleted == 2 and len(left) == 2


def test_fusion_rules():
    assert fusion.decide("or", True, False, False)
    assert not fusion.decide("and", True, False, False)
    assert fusion.decide("and", True, False, True)
    assert fusion.decide("majority", True, True, False)
    assert not fusion.decide("majority", False, False, True)
    assert not fusion.decide("weighted", True, False, False)      # 0.45 < 0.5
    assert fusion.decide("weighted", True, False, True)           # 0.65
    with pytest.raises(ValueError):
        fusion.decide("xor", 1, 1, 1)


def test_calibration_recovers_distances():
    # a 3.5 m x 10 m road rectangle seen in a 640x360 image
    px = [(200, 100), (440, 100), (520, 300), (120, 300)]
    cal = Calibration.from_points(px, 3.5, 10.0, (640, 360))
    assert cal.distance_m(px[0], px[1]) == pytest.approx(3.5, rel=1e-3)
    assert cal.distance_m(px[0], px[3]) == pytest.approx(10.0, rel=1e-3)
    cal2 = Calibration.from_json(json.dumps(cal.to_json()))
    assert cal2.distance_m(px[1], px[2]) == pytest.approx(cal.distance_m(px[1], px[2]))
    # same point seen in a half-size analysis frame gives the same ground position
    g1 = cal.to_ground(300, 200)
    g2 = cal.to_ground(150, 100, frame_size=(320, 180))
    assert g1 == pytest.approx(g2)
    with pytest.raises(ValueError):
        Calibration.from_points(px[:3], 3.5, 10, (640, 360))


def test_tracker_speed_and_stillness():
    tr = IoUTracker()
    t = 0.0
    for i in range(10):
        det = Detection(10 + i * 10, 50, 60 + i * 10, 90)
        vis = tr.update([det], t, (320, 240))
        t += 0.2
    track = vis[0]
    assert track.speed_n > 0.05          # 50 px/s on a 400 px diagonal
    for i in range(10):
        vis = tr.update([Detection(100, 50, 150, 90)], t, (320, 240))
        t += 0.2
    assert vis[0].still_for(t) > 1.0


def test_calibrated_speed_is_in_kmh():
    px = [(0, 0), (320, 0), (320, 240), (0, 240)]
    cal = Calibration.from_points(px, 32.0, 24.0, (320, 240))        # 10 px per metre
    tr = IoUTracker()
    t = 0.0
    for i in range(8):
        vis = tr.update([Detection(10 + i * 10, 100, 40 + i * 10, 130)], t, (320, 240), cal)   # 50 px/s = 5 m/s
        t += 0.2
    assert vis[0].speed_kmh == pytest.approx(18.0, rel=0.1)


def test_congestion_and_buckets():
    assert congestion_level(2, 6, 12) == "low"
    assert congestion_level(7, 6, 12) == "medium"
    assert congestion_level(15, 6, 12) == "high"
    sc = StatsCollector(6, 12)
    assert sc.add(0, 4) is None
    assert sc.add(30, 8) is None
    b = sc.add(61, 2)
    assert b["avg"] == 6.0 and b["max"] == 8


def test_orphan_recordings_are_adopted(tmp_path):
    d = db.Database(tmp_path / "o.db")
    folder = tmp_path / "rec"
    folder.mkdir()
    (folder / "Gate_20260101_101010_1-5.mp4").write_bytes(b"x" * 500)
    (folder / "Gate_20260101_101010_1-5.mp4.json").write_text(json.dumps({"camera": "Gate", "start": 1000.0, "duration": 12.0, "incident_id": "1-5"}))
    assert d.adopt_orphan_recordings(folder) == 1
    assert d.adopt_orphan_recordings(folder) == 0          # not added twice
    r = d.recordings()[0]
    assert r["camera_name"] == "Gate" and r["duration"] == 12.0 and r["incident_id"] == "1-5"


def test_reports_and_exports(tmp_path):
    from utwatch import reports
    d = db.Database(tmp_path / "r.db")
    cid = d.add_camera("Gate", "0")
    now = time.time()
    for stage, typ, sev in (("suspected", "accident", 3), ("confirmed", "accident", 4), ("info", "stationary", 2)):
        d.add_alert({"ts": now - 60, "camera_id": cid, "camera_name": "Gate", "type": typ, "severity": sev, "stage": stage,
                     "incident_id": "i1", "message": "m"})
    d.add_stat(cid, int(now // 60 * 60) - 120, 7.0, 9, "medium")
    rep = reports.build_report(d, now - 3600, now + 1)
    assert rep["confirmed"] == 1 and rep["suspected"] == 1
    assert rep["per_camera"]["Gate"]["peak_vehicles"] == 9 and rep["per_camera"]["Gate"]["incidents"] == 1
    assert "Gate" in reports.report_html(rep)
    n = reports.export_alerts_csv(d.query_alerts(), tmp_path / "a.csv")
    assert n == 3 and "Critical" in (tmp_path / "a.csv").read_text()
    reports.export_report_csv(rep, tmp_path / "r.csv")
    assert "Confirmed accidents" in (tmp_path / "r.csv").read_text()
