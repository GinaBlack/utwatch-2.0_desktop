"""Entry point: set-up wizard on first run, sign in, then the main window."""
from __future__ import annotations
import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from . import config
from .db import Database
from .ui import theme
from .ui.dialogs import LoginDialog, SetupWizard
from .ui.main_window import MainWindow


def make_icon() -> QIcon:
    pm = QPixmap(128, 128); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(config.BRAND["navy"])); p.setPen(Qt.NoPen); p.drawRoundedRect(4, 4, 120, 120, 26, 26)
    p.setBrush(QColor(config.BRAND["blue"])); p.drawEllipse(34, 30, 60, 60)
    p.setBrush(QColor(config.BRAND["navy"])); p.drawEllipse(52, 48, 24, 24)
    p.setPen(QPen(QColor(config.BRAND["orange"]), 8, Qt.SolidLine, Qt.RoundCap)); p.drawArc(26, 22, 76, 76, 40 * 16, 90 * 16)
    p.setPen(QPen(QColor(config.BRAND["green"]), 8, Qt.SolidLine, Qt.RoundCap)); p.drawArc(26, 22, 76, 76, 230 * 16, 80 * 16)
    p.end()
    return QIcon(pm)


def setup_logging():
    logging.basicConfig(filename=str(config.sub_dir("logs") / "utwatch.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    def hook(kind, value, tb):
        logging.getLogger("utwatch").error("Unhandled error", exc_info=(kind, value, tb))
        QMessageBox.critical(None, "UTWatch", f"Something went wrong: {value}\nDetails are in the log file in {config.sub_dir('logs')}.")
    sys.excepthook = hook


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyleSheet(theme.stylesheet())
    setup_logging()
    icon = make_icon()
    app.setWindowIcon(icon)
    db = Database()
    if db.user_count() == 0:
        if SetupWizard(db).exec() != SetupWizard.Accepted:
            return 0
    login = LoginDialog(db)
    if login.exec() != LoginDialog.Accepted:
        return 0
    win = MainWindow(db, login.user, icon)
    win.show()
    code = app.exec()
    db.close()
    return code
