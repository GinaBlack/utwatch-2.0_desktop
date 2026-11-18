"""Runs the whole desktop app headless (Qt offscreen) against a synthetic accident video."""
import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import synth
from utwatch import config, db
from utwatch.ui import theme
from utwatch.ui.dialogs import LoginDialog, SetupWizard
from utwatch.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    return app


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def test_full_app_flow(qapp, tmp_path):
    database = db.Database(tmp_path / "gui.db")
    database.create_user("boss", "Boss12345", "administrator")
    database.create_user("viewer", "Viewer123", "responder")
    video = synth.write_video(tmp_path / "scene.mp4", "empty", 2.0)
    database.add_camera("Gate north", str(video), 4.15, 9.24, 50)
    for key, value in (("detector", "motion"), ("temporal", "none"), ("audio", "none")):
        database.set_setting(key, value)
    user = database.authenticate("boss", "Boss12345")
    win = MainWindow(database, user)
    win.show()
    pump(qapp, 0.5)
    win.send_test_alert()
    modal = qapp.activeModalWidget()
    assert modal is not None
    modal.reject()
    pump(qapp, 0.25)
    assert qapp.activeModalWidget() is None

    snap = tmp_path / "incident.jpg"
    snap.write_bytes(b"snapshot")
    alert = {"ts": time.time(), "camera_id": None, "camera_name": "Gate north", "type": "accident",
             "severity": 3, "stage": "suspected", "incident_id": "incident-1",
             "message": "Suspected accident",
             "details": {"snapshot_path": str(snap), "scene_bbox": [1, 2, 30, 40]}}
    win.on_alert(alert)
    incident = database.incident_report("incident-1")
    assert incident["snapshot_path"] == str(snap)
    assert incident["scene_bbox"] == [1, 2, 30, 40]
    assert snap.with_suffix(".json").exists()

    # Selecting an alert and acknowledging it updates both the database and dashboard row.
    aid = database.add_alert({**alert, "incident_id": "incident-2", "details": {}})
    win._present({**alert, "incident_id": "incident-2", "details": {}}, aid)
    win.dashboard.alerts.setCurrentRow(0)
    assert win.dashboard.ack_btn.isEnabled()
    win.dashboard.ack_btn.click()
    assert database.conn.execute("SELECT ack_by FROM alerts WHERE id=?", (aid,)).fetchone()[0] == "boss"
    win.close()
    assert (tmp_path / "gui.db").exists()


def test_first_run_wizard_and_login(qapp, tmp_path):
    database = db.Database(tmp_path / "wiz.db")
    wiz = SetupWizard(database)
    wiz.admin.user.setText("root"); wiz.admin.pw.setText("Root12345"); wiz.admin.pw2.setText("Root12345")
    wiz.admin.admin_key.setText("AdminKey123")
    wiz.admin.officer_key.setText("OfficerKey123")
    wiz.admin.responder_key.setText("ResponderKey123")
    assert wiz.admin.validatePage()
    wiz.admin.pw2.setText("different1A")
    assert not wiz.admin.validatePage() and "match" in wiz.admin.err.text()
    wiz.admin.pw.setText("short"); wiz.admin.pw2.setText("short")
    assert not wiz.admin.validatePage()
    wiz.admin.pw.setText("Root12345"); wiz.admin.pw2.setText("Root12345")
    wiz.cam.src.setText("/no/such/video.mp4")
    assert not wiz.cam.validatePage()
    wiz.cam.src.setText("")
    wiz.accept()
    assert database.user_count() == 1
    assert database.registration_key_configured("officer")
    database.register_user("operator", "Operator123", "officer", "OfficerKey123")
    dlg = LoginDialog(database)
    dlg.name.setText("root"); dlg.pw.setText("wrong")
    dlg._try()
    assert dlg.user is None and "Wrong" in dlg.err.text()
    dlg.pw.setText("Root12345"); dlg._try()
    assert dlg.user["role"] == "administrator"


