"""Dialogs: sign in, first-run wizard, camera, settings, calibration, alert acknowledgement, users."""
from __future__ import annotations
import time

import cv2
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGridLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget, QWizard, QWizardPage)

from .. import config, plugins
from ..db import AuthError
from ..models import ROLES, Severity
from ..pipeline.calibration import Calibration
from ..pipeline.source import VideoSource
from .theme import SEVERITY_COLOURS


def _error_label() -> QLabel:
    lb = QLabel("")
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color: {config.BRAND['red']};")
    return lb


def grab_frame(source: str):
    """Open a source briefly and return one frame, or None."""
    src = VideoSource(source, loop_files=False)
    if not src.open():
        return None, src.last_error
    frame = None
    for _ in range(5):
        ok, frame = src.read()
        if ok:
            break
    src.release()
    return (frame, "") if frame is not None else (None, "The source opened but gave no picture")


class LoginDialog(QDialog):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db, self.user = database, None
        self.setWindowTitle("Sign in to UTWatch")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        title = QLabel("UTWatch"); title.setObjectName("h1")
        sub = QLabel("Urban traffic watch"); sub.setObjectName("dim")
        form = QFormLayout()
        self.name = QLineEdit(); self.name.setPlaceholderText("Username")
        self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.Password); self.pw.setPlaceholderText("Password")
        form.addRow("Username", self.name); form.addRow("Password", self.pw)
        self.err = _error_label()
        btn = QPushButton("Sign in"); btn.setObjectName("primary"); btn.setDefault(True)
        btn.clicked.connect(self._try)
        register = QPushButton("Create account...")
        register.clicked.connect(self._register)
        for w in (title, sub): lay.addWidget(w)
        lay.addSpacing(8); lay.addLayout(form); lay.addWidget(self.err); lay.addWidget(btn); lay.addWidget(register)
        self.pw.returnPressed.connect(self._try)

    def _try(self):
        try:
            self.user = self.db.authenticate(self.name.text(), self.pw.text())
            self.db.audit(self.user["username"], "sign in")
            self.accept()
        except AuthError as exc:
            self.err.setText(str(exc))
            self.pw.clear()
            self.pw.setFocus()

    def _register(self):
        dlg = RegistrationDialog(self.db, self)
        if dlg.exec():
            self.name.setText(dlg.name.text().strip())
            self.pw.setFocus()


class _AdminPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Create the administrator account")
        self.setSubTitle("This account manages cameras, settings and other users.")
        form = QFormLayout(self)
        self.user = QLineEdit(); self.pw = QLineEdit(); self.pw2 = QLineEdit()
        self.admin_key = QLineEdit(); self.officer_key = QLineEdit(); self.responder_key = QLineEdit()
        for e in (self.pw, self.pw2, self.admin_key, self.officer_key, self.responder_key):
            e.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.user); form.addRow("Password", self.pw); form.addRow("Repeat password", self.pw2)
        form.addRow("Administrator signup key", self.admin_key)
        form.addRow("Traffic officer signup key", self.officer_key)
        form.addRow("Responder signup key", self.responder_key)
        self.err = _error_label(); form.addRow(self.err)

    def validatePage(self):
        from ..security import check_password_rules
        try:
            if not self.user.text().strip():
                raise ValueError("Enter a username")
            check_password_rules(self.pw.text())
            if self.pw.text() != self.pw2.text():
                raise ValueError("The two passwords do not match")
            for key in (self.admin_key.text(), self.officer_key.text(), self.responder_key.text()):
                check_password_rules(key)
        except ValueError as exc:
            self.err.setText(str(exc)); return False
        return True


class _CameraPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Add your first camera")
        self.setSubTitle("Choose a video file, a webcam number such as 0, or a stream address (rtsp://...). You can skip this and add cameras later.")
        form = QFormLayout(self)
        self.name = QLineEdit("Camera 1"); self.src = QLineEdit(); self.src.setPlaceholderText("0, a video file, or rtsp://...")
        browse = QPushButton("Choose file..."); browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.src); row.addWidget(browse)
        form.addRow("Name", self.name); form.addRow("Source", row)
        self.err = _error_label(); form.addRow(self.err)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose a video", "", "Video (*.mp4 *.avi *.mov *.mkv);;All files (*)")
        if p: self.src.setText(p)

    def validatePage(self):
        if self.src.text().strip():
            frame, err = grab_frame(self.src.text())
            if frame is None:
                self.err.setText(err + ". Check the path or address, or clear it to skip."); return False
        return True


