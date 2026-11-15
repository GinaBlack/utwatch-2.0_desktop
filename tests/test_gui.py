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
    video = synth.write_video(tmp_path / "scene.mp4", "collision")
    cam_id = database.add_camera("Gate north", str(video), 4.15, 9.24, 50)
    database.set_setting("alert_cooldown_seconds", 2)
    user = database.authenticate("boss", "Boss12345")
    win = MainWindow(database, user)
    win.show()
    shots = Path(os.environ.get("UTWATCH_SHOTS", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)

    # let the video play until the collision has been analysed
    deadline = time.time() + 30
    got = []
    while time.time() < deadline:
        pump(qapp, 0.5)
        modal = qapp.activeModalWidget()
        got = database.query_alerts(camera_id=cam_id)
        if any(a["stage"] == "suspected" for a in got) and win.dashboard.tiles[cam_id].image is not None:
            win.grab().save(str(shots / "dashboard_suspected.png"))
        if modal is not None:
            win.grab().save(str(shots / "dashboard_modal.png"))
            modal.grab().save(str(shots / "alert_modal.png"))
            break
    stages = [a["stage"] for a in got]
    print("stages", stages)
    assert "suspected" in stages and "confirmed" in stages
    # the severe modal must block until acknowledged
    modal = qapp.activeModalWidget()
    assert modal is not None, "a confirmed accident should open the acknowledgement dialog"
    modal.reject()
    assert qapp.activeModalWidget() is modal, "Escape must not dismiss an unacknowledged severe alert"
    from PySide6.QtWidgets import QPushButton
    [b for b in modal.findChildren(QPushButton) if b.text() == "Acknowledge"][0].click()
    pump(qapp, 0.5)
    conf = [a for a in database.query_alerts(camera_id=cam_id) if a["stage"] == "confirmed"][0]
    assert database.conn.execute("SELECT ack_by FROM alerts WHERE id=?", (conf["id"],)).fetchone()[0] == "boss"

    # event recording is written after the post-roll
    for tab in range(win.tabs.count()):
        win.tabs.setCurrentIndex(tab)
        pump(qapp, 0.3)
        win.grab().save(str(shots / f"tab_{win.tabs.tabText(tab).lower()}.png"))
    win.close()
    recs = database.recordings(cam_id)
    print("recordings", [(r["kind"], round(r["duration"] or 0, 1)) for r in recs])
    assert any(r["kind"] == "event" for r in recs)
    assert (tmp_path / "gui.db").exists()


def test_first_run_wizard_and_login(qapp, tmp_path):
    database = db.Database(tmp_path / "wiz.db")
    wiz = SetupWizard(database)
    wiz.admin.user.setText("root"); wiz.admin.pw.setText("Root12345"); wiz.admin.pw2.setText("Root12345")
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
    dlg = LoginDialog(database)
    dlg.name.setText("root"); dlg.pw.setText("wrong")
    dlg._try()
    assert dlg.user is None and "Wrong" in dlg.err.text()
    dlg.pw.setText("Root12345"); dlg._try()
    assert dlg.user["role"] == "administrator"


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
    dlg._save()
    s = database.get_settings()
    assert s["window_seconds"] == 4.0 and s["fusion_rule"] == "majority" and s["congestion_high"] == 30


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
