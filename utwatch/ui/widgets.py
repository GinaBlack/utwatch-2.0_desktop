"""Reusable widgets: video tile, stat card, severity list, toast, bar chart."""
from __future__ import annotations
import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QLabel, QListWidget, QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget)

from ..config import BRAND
from ..models import Severity
from .theme import INK, INK_DIM, PANEL, PANEL_RAISED, SEVERITY_COLOURS

RAIL = {"ok": BRAND["green"], "suspected": BRAND["orange"], "confirmed": BRAND["red"],
        "reconnecting": BRAND["amber"], "warning": BRAND["amber"], "stopped": "#66798C", "error": BRAND["red"]}


class VideoTile(QFrame):
    """Live view with a status rail on the left edge. Click to focus, double-click to enlarge."""
    clicked = Signal(int)
    double_clicked = Signal(int)

    def __init__(self, cam_id: int, name: str, parent=None):
        super().__init__(parent)
        self.cam_id, self.name = cam_id, name
        self.image: QImage | None = None
        self.state, self.fps, self.message = "stopped", 0.0, "Connecting"
        self.setMinimumSize(240, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setObjectName("card")
        self.setAccessibleName(f"Camera {name}")

    def set_image(self, img: QImage):
        self.image = img
        self.update()

    def set_state(self, state: str, fps: float = 0.0, message: str = ""):
        self.state, self.fps, self.message = state, fps, message
        self.setToolTip(message)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QColor("#0E151D"))
        if self.image is not None:
            sz = self.image.size().scaled(r.size(), Qt.KeepAspectRatio)
            x, y = r.x() + (r.width() - sz.width()) // 2, r.y() + (r.height() - sz.height()) // 2
            p.drawImage(QRectF(x, y, sz.width(), sz.height()), self.image)
        else:
            p.setPen(QColor(INK_DIM))
            p.drawText(r, Qt.AlignCenter, self.message or "No picture yet")
        p.fillRect(r.x(), r.y(), 6, r.height(), QColor(RAIL.get(self.state, RAIL["stopped"])))
        label = f"{self.name}   {self.fps:.0f} fps" if self.state not in ("stopped", "reconnecting", "warning") else f"{self.name}   {self.state}"
        f = QFont(self.font()); f.setPointSize(9); p.setFont(f)
        p.fillRect(r.x() + 6, r.bottom() - 22, r.width() - 6, 22, QColor(14, 21, 29, 190))
        p.setPen(QColor(INK))
        p.drawText(r.x() + 14, r.bottom() - 6, label)

    def mousePressEvent(self, e):
        self.clicked.emit(self.cam_id)

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit(self.cam_id)


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(0)
        self.value = QLabel("0"); self.value.setObjectName("statValue")
        self.caption = QLabel(title); self.caption.setObjectName("dim")
        lay.addWidget(self.value); lay.addWidget(self.caption)

    def set(self, value, colour: str | None = None):
        self.value.setText(str(value))
        self.value.setStyleSheet(f"color: {colour};" if colour else "")


class SeverityList(QListWidget):
    """Alerts, newest first, with a coloured marker per severity. Text always states the level too."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setSpacing(1)

    def add_alert(self, a: dict, alert_id: int | None = None):
        sev = Severity(a["severity"])
        stage = {"suspected": "Suspected", "confirmed": "Confirmed", "expired": "Not confirmed"}.get(a["stage"], "")
        when = time.strftime("%H:%M:%S", time.localtime(a["ts"]))
        text = f"{when}  {sev.label}  {a['camera_name']}\n{a['message']}" + (f" ({stage})" if stage and stage not in a["message"] else "")
        it = QListWidgetItem(text)
        it.setData(Qt.UserRole, alert_id)
        it.setData(Qt.UserRole + 1, a)
        pm = QPixmap(10, 10); pm.fill(QColor(SEVERITY_COLOURS[int(sev)]))
        it.setIcon(pm)
        it.setForeground(QColor(INK if a["stage"] != "expired" else INK_DIM))
        self.insertItem(0, it)
        while self.count() > 300:
            self.takeItem(self.count() - 1)

    def mark_acknowledged(self, alert_id: int, username: str):
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.UserRole) == alert_id:
                alert = item.data(Qt.UserRole + 1)
                alert["ack_by"] = username
                item.setData(Qt.UserRole + 1, alert)
                item.setText(item.text() + f"\nAcknowledged by {username}")
                return


class Toast(QLabel):
    """A short message that appears over the window and fades after a few seconds."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setMargin(10)
        self.hide()
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, colour: str = BRAND["blue"], ms: int = 4500):
        self.setText(text)
        self.setStyleSheet(f"background:{PANEL}; border:1px solid {PANEL_RAISED}; border-left:6px solid {colour}; border-radius:4px;")
        self.setFixedWidth(340)
        self.adjustSize()
        p = self.parentWidget()
        self.move(p.width() - self.width() - 20, p.height() - self.height() - 40)
        self.raise_()
        self.show()
        self._timer.start(ms)