class SetupWizard(QWizard):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self.setWindowTitle("Set up UTWatch")
        self.setWizardStyle(QWizard.ModernStyle)
        self.admin, self.cam = _AdminPage(), _CameraPage()
        self.addPage(self.admin); self.addPage(self.cam)
        done = QWizardPage(); done.setTitle("Ready")
        done.setSubTitle("UTWatch will open on the dashboard. Use Tools, Send test alert to check alerts and sound, and Cameras, Calibrate to enable speed measurement.")
        self.addPage(done)

    def accept(self):
        try:
            self.db.create_user(self.admin.user.text(), self.admin.pw.text(), "administrator")
            owner_id = self.db.owner_user_id()
            self.db.set_registration_key("administrator", self.admin.admin_key.text(), owner_id)
            self.db.set_registration_key("officer", self.admin.officer_key.text(), owner_id)
            self.db.set_registration_key("responder", self.admin.responder_key.text(), owner_id)
            if self.cam.src.text().strip():
                self.db.add_camera(self.cam.name.text(), self.cam.src.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Setup", str(exc)); return
        super().accept()


class CameraDialog(QDialog):
    def __init__(self, database, camera: dict | None = None, parent=None):
        super().__init__(parent)
        self.db, self.camera = database, camera
        self.setWindowTitle("Edit camera" if camera else "Add camera")
        self.setMinimumWidth(460)
        c = camera or {}
        form = QFormLayout()
        self.name = QLineEdit(c.get("name", "")); self.src = QLineEdit(c.get("source", ""))
        browse = QPushButton("Choose file..."); browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.src); row.addWidget(browse)
        self.lat = QLineEdit("" if c.get("lat") is None else str(c["lat"])); self.lon = QLineEdit("" if c.get("lon") is None else str(c["lon"]))
        self.limit = QLineEdit("" if c.get("speed_limit_kmh") is None else str(c["speed_limit_kmh"]))
        self.enabled = QCheckBox("Camera is on"); self.enabled.setChecked(bool(c.get("enabled", 1)))
        form.addRow("Name", self.name); form.addRow("Source", row)
        form.addRow("Latitude", self.lat); form.addRow("Longitude", self.lon)
        form.addRow("Speed limit (km/h)", self.limit); form.addRow("", self.enabled)
        self.err = _error_label()
        self.cal_btn = QPushButton("Calibrate speed..."); self.cal_btn.clicked.connect(self._calibrate)
        self.cal_state = QLabel("Calibrated" if c.get("calibration") else "Not calibrated: speeds are not shown")
        self.cal_state.setObjectName("dim")
        self.new_cal = c.get("calibration")
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("primary")
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(form)
        r2 = QHBoxLayout(); r2.addWidget(self.cal_btn); r2.addWidget(self.cal_state, 1); lay.addLayout(r2)
        lay.addWidget(self.err); lay.addWidget(bb)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose a video", "", "Video (*.mp4 *.avi *.mov *.mkv);;All files (*)")
        if p: self.src.setText(p)

    def _calibrate(self):
        frame, err = grab_frame(self.src.text())
        if frame is None:
            self.err.setText((err or "Enter a source first") + "."); return
        dlg = CalibrationDialog(frame, self)
        if dlg.exec() and dlg.result_json:
            self.new_cal = dlg.result_json
            self.cal_state.setText(f"Calibrated (check: {dlg.check_text})" if dlg.check_text else "Calibrated")

    def _save(self):
        try:
            limit = float(self.limit.text()) if self.limit.text().strip() else None
            if limit is not None and not 5 <= limit <= 200:
                raise ValueError("Speed limit must be between 5 and 200 km/h")
            if not self.src.text().strip():
                raise ValueError("Enter a source")
            if self.camera:
                self.db.update_camera(self.camera["id"], name=self.name.text().strip(), source=self.src.text().strip(),
                                      enabled=int(self.enabled.isChecked()), lat=self.lat.text() or None, lon=self.lon.text() or None,
                                      speed_limit_kmh=limit, calibration=self.new_cal)
            else:
                cid = self.db.add_camera(self.name.text(), self.src.text(), self.lat.text() or None, self.lon.text() or None, limit)
                if self.new_cal:
                    self.db.update_camera(cid, calibration=self.new_cal)
        except Exception as exc:
            self.err.setText(str(exc)); return
        self.accept()