def test_accident_modal_can_be_dismissed_without_ack(qapp):
    from utwatch.ui.dialogs import AlertModal
    alert = {"camera_name": "Gate", "ts": time.time(), "severity": 4,
             "stage": "confirmed", "message": "Accident confirmed"}
    dlg = AlertModal(alert, 12, True)
    dlg.show()
    qapp.processEvents()
    dlg.reject()
    assert dlg.result() == AlertModal.Rejected
    assert not dlg._ack


def test_responder_cannot_manage(qapp, tmp_path):
    database = db.Database(tmp_path / "role.db")
    database.create_user("viewer", "Viewer123", "responder")
    win = MainWindow(database, database.authenticate("viewer", "Viewer123"))
    assert win.admin is None
    assert not win.dashboard.ack_btn.isVisibleTo(win.dashboard)
    win.acknowledge(1)             # must be ignored for a read-only role
    win.close()


def test_settings_dialog_validates_and_saves(qapp, tmp_path):
    from utwatch.ui.dialogs import SettingsDialog
    database = db.Database(tmp_path / "set.db")
    dlg = SettingsDialog(database)
    dlg.editors["congestion_medium"].setValue(20)
    dlg.editors["congestion_high"].setValue(10)
    dlg._save()
    assert "above medium" in dlg.err.text()
    dlg.editors["congestion_high"].setValue(30)
    dlg.editors["window_seconds"].setValue(4.0)
    dlg.editors["window_hop_seconds"].setValue(1.0)
    dlg.editors["fusion_rule"].setCurrentText("majority")
    dlg.class_checks["car"].setChecked(False)
    dlg._save()
    s = database.get_settings()
    assert s["window_seconds"] == 4.0 and s["fusion_rule"] == "majority" and s["congestion_high"] == 30
    assert "car" in s["hidden_classes"].split(",")


def test_report_pdf_and_calibration_dialog(qapp, tmp_path):
    import numpy as np
    from utwatch import reports
    from utwatch.ui.pages import save_report_pdf
    from utwatch.ui.dialogs import CalibrationDialog
    database = db.Database(tmp_path / "pdf.db")
    rep = reports.build_report(database, time.time() - 3600, time.time())
    pdf = tmp_path / "r.pdf"
    save_report_pdf(rep, str(pdf))
    assert pdf.exists() and pdf.read_bytes()[:4] == b"%PDF"
    dlg = CalibrationDialog(np.zeros((360, 640, 3), np.uint8))
    dlg.w.setValue(3.5); dlg.l.setValue(10.0)
    for x, y in ((200, 100), (440, 100), (520, 300), (120, 300)):
        dlg._click(x * dlg.view.scale, y * dlg.view.scale) if False else dlg._click(x, y)
    assert dlg.cal is not None and dlg.save.isEnabled()
    dlg.known.setValue(3.5)
    dlg._click(200, 100); dlg._click(440, 100)             # the top edge is 3.5 m, so the check should be near 0% off
    assert "0% off" in dlg.check_text or "1% off" in dlg.check_text, dlg.check_text
    dlg._save()
    assert dlg.result_json is not None


def test_watchdog_restarts_dead_worker(qapp, tmp_path):
    database = db.Database(tmp_path / "wd.db")
    database.create_user("boss", "Boss12345", "administrator")
    video = synth.write_video(tmp_path / "s.mp4", "empty", 3.0)
    cid = database.add_camera("Cam", str(video))
    for key, value in (("detector", "motion"), ("temporal", "none"), ("audio", "none")):
        database.set_setting(key, value)
    win = MainWindow(database, database.authenticate("boss", "Boss12345"))
    pump(qapp, 1.0)
    first = win.workers[cid]
    first._stop = False
    first.cmds.put(("stop", None))            # stops the loop without marking it as an intentional stop
    first.wait(5000)
    first._stop = False
    assert not first.isRunning()
    win._watchdog()
    assert win.workers[cid] is not first and win.workers[cid].isRunning()
    win.close()
