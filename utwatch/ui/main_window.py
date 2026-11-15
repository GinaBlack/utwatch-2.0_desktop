"""Main window: wires cameras, alerts, recording, storage and the tabs together."""
from __future__ import annotations
import datetime as dt
import json
import logging
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QInputDialog, QLabel, QMainWindow, QMessageBox, QSystemTrayIcon, QTabWidget)

from .. import config, reports
from ..audio import AudioMonitor
from ..models import Severity, can
from ..pipeline.calibration import Calibration
from . import dialogs
from .pages import (AdminPage, DashboardPage, DiagnosticsPage, GalleryPage, HistoryPage, MapPage, ReportsPage, save_report_pdf)
from .theme import SEVERITY_COLOURS
from .widgets import Toast
from .worker import CameraWorker


class MainWindow(QMainWindow):
    def __init__(self, database, user: dict, icon: QIcon | None = None):
        super().__init__()
        self.db, self.user, self.role = database, user, user["role"]
        self.settings = self.db.get_settings()
        self.workers: dict[int, CameraWorker] = {}
        self.statuses: dict[int, dict] = {}
        self.last_seen: dict[int, float] = {}
        self.selected_cam: int | None = None
        self._modals: list = []
        self._modal_open = False
        self.audio_text = "Audio off"
        self.setWindowTitle(f"UTWatch  |  {user['username']} ({self.role})")
        self.resize(1360, 820)
        if icon: self.setWindowIcon(icon)

        self.tabs = QTabWidget(); self.setCentralWidget(self.tabs)
        self.dashboard = DashboardPage(can(self.role, "acknowledge"))
        self.history = HistoryPage(self.db, self.role)
        self.gallery = GalleryPage(self.db, self.role, user["username"])
        self.reports_page = ReportsPage(self.db, self.role)
        self.map = MapPage(self.db, self.role)
        self.diag = DiagnosticsPage(self.db)
        for page, name in ((self.dashboard, "Dashboard"), (self.history, "History"), (self.gallery, "Recordings"),
                           (self.reports_page, "Reports"), (self.map, "Map"), (self.diag, "Diagnostics")):
            self.tabs.addTab(page, name)
        self.admin = None
        if can(self.role, "manage_users"):
            self.admin = AdminPage(self.db, user)
            self.tabs.addTab(self.admin, "Administration")
        self.tabs.currentChanged.connect(self._tab_changed)

        self.toast = Toast(self)
        self.status_user = QLabel(f"Signed in as {user['username']}")
        self.status_audio = QLabel("Audio off")
        self.statusBar().addWidget(self.status_user, 1); self.statusBar().addPermanentWidget(self.status_audio)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable() and icon:
            self.tray = QSystemTrayIcon(icon, self); self.tray.show()

        self.audio = AudioMonitor(self.settings, self)
        self.audio.score.connect(self._audio_score)
        self.audio.status.connect(self._audio_status)

        self._build_menus()
        self.dashboard.acknowledge_requested.connect(self.acknowledge)
        self.dashboard.camera_selected.connect(lambda c: setattr(self, "selected_cam", c))

        adopted = self.db.adopt_orphan_recordings(config.sub_dir("recordings"))
        if adopted:
            self.db.audit(None, "recovered recordings", f"{adopted} clips found on disk and added")
        self.start_cameras()
        self.audio.start()
        self.refresh_counts()
        self.history.refresh(); self.gallery.reload_cameras(); self.gallery.refresh()

        self.t_diag = QTimer(self, interval=1000, timeout=self._tick); self.t_diag.start()
        self.t_storage = QTimer(self, interval=3600_000, timeout=self.enforce_storage); self.t_storage.start()
        self.t_report = QTimer(self, interval=600_000, timeout=self.maybe_scheduled_reports); self.t_report.start()
        QTimer.singleShot(2000, self.enforce_storage)
        QTimer.singleShot(5000, self.maybe_scheduled_reports)

    # ------------------------------------------------------------ menus
    def _act(self, menu, text, fn, perm=None, shortcut=None):
        a = QAction(text, self); a.triggered.connect(fn)
        if shortcut: a.setShortcut(shortcut)
        if perm and not can(self.role, perm): a.setEnabled(False)
        menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self._act(f, "Settings...", self.open_settings, "manage_settings", "Ctrl+,")
        f.addSeparator(); self._act(f, "Exit", self.close, None, "Ctrl+Q")
        c = mb.addMenu("&Cameras")
        self._act(c, "Add camera...", self.add_camera, "manage_cameras")
        self._act(c, "Edit selected camera...", self.edit_camera, "manage_cameras")
        self._act(c, "Remove selected camera...", self.remove_camera, "manage_cameras")
        self._act(c, "Calibrate selected camera...", self.calibrate_camera, "manage_cameras")
        c.addSeparator()
        self.rec_action = self._act(c, "Start or stop recording on selected camera", self.toggle_recording, "record_manual", "Ctrl+R")
        t = mb.addMenu("&Tools")
        self._act(t, "Send test alert", self.send_test_alert)
        self._act(t, "Simulate a crash sound", lambda: self.audio.simulate(0.9))
        h = mb.addMenu("&Help")
        self._act(h, "About UTWatch", lambda: QMessageBox.about(self, "UTWatch", "UTWatch 2.0 (development build)\nUrban traffic watch: real-time monitoring and accident detection."))

    # ------------------------------------------------------------ cameras
    def start_cameras(self):
        cams = self.db.cameras()
        self.dashboard.set_cameras([c for c in cams if c["enabled"]])
        self.map.reload(); self.history.reload_cameras(); self.gallery.reload_cameras()
        rec_dir = config.sub_dir("recordings")
        for cam in cams:
            if not cam["enabled"]: continue
            w = CameraWorker(cam, self.settings, rec_dir, self)
            w.frame.connect(self.dashboard.update_frame)
            w.alert.connect(self.on_alert)
            w.bucket.connect(self.on_bucket)
            w.recording.connect(self.on_recording)
            w.status.connect(self.on_status)
            self.workers[cam["id"]] = w
            self.last_seen[cam["id"]] = time.time()
            w.start()

    def stop_cameras(self):
        for w in self.workers.values(): w.stop()
        for w in self.workers.values(): w.wait(8000)
        QApplication.processEvents()            # deliver recordings the workers finished while shutting down
        self.workers.clear(); self.statuses.clear()

    def restart_cameras(self):
        self.stop_cameras(); self.start_cameras()

    def _pick_camera(self):
        cams = self.db.cameras()
        if not cams:
            QMessageBox.information(self, "Cameras", "Add a camera first."); return None
        if self.selected_cam and any(c["id"] == self.selected_cam for c in cams):
            return next(c for c in cams if c["id"] == self.selected_cam)
        if len(cams) == 1: return cams[0]
        name, ok = QInputDialog.getItem(self, "Choose a camera", "Camera", [c["name"] for c in cams], 0, False)
        return next(c for c in cams if c["name"] == name) if ok else None

    def add_camera(self):
        if dialogs.CameraDialog(self.db, None, self).exec():
            self.db.audit(self.user["username"], "add camera"); self.restart_cameras()

    def edit_camera(self):
        cam = self._pick_camera()
        if cam and dialogs.CameraDialog(self.db, cam, self).exec():
            self.db.audit(self.user["username"], "edit camera", cam["name"]); self.restart_cameras()

    def remove_camera(self):
        cam = self._pick_camera()
        if cam and QMessageBox.question(self, "Remove camera", f"Remove {cam['name']}? Its alerts and recordings stay in history.") == QMessageBox.Yes:
            self.db.delete_camera(cam["id"]); self.db.audit(self.user["username"], "remove camera", cam["name"]); self.restart_cameras()

    def calibrate_camera(self):
        cam = self._pick_camera()
        if not cam: return
        frame, err = dialogs.grab_frame(cam["source"])
        if frame is None:
            QMessageBox.warning(self, "Calibrate", err + "."); return
        dlg = dialogs.CalibrationDialog(frame, self)
        if dlg.exec() and dlg.result_json:
            self.db.update_camera(cam["id"], calibration=dlg.result_json)
            self.db.audit(self.user["username"], "calibrate camera", cam["name"])
            w = self.workers.get(cam["id"])
            if w: w.command("calibration", dlg.result_json)
            self.toast.show_message(f"{cam['name']} calibrated. Speeds will now show in km/h.", config.BRAND["green"])

    def toggle_recording(self):
        cam = self._pick_camera()
        w = self.workers.get(cam["id"]) if cam else None
        if not w: return
        recording = self.statuses.get(cam["id"], {}).get("recording", False)
        w.command("manual_stop" if recording else "manual_start")
        self.toast.show_message(("Stopped recording " if recording else "Recording started on ") + cam["name"], config.BRAND["blue"])

    # ------------------------------------------------------------ settings
    def open_settings(self):
        if dialogs.SettingsDialog(self.db, self).exec():
            self.settings = self.db.get_settings()
            cams = {c["id"]: c for c in self.db.cameras()}
            for cid, w in self.workers.items():
                w.command("settings", (self.settings, cams.get(cid, w.camera)))
            self.audio.stop(); self.audio.s = dict(self.settings); self.audio.start()
            self.db.audit(self.user["username"], "change settings")
            self.toast.show_message("Settings saved. Model changes apply after cameras restart.", config.BRAND["green"])
            self.restart_cameras()

    # ------------------------------------------------------------ audio
    def _audio_score(self, score: float):
        for w in self.workers.values(): w.command("audio", score)

    def _audio_status(self, text: str, healthy: bool):
        self.audio_text = text
        self.status_audio.setText(text)
        self.dashboard.set_audio(text, healthy)

    # ------------------------------------------------------------ signals from cameras
    def on_status(self, cam_id: int, st: dict):
        self.statuses[cam_id] = st
        self.last_seen[cam_id] = time.time()
        self.dashboard.update_status(self.statuses)
        self.map.set_states({cid: s.get("state", "ok") for cid, s in self.statuses.items()})
        if st.get("state") == "error" and st.get("message"):
            self.toast.show_message(st["message"], config.BRAND["red"])

    def on_bucket(self, cam_id: int, b: dict):
        self.db.add_stat(cam_id, b["minute_ts"], b["avg"], b["max"], b["congestion"])
        rows = self.db.stats_since(time.time() - 3600)
        per_min: dict[int, float] = {}
        for r in rows: per_min[r["minute_ts"]] = per_min.get(r["minute_ts"], 0) + r["vehicles_avg"]
        self.dashboard.set_chart([per_min[k] for k in sorted(per_min)][-60:])

    def on_recording(self, r: dict):
        self.db.add_recording(r)
        self.gallery.refresh()

    def on_alert(self, a: dict):
        aid = self.db.add_alert(a)
        self._log_jsonl(a)
        self._present(a, aid)

    def _present(self, a: dict, aid: int | None):
        self.dashboard.add_alert(a, aid)
        sev = Severity(a["severity"])
        colour = SEVERITY_COLOURS[int(sev)]
        muted = bool(self.settings["mute_alerts"])
        if a["stage"] == "confirmed" or sev == Severity.CRITICAL:
            if not muted: self._beep(3)
            self._modals.append((a, aid)); self._next_modal()
        elif sev >= Severity.HIGH:
            if not muted: self._beep(2)
            self.toast.show_message(f"{a['message']}: {a['camera_name']}", colour, 7000)
        else:
            self.toast.show_message(f"{a['message']}: {a['camera_name']}", colour)
        if self.tray and sev >= Severity.HIGH:
            self.tray.showMessage("UTWatch", f"{a['message']}: {a['camera_name']}")
        self.refresh_counts()

    def _beep(self, n: int):
        for i in range(n):
            QTimer.singleShot(i * 350, QApplication.beep)

    def _next_modal(self):
        if self._modal_open or not self._modals:
            return
        a, aid = self._modals.pop(0)
        self._modal_open = True
        dlg = dialogs.AlertModal(a, aid, can(self.role, "acknowledge"), self)
        dlg.acknowledged.connect(self.acknowledge)
        dlg.finished.connect(lambda *_: self._modal_closed())
        dlg.open()

    def _modal_closed(self):
        self._modal_open = False
        QTimer.singleShot(200, self._next_modal)

    def acknowledge(self, alert_id: int):
        if not can(self.role, "acknowledge"):
            return
        self.db.acknowledge(alert_id, self.user["username"])
        self.db.audit(self.user["username"], "acknowledge alert", str(alert_id))
        self.refresh_counts()

    def refresh_counts(self):
        midnight = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        confirmed = self.db.query_alerts(since=midnight, limit=100000)
        today = sum(1 for a in confirmed if a["stage"] == "confirmed")
        waiting = sum(1 for a in self.db.query_alerts(since=time.time() - 86400, limit=100000)
                      if a["stage"] == "confirmed" and not a["ack_by"])
        self.dashboard.set_counts(today, waiting)

    def send_test_alert(self):
        a = {"ts": time.time(), "camera_id": None, "camera_name": "Test camera", "type": "accident", "severity": 4,
             "stage": "confirmed", "incident_id": "test", "message": "Test alert: accident confirmed", "details": {}}
        self._present(a, None)

    # ------------------------------------------------------------ housekeeping
    def _watchdog(self):
        """Restart a camera worker that has died or has stopped reporting."""
        cams = {c["id"]: c for c in self.db.cameras()}
        for cid, w in list(self.workers.items()):
            stalled = time.time() - self.last_seen.get(cid, time.time()) > 20
            if (not w.isRunning() or stalled) and cid in cams and not w._stop:
                logging.getLogger("utwatch").warning("Restarting camera %s (%s)", cams[cid]["name"], "stalled" if w.isRunning() else "stopped")
                w.stop(); w.wait(3000)
                nw = CameraWorker(cams[cid], self.settings, config.sub_dir("recordings"), self)
                for sig, slot in ((nw.frame, self.dashboard.update_frame), (nw.alert, self.on_alert), (nw.bucket, self.on_bucket),
                                  (nw.recording, self.on_recording), (nw.status, self.on_status)):
                    sig.connect(slot)
                self.workers[cid] = nw
                self.last_seen[cid] = time.time()
                nw.start()
                self.db.audit(None, "camera restarted by watchdog", cams[cid]["name"])

    def _log_jsonl(self, a: dict):
        """Append every alert to a JSON Lines file (rotated at 5 MB) as a second, human-readable record."""
        path = Path(config.sub_dir("logs")) / "alerts.jsonl"
        try:
            if path.exists() and path.stat().st_size > 5_000_000:
                path.replace(path.with_name("alerts.1.jsonl"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"schema": 1, **a}, default=str) + "\n")
        except OSError:
            logging.getLogger("utwatch").exception("Could not write alerts.jsonl")

    def _tick(self):
        self._watchdog()
        names = {c["id"]: c["name"] for c in self.db.cameras()}
        if self.tabs.currentWidget() is self.diag:
            self.diag.update_all(names, self.statuses, self.audio_text)

    def _tab_changed(self, i):
        w = self.tabs.widget(i)
        if w is self.history: self.history.refresh()
        elif w is self.gallery: self.gallery.refresh()
        elif w is self.map: self.map.reload()
        elif w is self.admin: self.admin.refresh()

    def enforce_storage(self):
        n = self.db.enforce_storage(self.settings["storage_quota_gb"], self.settings["retention_days"])
        if n:
            self.db.audit(None, "storage clean-up", f"{n} recordings removed")
            self.gallery.refresh()

    def maybe_scheduled_reports(self):
        """Create a daily report once per day, and a weekly one on Mondays, saved to the reports folder."""
        folder = config.sub_dir("reports")
        today = dt.date.today()
        now = time.time()
        daily = folder / f"daily_{today.isoformat()}.pdf"
        if not daily.exists():
            save_report_pdf(reports.build_report(self.db, now - 86400, now, "Daily traffic report"), str(daily))
        weekly = folder / f"weekly_{today.isoformat()}.pdf"
        if today.weekday() == 0 and not weekly.exists():
            save_report_pdf(reports.build_report(self.db, now - 7 * 86400, now, "Weekly traffic report"), str(weekly))

    def closeEvent(self, e):
        self.audio.stop()
        self.stop_cameras()
        self.db.audit(self.user["username"], "sign out")
        super().closeEvent(e)