class _Clicker(QLabel):
    clicked = Signal(float, float)       # image coordinates

    def __init__(self, frame_bgr):
        super().__init__()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        self.base = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.img_size = (w, h)
        self.scale = min(1.0, 760 / w)
        self.points: list[tuple[float, float, str]] = []
        self.setFixedSize(int(w * self.scale), int(h * self.scale))
        self.setCursor(Qt.CrossCursor)
        self._render()

    def mousePressEvent(self, e):
        self.clicked.emit(e.position().x() / self.scale, e.position().y() / self.scale)

    def _render(self):
        pm = QPixmap.fromImage(self.base).scaled(self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p = QPainter(pm)
        pts = [q for q in self.points if q[2] == "ref"]
        p.setPen(QPen(QColor(config.BRAND["blue"]), 2))
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            if len(pts) > 1 and (i < len(pts) - 1 or len(pts) == 4):
                p.drawLine(QPointF(a[0] * self.scale, a[1] * self.scale), QPointF(b[0] * self.scale, b[1] * self.scale))
        for n, (x, y, kind) in enumerate(self.points):
            col = QColor(config.BRAND["blue"] if kind == "ref" else config.BRAND["orange"])
            p.setPen(QPen(col, 2)); p.setBrush(col)
            p.drawEllipse(QPointF(x * self.scale, y * self.scale), 5, 5)
            p.setPen(QColor("white")); p.drawText(QPointF(x * self.scale + 8, y * self.scale - 6), str(n + 1) if kind == "ref" else "check")
        p.end()
        self.setPixmap(pm)


class CalibrationDialog(QDialog):
    """Click the four corners of a road rectangle of known size (top-left, top-right, bottom-right, bottom-left),
    then click two more points a known distance apart to check the result."""

    def __init__(self, frame_bgr, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate speed")
        self.cal: Calibration | None = None
        self.result_json, self.check_text = None, ""
        self.view = _Clicker(frame_bgr)
        self.view.clicked.connect(self._click)
        self.step = QLabel("Step 1: click the four corners of a road rectangle you can measure: top-left, top-right, bottom-right, bottom-left.")
        self.step.setWordWrap(True)
        self.w = QDoubleSpinBox(); self.w.setRange(0.5, 200); self.w.setValue(3.5); self.w.setSuffix(" m")
        self.l = QDoubleSpinBox(); self.l.setRange(0.5, 500); self.l.setValue(10.0); self.l.setSuffix(" m")
        self.known = QDoubleSpinBox(); self.known.setRange(0.5, 500); self.known.setValue(5.0); self.known.setSuffix(" m")
        form = QFormLayout()
        form.addRow("Rectangle width (top edge)", self.w); form.addRow("Rectangle length (left edge)", self.l)
        form.addRow("Known distance for the check", self.known)
        self.out = QLabel(""); self.out.setWordWrap(True)
        self.reset = QPushButton("Start again"); self.reset.clicked.connect(self._reset)
        self.save = QPushButton("Save calibration"); self.save.setObjectName("primary"); self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        row = QHBoxLayout(); row.addWidget(self.reset); row.addStretch(1); row.addWidget(self.save)
        lay = QVBoxLayout(self)
        lay.addWidget(self.step); lay.addWidget(self.view); lay.addLayout(form); lay.addWidget(self.out); lay.addLayout(row)

    def _reset(self):
        self.view.points.clear(); self.cal = None; self.save.setEnabled(False); self.out.setText("")
        self.step.setText("Step 1: click the four corners of a road rectangle you can measure: top-left, top-right, bottom-right, bottom-left.")
        self.view._render()

    def _click(self, x, y):
        ref = [p for p in self.view.points if p[2] == "ref"]
        chk = [p for p in self.view.points if p[2] == "check"]
        if len(ref) < 4:
            self.view.points.append((x, y, "ref"))
            if len(ref) + 1 == 4:
                try:
                    self.cal = Calibration.from_points([(p[0], p[1]) for p in self.view.points], self.w.value(), self.l.value(), self.view.img_size)
                except ValueError as exc:
                    self.out.setText(str(exc)); return
                self.save.setEnabled(True)
                self.step.setText("Step 2 (recommended): click two points a known distance apart to check the result, or save now.")
        elif len(chk) < 2:
            self.view.points.append((x, y, "check"))
            if len(chk) + 1 == 2 and self.cal:
                a, b = [p for p in self.view.points if p[2] == "check"]
                d = self.cal.distance_m((a[0], a[1]), (b[0], b[1]))
                err = abs(d - self.known.value()) / self.known.value() * 100
                self.check_text = f"{d:.1f} m measured against {self.known.value():.1f} m, {err:.0f}% off"
                self.out.setText(f"Check: {self.check_text}. " + ("Good." if err <= 10 else "Over 10% off: start again with more careful clicks."))
        self.view._render()

    def _save(self):
        if self.cal:
            self.result_json = self.cal.to_json()
            self.accept()


class SettingsDialog(QDialog):
    OBJECT_CLASSES = ("car", "truck", "bus", "motorcycle", "person", "animal", "vehicle", "object", "accident")
    GROUPS = {
        "Analysis": ["analysis_fps", "analysis_width", "window_seconds", "window_hop_seconds", "fusion_rule"],
        "Incident rules": ["stationary_speed", "stationary_seconds", "aftermath_seconds", "overspeed_frames",
                           "congestion_medium", "congestion_high"],
        "Alerts and recording": ["alert_cooldown_seconds", "mute_alerts", "record_fps",
                                 "storage_quota_gb", "retention_days"],
        "Models": ["detector", "detector_weights", "temporal", "temporal_weights", "audio", "audio_weights",
                   "audio_scaler_weights"],
        "Object display": ["hidden_classes"],
        "Cloud": [],
    }

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self.setWindowTitle("Settings")
        self.resize(560, 560)
        self.values = database.get_settings()
        self.editors: dict = {}
        self.class_checks = {}
        hidden = set(filter(None, self.values.get("hidden_classes", "").split(",")))
        tabs = QTabWidget()
        avail = plugins.available()
        choices = {"fusion_rule": config.FUSION_RULES, "detector": avail["detector"], "temporal": avail["temporal"], "audio": avail["audio"]}
        for group, keys in self.GROUPS.items():
            page = QWidget(); form = QFormLayout(page)
            if group == "Cloud":
                for text in ("Cloud sync is not connected. All data remains on this computer.",
                             "The future implementation is planned to include incident metadata, snapshots, and recordings; provider and retention settings are pending the storage design discussion."):
                    info = QLabel(text); info.setWordWrap(True); form.addRow(info)
            for k in keys:
                typ, lo, hi, _d, desc = config.SETTING_SPECS[k]
                if k == "hidden_classes":
                    box = QWidget(); row = QGridLayout(box); row.setContentsMargins(0, 0, 0, 0)
                    for ix, label in enumerate(self.OBJECT_CLASSES):
                        check = QCheckBox(label.capitalize())
                        check.setChecked(label not in hidden)
                        self.class_checks[label] = check
                        row.addWidget(check, ix // 4, ix % 4)
                    self.editors[k] = box
                    form.addRow("Visible classes (detection remains active)", box)
                    continue
                if k in choices:
                    w = QComboBox(); w.addItems(list(choices[k])); w.setCurrentText(str(self.values[k]))
                elif k == "mute_alerts":
                    w = QCheckBox(); w.setChecked(bool(self.values[k]))
                elif typ is int:
                    w = QSpinBox(); w.setRange(lo, hi); w.setValue(int(self.values[k]))
                elif typ is float:
                    w = QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(3 if hi <= 1 else 1); w.setSingleStep(0.1 if hi > 1 else 0.01)
                    w.setValue(float(self.values[k]))
                else:
                    w = QLineEdit(str(self.values[k]))
                w.setToolTip(desc)
                self.editors[k] = w
                form.addRow(desc, w)
            tabs.addTab(page, group)
        self.err = _error_label()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs); lay.addWidget(self.err); lay.addWidget(bb)

    def _read(self, k):
        w = self.editors[k]
        if k == "hidden_classes":
            return ",".join(label for label, check in self.class_checks.items() if not check.isChecked())
        if isinstance(w, QComboBox): return w.currentText()
        if isinstance(w, QCheckBox): return int(w.isChecked())
        if isinstance(w, (QSpinBox, QDoubleSpinBox)): return w.value()
        return w.text()

    def _save(self):
        try:
            new = {k: config.validate_setting(k, self._read(k)) for k in self.editors}
            if new["congestion_high"] <= new["congestion_medium"]:
                raise ValueError("Heavy congestion must start above medium congestion")
            if new["window_hop_seconds"] > new["window_seconds"]:
                raise ValueError("The hop cannot be longer than the window")
        except ValueError as exc:
            self.err.setText(str(exc)); return
        for k, v in new.items():
            self.db.set_setting(k, v)
        self.accept()


class AlertModal(QDialog):
    """Severe incidents block until an operator with permission acknowledges them."""
    acknowledged = Signal(int)

    def __init__(self, alert: dict, alert_id: int | None, can_ack: bool, parent=None):
        super().__init__(parent)
        self.alert_id, self.can_ack, self._ack = alert_id, can_ack, False
        self.setWindowTitle("Accident confirmed" if alert["stage"] == "confirmed" else "Alert")
        self.setModal(True)
        col = SEVERITY_COLOURS[int(alert["severity"])]
        lay = QVBoxLayout(self)
        band = QFrame(); band.setFixedHeight(8); band.setStyleSheet(f"background:{col};")
        head = QLabel(alert["message"]); head.setObjectName("h1")
        info = QLabel(f"{alert['camera_name']}\n{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert['ts']))}\n"
                      f"Severity: {Severity(alert['severity']).label}")
        lay.addWidget(band); lay.addWidget(head); lay.addWidget(info)
        row = QHBoxLayout()
        dismiss = QPushButton("Dismiss" if can_ack else "Close")
        dismiss.clicked.connect(self.reject)
        row.addWidget(dismiss)
        if can_ack:
            btn = QPushButton("Acknowledge"); btn.setObjectName("danger")
            btn.clicked.connect(self._done)
            row.addWidget(btn)
        lay.addLayout(row)
        self.setMinimumWidth(380)

    def _done(self):
        self._ack = True
        if self.can_ack and self.alert_id is not None:
            self.acknowledged.emit(self.alert_id)
        self.accept()

    def reject(self):
        super().reject()


class RegistrationDialog(QDialog):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self.setWindowTitle("Create UTWatch account")
        form = QFormLayout()
        self.name = QLineEdit()
        self.role = QComboBox(); self.role.addItems(ROLES); self.role.setCurrentText("officer")
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.Password)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.repeat = QLineEdit(); self.repeat.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.name); form.addRow("Operator role", self.role)
        form.addRow("Role signup key", self.key)
        form.addRow("Password", self.password); form.addRow("Repeat password", self.repeat)
        self.err = _error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(self.err); layout.addWidget(buttons)

    def _save(self):
        try:
            if self.password.text() != self.repeat.text():
                raise ValueError("The two passwords do not match")
            self.db.register_user(self.name.text(), self.password.text(), self.role.currentText(), self.key.text())
        except (ValueError, AuthError) as exc:
            self.err.setText(str(exc)); return
        self.accept()


