"""Main tabs: dashboard, history, recordings, reports, map, diagnostics, administration."""
from __future__ import annotations
import os
import shutil
import time
from pathlib import Path

import cv2
from PySide6.QtCore import QDateTime, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateTimeEdit, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from .. import config, reports
from ..models import Severity, can, ROLES
from .theme import INK_DIM, SEVERITY_COLOURS
from .widgets import BarChart, SeverityList, StatCard, VideoTile
from .worker import to_qimage


def _page() -> QWidget:
    w = QWidget(); w.setObjectName("page"); return w


def _ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    return t


# ---------------------------------------------------------------- dashboard
class DashboardPage(QWidget):
    acknowledge_requested = Signal(int)
    camera_selected = Signal(int)

    def __init__(self, can_ack: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.tiles: dict[int, VideoTile] = {}
        self.focus_id: int | None = None
        self.can_ack = can_ack
        root = QHBoxLayout(self); root.setContentsMargins(12, 12, 12, 12); root.setSpacing(12)
        left = QVBoxLayout()
        bar = QHBoxLayout()
        self.title = QLabel("Live cameras"); self.title.setObjectName("h1")
        self.back = QPushButton("Back to all cameras"); self.back.clicked.connect(self.show_grid); self.back.hide()
        self.audio_label = QLabel("Audio off"); self.audio_label.setObjectName("dim")
        bar.addWidget(self.title); bar.addStretch(1); bar.addWidget(self.audio_label); bar.addWidget(self.back)
        left.addLayout(bar)
        self.grid_host = QWidget(); self.grid = QGridLayout(self.grid_host); self.grid.setSpacing(10)
        self.empty = QLabel("No cameras yet. Use Cameras, Add camera to connect one."); self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignCenter)
        left.addWidget(self.grid_host, 1); left.addWidget(self.empty)
        root.addLayout(left, 1)

        side = QFrame(); side.setObjectName("panel"); side.setFixedWidth(360)
        sl = QVBoxLayout(side); sl.setContentsMargins(12, 12, 12, 12)
        cards = QGridLayout(); cards.setSpacing(8)
        self.c_cong = StatCard("Congestion")
        self.c_conf, self.c_open = StatCard("Confirmed today"), StatCard("Unacknowledged")
        for i, c in enumerate((self.c_cong, self.c_conf, self.c_open)):
            cards.addWidget(c, i // 2, i % 2)
        sl.addLayout(cards)
        sl.addWidget(QLabel("Traffic by camera — average and peak vehicles per minute")); self.chart = BarChart(); sl.addWidget(self.chart)
        sl.addWidget(QLabel("Recent alerts"))
        self.alerts = SeverityList(); sl.addWidget(self.alerts, 1)
        self.ack_btn = QPushButton("Acknowledge selected"); self.ack_btn.setObjectName("primary")
        self.ack_btn.setEnabled(False); self.ack_btn.clicked.connect(self._ack)
        self.ack_btn.setVisible(can_ack)
        sl.addWidget(self.ack_btn)
        self.alerts.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(side)

    # ---- cameras and video
    def set_cameras(self, cams: list[dict]):
        for t in self.tiles.values():
            t.setParent(None); t.deleteLater()
        self.tiles.clear()
        for c in cams:
            t = VideoTile(c["id"], c["name"])
            t.double_clicked.connect(self.focus)
            t.clicked.connect(self.camera_selected)
            self.tiles[c["id"]] = t
        self.empty.setVisible(not cams)
        self._layout()

    def _layout(self):
        while self.grid.count():
            self.grid.takeAt(0)
        shown = [t for cid, t in self.tiles.items() if self.focus_id in (None, cid)]
        for t in self.tiles.values():
            t.setVisible(t in shown)
        n = len(shown)
        cols = 1 if n <= 1 else 2 if n <= 4 else 3
        for i, t in enumerate(shown):
            self.grid.addWidget(t, i // cols, i % cols)

    def focus(self, cam_id: int):
        self.focus_id = cam_id; self.back.show(); self._layout()

    def show_grid(self):
        self.focus_id = None; self.back.hide(); self._layout()

    def update_frame(self, cam_id: int, img: QImage):
        t = self.tiles.get(cam_id)
        if t: t.set_image(img)

    def update_status(self, statuses: dict):
        worst = "low"
        order = {"low": 0, "medium": 1, "high": 2}
        for cid, st in statuses.items():
            t = self.tiles.get(cid)
            if t:
                t.set_state(st.get("state", "ok"), st.get("fps", 0), st.get("message", ""))
            if order.get(st.get("level", "low"), 0) > order[worst]:
                worst = st["level"]
        self.c_cong.set(worst.capitalize(), {"low": config.BRAND["green"], "medium": config.BRAND["amber"], "high": config.BRAND["red"]}[worst])

    def set_audio(self, text: str, healthy: bool):
        self.audio_label.setText(("Microphone: " if healthy else "") + text)
        self.audio_label.setStyleSheet(f"color: {config.BRAND['green'] if healthy else INK_DIM};")

    # ---- alerts
    def add_alert(self, a: dict, alert_id: int | None):
        self.alerts.add_alert(a, alert_id)

    def set_counts(self, confirmed_today: int, waiting: int):
        self.c_conf.set(confirmed_today, config.BRAND["red"] if confirmed_today else None)
        self.c_open.set(waiting, config.BRAND["orange"] if waiting else None)

    def set_chart(self, minutes, series):
        self.chart.set_data(minutes, series)

    def mark_acknowledged(self, alert_id: int, username: str):
        self.alerts.mark_acknowledged(alert_id, username)
        self._selection_changed()

    def _selection_changed(self):
        selected = self.alerts.selectedItems()
        self.ack_btn.setEnabled(any(
            item.data(Qt.UserRole) is not None and not (item.data(Qt.UserRole + 1) or {}).get("ack_by")
            for item in selected))

    def _ack(self):
        for it in self.alerts.selectedItems():
            aid = it.data(Qt.UserRole)
            alert = it.data(Qt.UserRole + 1) or {}
            if aid is not None and not alert.get("ack_by"):
                self.acknowledge_requested.emit(aid)


# ---------------------------------------------------------------- history
class HistoryPage(QWidget):
    PAGE = 200

    def __init__(self, database, role: str, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db, self.role, self.offset = database, role, 0
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.cam = QComboBox(); self.typ = QComboBox(); self.sev = QComboBox()
        self.typ.addItems(["All types", "accident", "stationary", "overspeed", "congestion"])
        self.sev.addItems(["Any severity", "Medium and above", "High and above", "Critical only"])
        self.since = QDateTimeEdit(QDateTime.currentDateTime().addDays(-7)); self.since.setCalendarPopup(True)
        self.since.setDisplayFormat("yyyy-MM-dd HH:mm")
        go = QPushButton("Apply filters"); go.setObjectName("primary"); go.clicked.connect(self.reset)
        for w in (QLabel("Camera"), self.cam, self.typ, self.sev, QLabel("Since"), self.since, go):
            bar.addWidget(w)
        bar.addStretch(1)
        self.export_btn = QPushButton("Export CSV..."); self.export_btn.clicked.connect(self.export)
        self.export_btn.setVisible(can(role, "export"))
        bar.addWidget(self.export_btn)
        lay.addLayout(bar)
        self.table = _table(["Time", "Camera", "Type", "Stage", "Severity", "Message", "Acknowledged by"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        nav = QHBoxLayout()
        self.prev, self.next = QPushButton("Newer"), QPushButton("Older")
        self.prev.clicked.connect(lambda: self._page(-1)); self.next.clicked.connect(lambda: self._page(1))
        self.count = QLabel(""); self.count.setObjectName("dim")
        nav.addWidget(self.prev); nav.addWidget(self.next); nav.addWidget(self.count); nav.addStretch(1)
        lay.addLayout(nav)
        self.rows: list[dict] = []
        self.reload_cameras()

    def reload_cameras(self):
        cur = self.cam.currentData()
        self.cam.clear(); self.cam.addItem("All cameras", None)
        for c in self.db.cameras():
            self.cam.addItem(c["name"], c["id"])
        i = self.cam.findData(cur)
        self.cam.setCurrentIndex(max(i, 0))

    def _filters(self):
        t = self.typ.currentText()
        return dict(camera_id=self.cam.currentData(), type_=None if t == "All types" else t,
                    min_severity=[None, 2, 3, 4][self.sev.currentIndex()], since=self.since.dateTime().toSecsSinceEpoch())

    def reset(self):
        self.offset = 0; self.refresh()

    def _page(self, d):
        self.offset = max(0, self.offset + d * self.PAGE); self.refresh()

    def refresh(self):
        self.rows = self.db.query_alerts(limit=self.PAGE, offset=self.offset, **self._filters())
        self.table.setRowCount(len(self.rows))
        for r, a in enumerate(self.rows):
            vals = [_ts(a["ts"]), a["camera_name"], a["type"].replace("_", " "), a["stage"], Severity(a["severity"]).label,
                    a["message"], a["ack_by"] or ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 4: it.setForeground(QColor(SEVERITY_COLOURS[a["severity"]]))
                self.table.setItem(r, c, it)
        for c in (0, 1, 2, 3, 4, 6):
            self.table.resizeColumnToContents(c)
        self.count.setText(f"{len(self.rows)} shown" + (f", from {self.offset + 1}" if self.offset else ""))
        self.prev.setEnabled(self.offset > 0); self.next.setEnabled(len(self.rows) == self.PAGE)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export alerts", "alerts.csv", "CSV (*.csv)")
        if path:
            rows = self.db.query_alerts(limit=100000, **self._filters())
            n = reports.export_alerts_csv(rows, path)
            self.db.audit(None, "export alerts", f"{n} rows to {path}")
            QMessageBox.information(self, "Export", f"Saved {n} alerts to {path}")


# ---------------------------------------------------------------- recordings
class GalleryPage(QWidget):
    def __init__(self, database, role: str, username: str, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db, self.role, self.username = database, role, username
        self.cap = None
        self.timer = QTimer(self, interval=40); self.timer.timeout.connect(self._tick)
        lay = QHBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)
        left = QVBoxLayout()
        self.cam = QComboBox(); self.cam.currentIndexChanged.connect(self.refresh)
        self.list = QListWidget(); self.list.setIconSize(QPixmap(160, 90).size()); self.list.setSpacing(4)
        self.list.currentItemChanged.connect(self._select)
        left.addWidget(self.cam); left.addWidget(self.list, 1)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        self.view = QLabel("Choose a recording"); self.view.setAlignment(Qt.AlignCenter); self.view.setMinimumSize(480, 300)
        self.view.setObjectName("card")
        self.info = QLabel(""); self.info.setObjectName("dim"); self.info.setWordWrap(True)
        row = QHBoxLayout()
        self.play = QPushButton("Pause"); self.play.clicked.connect(self._toggle)
        self.protect = QPushButton("Protect"); self.protect.clicked.connect(self._protect)
        self.export = QPushButton("Save a copy..."); self.export.clicked.connect(self._export)
        self.delete = QPushButton("Delete"); self.delete.setObjectName("danger"); self.delete.clicked.connect(self._delete)
        self.protect.setVisible(can(role, "protect_clip")); self.export.setVisible(can(role, "export"))
        self.delete.setVisible(can(role, "delete_clip"))
        for b in (self.play, self.protect, self.export, self.delete): row.addWidget(b)
        right.addWidget(self.view, 1); right.addWidget(self.info); right.addLayout(row)
        lay.addLayout(right, 2)
        self.recs: dict[int, dict] = {}

    def reload_cameras(self):
        cur = self.cam.currentData()
        self.cam.blockSignals(True); self.cam.clear(); self.cam.addItem("All cameras", None)
        for c in self.db.cameras(): self.cam.addItem(c["name"], c["id"])
        self.cam.setCurrentIndex(max(self.cam.findData(cur), 0)); self.cam.blockSignals(False)

    def refresh(self):
        self.list.clear(); self.recs.clear()
        for r in self.db.recordings(self.cam.currentData()):
            self.recs[r["id"]] = r
            kind = "Manual" if r["kind"] == "manual" else "Event"
            it = QListWidgetItem(f"{r['camera_name']}\n{_ts(r['ts'])}  {kind}  {r['duration'] or 0:.0f} s" + ("  protected" if r["protected"] else ""))
            it.setData(Qt.UserRole, r["id"])
            if r["thumb"] and os.path.exists(r["thumb"]):
                it.setIcon(QIcon(QPixmap(r["thumb"]).scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.list.addItem(it)

    def _current(self):
        it = self.list.currentItem()
        return self.recs.get(it.data(Qt.UserRole)) if it else None

    def _select(self, *_):
        self._stop()
        r = self._current()
        if not r: return
        if not os.path.exists(r["path"]):
            self.view.setText("The video file is missing from disk"); return
        self.cap = cv2.VideoCapture(r["path"])
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 10
        self.timer.start(int(1000 / min(30, self.fps)))
        self.play.setText("Pause")
        self.info.setText(f"{r['path']}\n{(r['size'] or 0) / 1e6:.1f} MB" + (f"  incident {r['incident_id']}" if r["incident_id"] else ""))
        self.protect.setText("Remove protection" if r["protected"] else "Protect")

    def _tick(self):
        if self.cap is None: return
        ok, f = self.cap.read()
        if not ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0); return
        img = to_qimage(f)
        self.view.setPixmap(QPixmap.fromImage(img).scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _stop(self):
        self.timer.stop()
        if self.cap: self.cap.release(); self.cap = None

    def _toggle(self):
        if self.timer.isActive(): self.timer.stop(); self.play.setText("Play")
        else: self.timer.start(int(1000 / min(30, self.fps))); self.play.setText("Pause")

    def _protect(self):
        r = self._current()
        if r:
            self.db.set_protected(r["id"], not r["protected"]); self.db.audit(self.username, "toggle protect", r["path"]); self.refresh()

    def _export(self):
        r = self._current()
        if not r: return
        dest, _ = QFileDialog.getSaveFileName(self, "Save a copy", os.path.basename(r["path"]), "Video (*.mp4)")
        if dest:
            shutil.copyfile(r["path"], dest); self.db.audit(self.username, "export clip", r["path"])

    def _delete(self):
        r = self._current()
        if r and QMessageBox.question(self, "Delete recording", "Delete this recording? This cannot be undone.") == QMessageBox.Yes:
            self._stop(); self.db.delete_recording(r["id"]); self.db.audit(self.username, "delete clip", r["path"]); self.refresh()


# ---------------------------------------------------------------- reports
class ReportsPage(QWidget):
    PERIODS = [("Last 24 hours", 86400), ("Last 7 days", 7 * 86400), ("Last 30 days", 30 * 86400)]

    def __init__(self, database, role: str, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db, self.role, self.report = database, role, None
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.period = QComboBox(); [self.period.addItem(n, s) for n, s in self.PERIODS]
        gen = QPushButton("Create report"); gen.setObjectName("primary"); gen.clicked.connect(self.generate)
        self.pdf = QPushButton("Save as PDF..."); self.pdf.clicked.connect(self.export_pdf)
        self.csv = QPushButton("Save as CSV..."); self.csv.clicked.connect(self.export_csv)
        for w in (self.period, gen, self.pdf, self.csv): bar.addWidget(w)
        bar.addStretch(1)
        self.pdf.setVisible(can(role, "export")); self.csv.setVisible(can(role, "export"))
        self.view = QTextBrowser()
        lay.addLayout(bar); lay.addWidget(self.view, 1)
        self.view.setHtml("<p>Choose a period and create a report. Reports are computed on this computer.</p>")

    def generate(self):
        now = time.time()
        self.report = reports.build_report(self.db, now - self.period.currentData(), now, f"Traffic report: {self.period.currentText().lower()}")
        self.view.setHtml(reports.report_html(self.report))

    def export_pdf(self):
        if not self.report: self.generate()
        path, _ = QFileDialog.getSaveFileName(self, "Save report", "report.pdf", "PDF (*.pdf)")
        if path: save_report_pdf(self.report, path); self.db.audit(None, "export report pdf", path)

    def export_csv(self):
        if not self.report: self.generate()
        path, _ = QFileDialog.getSaveFileName(self, "Save report", "report.csv", "CSV (*.csv)")
        if path: reports.export_report_csv(self.report, path); self.db.audit(None, "export report csv", path)


def save_report_pdf(report: dict, path: str):
    from PySide6.QtGui import QPageSize, QPdfWriter, QTextDocument
    w = QPdfWriter(path)
    w.setPageSize(QPageSize(QPageSize.A4))
    doc = QTextDocument()
    doc.setHtml(f"<body style='color:#000;background:#fff'>{reports.report_html(report)}</body>")
    doc.print_(w)


# ---------------------------------------------------------------- map
class MapCanvas(QWidget):
    placed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: QPixmap | None = None
        self.cams: list[dict] = []
        self.state: dict[int, str] = {}
        self.placing = False
        self.flash = False
        self.setMinimumSize(520, 360)
        t = QTimer(self, interval=500); t.timeout.connect(self._blink); t.start()

    def _blink(self):
        self.flash = not self.flash
        if any(s in ("suspected", "confirmed") for s in self.state.values()): self.update()

    def _rect(self) -> QRectF:
        if self.image is None: return QRectF(self.rect())
        sz = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRectF((self.width() - sz.width()) / 2, (self.height() - sz.height()) / 2, sz.width(), sz.height())

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = self._rect()
        p.fillRect(self.rect(), QColor("#0E151D"))
        if self.image is not None: p.drawPixmap(r.toRect(), self.image)
        else:
            p.setPen(QColor(INK_DIM)); p.drawText(self.rect(), Qt.AlignCenter, "Import a site map to place cameras on it")
        for c in self.cams:
            if c.get("map_x") is None: continue
            st = self.state.get(c["id"], "ok")
            col = {"ok": config.BRAND["green"], "suspected": config.BRAND["orange"], "confirmed": config.BRAND["red"],
                   "reconnecting": config.BRAND["amber"], "warning": config.BRAND["amber"],
                   "stopped": "#66798C", "error": config.BRAND["red"]}.get(st, "#66798C")
            x, y = r.x() + c["map_x"] * r.width(), r.y() + c["map_y"] * r.height()
            rad = 12 if st in ("suspected", "confirmed") and self.flash else 8
            p.setBrush(QColor(col)); p.setPen(QPen(QColor("white"), 2)); p.drawEllipse(QPointF(x, y), rad, rad)
            p.setPen(QColor("white")); p.drawText(QPointF(x + 14, y + 4), f"{c['name']} ({st})")

    def mousePressEvent(self, e):
        if self.placing and self.image is not None:
            r = self._rect()
            fx, fy = (e.position().x() - r.x()) / r.width(), (e.position().y() - r.y()) / r.height()
            if 0 <= fx <= 1 and 0 <= fy <= 1: self.placed.emit(fx, fy)


class MapPage(QWidget):
    def __init__(self, database, role: str, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db, self.role = database, role
        self.path = config.sub_dir("maps") / "site_map.png"
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.imp = QPushButton("Import site map..."); self.imp.clicked.connect(self.import_map)
        self.cam = QComboBox()
        self.place = QPushButton("Place selected camera"); self.place.setCheckable(True); self.place.toggled.connect(self._placing)
        self.note = QLabel("Works without internet. An online map can be added later."); self.note.setObjectName("dim")
        for w in (self.imp, self.cam, self.place): bar.addWidget(w)
        bar.addWidget(self.note, 1)
        admin = can(role, "manage_cameras")
        for w in (self.imp, self.cam, self.place): w.setVisible(admin)
        self.canvas = MapCanvas(); self.canvas.placed.connect(self._placed)
        lay.addLayout(bar); lay.addWidget(self.canvas, 1)
        if self.path.exists(): self.canvas.image = QPixmap(str(self.path))
        self.reload()

    def reload(self):
        cams = self.db.cameras()
        self.canvas.cams = cams
        cur = self.cam.currentData()
        self.cam.clear()
        for c in cams: self.cam.addItem(c["name"], c["id"])
        self.cam.setCurrentIndex(max(self.cam.findData(cur), 0))
        self.canvas.update()

    def set_states(self, states: dict): self.canvas.state = states; self.canvas.update()

    def import_map(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose a site map image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if p:
            pm = QPixmap(p)
            if pm.isNull(): QMessageBox.warning(self, "Site map", "That file is not a readable image."); return
            pm.save(str(self.path), "PNG"); self.canvas.image = pm; self.canvas.update()

    def _placing(self, on): self.canvas.placing = on; self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def _placed(self, fx, fy):
        cid = self.cam.currentData()
        if cid:
            self.db.update_camera(cid, map_x=fx, map_y=fy); self.place.setChecked(False); self.reload()


# ---------------------------------------------------------------- diagnostics
class DiagnosticsPage(QWidget):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db = database
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)
        self.sys = QLabel(""); self.sys.setObjectName("dim")
        self.table = _table(["Camera", "State", "Status detail", "Display fps", "Frames analysed", "Detect ms", "Track and rules ms", "Total ms",
                             "Vehicles", "Tracked", "Detector", "Temporal"])
        self.notes = QLabel(""); self.notes.setWordWrap(True); self.notes.setObjectName("dim")
        lay.addWidget(QLabel("Pipeline timings")); lay.addWidget(self.table, 1); lay.addWidget(self.sys); lay.addWidget(self.notes)

    def update_all(self, names: dict, statuses: dict, audio: str):
        self.table.setRowCount(len(statuses))
        notes = set()
        for r, (cid, st) in enumerate(sorted(statuses.items())):
            t = st.get("timings", {})
            vals = [names.get(cid, str(cid)), st.get("state", ""), st.get("message", ""), st.get("fps", ""), st.get("analysed", ""), t.get("detect", ""),
                    t.get("track_rules", ""), t.get("total", ""), st.get("vehicles", ""), st.get("tracks", ""),
                    st.get("detector", ""), st.get("temporal", "")]
            for c, v in enumerate(vals): self.table.setItem(r, c, QTableWidgetItem(str(v)))
            notes.update(st.get("notes", []))
        self.notes.setText("\n".join(sorted(notes)))
        try:
            import psutil
            p = psutil.Process()
            mem = f"app memory {p.memory_info().rss / 1e6:.0f} MB, system memory {psutil.virtual_memory().percent:.0f}% used, cpu {psutil.cpu_percent():.0f}%"
        except Exception:
            mem = "install psutil to see memory and cpu"
        size = os.path.getsize(self.db.path) / 1e6 if os.path.exists(self.db.path) else 0
        self.sys.setText(f"{mem}. Database {size:.1f} MB. Audio: {audio}")


# ---------------------------------------------------------------- administration
class AdminPage(QWidget):
    def __init__(self, database, current_user: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db, self.me = database, current_user
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(QLabel("Users"))
        self.users = _table(["Username", "Role", "Active", "Approved", "Locked"])
        row = QHBoxLayout()
        for text, fn in (("Add user...", self.add), ("Change role...", self.role), ("Enable or disable", self.toggle),
                         ("Approve", self.approve), ("Reset password...", self.reset)):
            b = QPushButton(text); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1)
        lay.addWidget(self.users, 1); lay.addLayout(row)
        from .dialogs import RegistrationKeysDialog
        self.keys_button = QPushButton("Role signup keys...")
        self.keys_button.clicked.connect(lambda: self._registration_keys(RegistrationKeysDialog))
        self.keys_button.setVisible(self.me["id"] == self.db.owner_user_id())
        row.addWidget(self.keys_button)
        lay.addWidget(QLabel("Audit log"))
        self.audit = _table(["Time", "User", "Action", "Target"]); lay.addWidget(self.audit, 1)
        self.rows: list[dict] = []
        self.refresh()

    def refresh(self):
        self.rows = self.db.list_users()
        self.users.setRowCount(len(self.rows))
        for r, u in enumerate(self.rows):
            vals = [u["username"], u["role"], "yes" if u["active"] else "no", "yes" if u["approved"] else "no",
                    "yes" if u["locked_until"] > time.time() else ""]
            for c, v in enumerate(vals): self.users.setItem(r, c, QTableWidgetItem(v))
        log = self.db.audit_log(300)
        self.audit.setRowCount(len(log))
        for r, a in enumerate(log):
            for c, v in enumerate([_ts(a["ts"]), a["username"] or "", a["action"], a["target"] or ""]):
                self.audit.setItem(r, c, QTableWidgetItem(v))
        self.audit.resizeColumnsToContents()

    def _sel(self):
        r = self.users.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def add(self):
        from .dialogs import UserDialog
        if UserDialog(self.db, self).exec():
            self.db.audit(self.me["username"], "add user"); self.refresh()

    def _registration_keys(self, dialog_type):
        if dialog_type(self.db, self.me, self).exec():
            self.db.audit(self.me["username"], "change registration keys")

    def role(self):
        u = self._sel()
        if not u: return
        role, ok = QInputDialog.getItem(self, "Change role", f"Role for {u['username']}", list(ROLES), ROLES.index(u["role"]), False)
        if ok:
            if u["id"] == self.me["id"] and role != "administrator":
                QMessageBox.warning(self, "Users", "You cannot remove your own administrator role."); return
            self.db.update_user(u["id"], role=role); self.db.audit(self.me["username"], "change role", f"{u['username']} to {role}"); self.refresh()

    def toggle(self):
        u = self._sel()
        if not u: return
        if u["id"] == self.me["id"]:
            QMessageBox.warning(self, "Users", "You cannot disable your own account."); return
        self.db.update_user(u["id"], active=0 if u["active"] else 1)
        self.db.audit(self.me["username"], "enable or disable user", u["username"]); self.refresh()

    def approve(self):
        u = self._sel()
        if u: self.db.update_user(u["id"], approved=1); self.db.audit(self.me["username"], "approve user", u["username"]); self.refresh()

    def reset(self):
        u = self._sel()
        if not u: return
        pw, ok = QInputDialog.getText(self, "Reset password", f"New password for {u['username']}")
        if ok:
            try:
                self.db.reset_password(u["id"], pw)
            except ValueError as exc:
                QMessageBox.warning(self, "Reset password", str(exc)); return
            self.db.audit(self.me["username"], "reset password", u["username"]); self.refresh()