class BarChart(QWidget):
    """Minute-by-minute per-camera averages and peaks, with congestion details on hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.minutes: list[int] = []
        self.series: dict[str, list[dict]] = {}
        self.setMinimumHeight(160)
        self.setMouseTracking(True)

    def set_data(self, minutes: list[int], series: dict[str, list[dict]]):
        self.minutes = list(minutes)
        self.series = {name: list(rows) for name, rows in series.items()}
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        bounds = self.rect().adjusted(4, 4, -4, -4)
        if not self.minutes:
            p.setPen(QColor(INK_DIM)); p.drawText(bounds, Qt.AlignCenter, "No data yet"); return
        colors = [BRAND["blue"], BRAND["orange"], BRAND["green"], "#B47ADB", "#2CB6B1", "#E6C84F"]
        rows = [r for values in self.series.values() for r in values]
        top = max(1, max((r.get("max", 0) for r in rows), default=0))
        p.setFont(QFont(self.font().family(), 7))
        legend_y = bounds.top() + 10
        left = bounds.left() + 30
        for i, name in enumerate(self.series):
            x = left + i * 95
            p.fillRect(QRectF(x, legend_y - 7, 8, 8), QColor(colors[i % len(colors)]))
            p.setPen(QColor(INK_DIM)); p.drawText(x + 12, legend_y, name[:12])
        plot = bounds.adjusted(30, 22, -2, -20)
        for tick in range(5):
            y = plot.bottom() - plot.height() * tick / 4
            value = top * tick / 4
            p.setPen(QPen(QColor("#33475E"), 1))
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            p.setPen(QColor(INK_DIM))
            p.drawText(bounds.left(), int(y + 3), f"{value:.0f}")
        count = len(self.minutes)
        step = plot.width() / count
        group_width = step * 0.78
        names = list(self.series)
        bar_width = group_width / max(1, len(names))
        for ix in range(count):
            gx = plot.left() + ix * step + (step - group_width) / 2
            for ci, name in enumerate(names):
                samples = self.series[name]
                if ix >= len(samples):
                    continue
                sample = samples[ix]
                color = QColor(colors[ci % len(colors)])
                avg_h = plot.height() * sample.get("avg", 0) / top
                max_h = plot.height() * sample.get("max", 0) / top
                x = gx + ci * bar_width
                p.fillRect(QRectF(x, plot.bottom() - avg_h, max(1, bar_width - 1), avg_h), color)
                p.setPen(QPen(color.lighter(145), 1))
                p.drawLine(int(x), int(plot.bottom() - max_h), int(x + bar_width - 1), int(plot.bottom() - max_h))
            if ix % max(1, count // 6) == 0:
                p.setPen(QColor(INK_DIM))
                p.drawText(QRectF(plot.left() + ix * step - 18, plot.bottom() + 3, 42, 14),
                           Qt.AlignHCenter, time.strftime("%H:%M", time.localtime(self.minutes[ix])))

    def mouseMoveEvent(self, event):
        if not self.minutes:
            self.setToolTip("")
            return
        plot_left = self.rect().left() + 34
        plot_width = self.rect().width() - 40
        index = int((event.position().x() - plot_left) / max(1, plot_width) * len(self.minutes))
        if not 0 <= index < len(self.minutes):
            self.setToolTip("")
            return
        lines = [time.strftime("%H:%M", time.localtime(self.minutes[index]))]
        for name, samples in self.series.items():
            if index < len(samples):
                sample = samples[index]
                lines.append(f"{name}: avg {sample.get('avg', 0):.1f}, peak {sample.get('max', 0)}, "
                             f"{sample.get('congestion', 'low')} congestion")
        self.setToolTip("\n".join(lines))