class UserDialog(QDialog):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self.setWindowTitle("Add user")
        form = QFormLayout()
        self.name = QLineEdit(); self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.Password)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.Password)
        self.role = QComboBox(); self.role.addItems(ROLES); self.role.setCurrentText("officer")
        self.err = _error_label()
        form.addRow("Username", self.name); form.addRow("Temporary password", self.pw)
        form.addRow("Role signup key", self.key); form.addRow("Role", self.role)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(self.err); lay.addWidget(bb)

    def _save(self):
        try:
            self.db.register_user(self.name.text(), self.pw.text(), self.role.currentText(), self.key.text())
        except (ValueError, AuthError) as exc:
            self.err.setText(str(exc)); return
        self.accept()


class RegistrationKeysDialog(QDialog):
    def __init__(self, database, user: dict, parent=None):
        super().__init__(parent)
        self.db, self.user = database, user
        self.setWindowTitle("Role signup keys")
        form = QFormLayout()
        self.fields = {}
        for role, label in (("administrator", "Administrator"), ("officer", "Traffic officer"),
                            ("responder", "Responder")):
            edit = QLineEdit(); edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText("Configured — enter a replacement" if database.registration_key_configured(role)
                                    else "Not configured — enter an initial key")
            self.fields[role] = edit
            form.addRow(f"{label} key", edit)
        self.err = _error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("Only the first administrator can change these keys."))
        layout.addLayout(form); layout.addWidget(self.err); layout.addWidget(buttons)

    def _save(self):
        try:
            for role, field in self.fields.items():
                if field.text():
                    self.db.set_registration_key(role, field.text(), self.user["id"])
        except (ValueError, AuthError) as exc:
            self.err.setText(str(exc)); return
        self.accept()
