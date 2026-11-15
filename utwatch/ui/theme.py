"""Control-room theme built from the UTWatch brand palette."""
from ..config import BRAND

SEVERITY_COLOURS = {1: BRAND["blue"], 2: BRAND["amber"], 3: BRAND["orange"], 4: BRAND["red"]}
INK = "#ECF0F1"
INK_DIM = "#9FB0BF"
BASE = "#17212D"
PANEL = BRAND["navy"]
PANEL_RAISED = "#26364A"
LINE = "#33475E"


def stylesheet() -> str:
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 10pt; color: {INK}; }}
QMainWindow, QDialog, QWidget#page {{ background: {BASE}; }}
QWidget {{ background: transparent; }}
QDialog, QWizard {{ background: {BASE}; }}
QTabWidget {{ background: {PANEL}; }}
QTabWidget::pane {{ border: 0; background: {BASE}; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar {{ background: {PANEL}; }}
QTabBar::tab {{ background: transparent; color: {INK_DIM}; padding: 9px 18px; border-bottom: 3px solid transparent; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 3px solid {BRAND['blue']}; }}
QTabBar::tab:hover {{ color: {INK}; }}
QMenuBar {{ background: {PANEL}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {PANEL_RAISED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; }}
QStatusBar {{ background: {PANEL}; color: {INK_DIM}; }}
QStatusBar QLabel {{ color: {INK_DIM}; }}
QFrame#card, QFrame#panel {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; }}
QLabel#h1 {{ font-size: 15pt; font-weight: 600; }}
QLabel#dim {{ color: {INK_DIM}; }}
QLabel#statValue {{ font-size: 20pt; font-weight: 600; }}
QPushButton {{ background: {PANEL_RAISED}; border: 1px solid {LINE}; border-radius: 5px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {BRAND['blue']}; }}
QPushButton:focus {{ border: 2px solid {BRAND['blue']}; }}
QPushButton:disabled {{ color: #66798C; }}
QPushButton#primary {{ background: {BRAND['blue']}; border-color: {BRAND['blue']}; color: #08121C; font-weight: 600; }}
QPushButton#danger {{ background: {BRAND['red']}; border-color: {BRAND['red']}; color: #1b0705; font-weight: 600; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit, QTextBrowser, QTextEdit {{
  background: {PANEL}; border: 1px solid {LINE}; border-radius: 4px; padding: 4px 6px; selection-background-color: {BRAND['blue']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid {BRAND['blue']}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {BRAND['blue']}; }}
QTableWidget, QListWidget, QTreeWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px;
  alternate-background-color: #1B2736; gridline-color: {LINE}; selection-background-color: {PANEL_RAISED}; }}
QHeaderView::section {{ background: {PANEL_RAISED}; border: 0; padding: 6px; color: {INK_DIM}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QToolTip {{ background: {PANEL}; color: {INK}; border: 1px solid {LINE}; }}
"""
