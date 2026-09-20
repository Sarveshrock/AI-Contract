"""Classic theme: the silver, bevelled "Windows 9x" look (raised buttons, sunken inputs, navy title bars)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.ui.theme.tokens import Theme

FACE = "#C0C0C0"
WHITE = "#FFFFFF"
SHADOW = "#808080"
DARK = "#404040"
NAVY = "#0A24A8"
NAVY_DARK = "#000080"
TITLE_TEXT = "#FFFFFF"

RAISED = f"border: 2px solid; border-color: {WHITE} {DARK} {DARK} {WHITE};"
SUNKEN = f"border: 2px solid; border-color: {SHADOW} {WHITE} {WHITE} {SHADOW};"
PRESSED = f"border: 2px solid; border-color: {DARK} {WHITE} {WHITE} {DARK};"

FONT = 'Tahoma, "Segoe UI", "MS Sans Serif", Arial, sans-serif'
MONO = '"Lucida Console", "Courier New", Consolas, monospace'

# solid badge colours: (background, text)
BADGES = {"success": ("#009A00", "#FFFFFF"), "warning": ("#FFD800", "#000000"), "danger": ("#D40000", "#FFFFFF"), "info": ("#1030D0", "#FFFFFF"),
          "neutral": ("#808080", "#FFFFFF"), "violet": ("#6A2CC8", "#FFFFFF"), "cyan": (NAVY, "#FFFFFF")}


def _assets() -> dict[str, str]:
    """Small PNG glyphs (combo arrow, check mark, radio dot) that plain QSS cannot draw. Empty if Qt has no GUI app yet."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap, QPolygonF

    if QGuiApplication.instance() is None:
        return {}
    out_dir = Path(tempfile.gettempdir()) / "contractlens_theme"
    out_dir.mkdir(exist_ok=True)
    paths: dict[str, str] = {}

    def make(name: str, w: int, h: int, draw) -> None:
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        draw(p)
        p.end()
        f = out_dir / f"{name}.png"
        pm.save(str(f))
        paths[name] = f.as_posix()

    def arrow(p: QPainter) -> None:
        p.setBrush(QColor("#000000"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(9, 0), QPointF(4.5, 5)]))

    def check(p: QPainter) -> None:
        p.setPen(QPen(QColor("#000000"), 2))
        p.drawLine(2, 5, 4, 8)
        p.drawLine(4, 8, 9, 2)

    def dot(p: QPainter) -> None:
        p.setBrush(QColor("#000000"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(3, 3, 5, 5)

    make("arrow_down", 9, 5, arrow)
    make("check", 11, 11, check)
    make("radio_dot", 11, 11, dot)
    return paths


def build_classic_qss(t: Theme) -> str:
    a = _assets()
    arrow = f"image: url({a['arrow_down']});" if a else ""
    check = f"image: url({a['check']});" if a else f"background: {NAVY};"
    dot = f"image: url({a['radio_dot']});" if a else f"background: {NAVY};"
    badge = "\n".join(f'QLabel[badge="true"][tone="{n}"] {{ background: {bg}; color: {fg}; }}' for n, (bg, fg) in BADGES.items())
    dots = "\n".join(f'QLabel[dot="true"][tone="{n}"] {{ background: {bg}; }}' for n, (bg, _fg) in BADGES.items())
    return f"""
* {{ font-family: {FONT}; font-size: 12px; color: #000000; outline: none; }}
QWidget {{ background: transparent; }}
QMainWindow, QDialog {{ background: {FACE}; }}
QToolTip {{ background: #FFFFE1; color: #000000; border: 1px solid #000000; padding: 2px 5px; }}

/* ---- typography ---- */
QLabel[role="h1"] {{ font-size: 18px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 15px; font-weight: 700; }}
QLabel[role="h3"] {{ font-size: 13px; font-weight: 700; }}
QLabel[role="eyebrow"] {{ font-size: 11px; font-weight: 700; color: {NAVY}; }}
QLabel[role="muted"] {{ color: #202020; }}
QLabel[role="faint"] {{ color: #404040; font-size: 11px; }}
QLabel[role="kpi"] {{ font-size: 26px; font-weight: 700; color: {NAVY}; }}
QLabel[role="mono"] {{ font-family: {MONO}; font-size: 11px; color: #202020; }}
QLabel[role="quote"] {{ font-family: {MONO}; font-size: 11px; color: #000000; background: #FFFFEE; {SUNKEN} padding: 4px 6px; }}
QLabel[role="titlebar"] {{ color: {TITLE_TEXT}; font-size: 15px; font-weight: 700; letter-spacing: 1px; background: transparent; }}
QWidget#ScreenHeader {{ background: {NAVY}; }}
QWidget[panelHeader="true"] {{ background: {NAVY}; }}
QWidget[panelHeader="true"] QLabel {{ color: {TITLE_TEXT}; background: transparent; }}
QWidget[panelHeader="true"] QLabel[role="faint"] {{ color: #C8D2FF; }}

/* ---- panels ---- */
QFrame[panel="glass"] {{ background: {FACE}; {RAISED} }}
QFrame[panel="sunken"] {{ background: {WHITE}; {SUNKEN} }}
QFrame[panel="flat"] {{ background: transparent; border: none; }}
QFrame[panel="card"] {{ background: {FACE}; {RAISED} }}
QFrame[panel="card"]:hover {{ background: #D4D4D4; }}
QFrame[panel="card"][selected="true"] {{ background: #C4D0FF; border-color: {NAVY} {NAVY_DARK} {NAVY_DARK} {NAVY}; }}
QFrame[panel="stat"] {{ background: {FACE}; {RAISED} }}
QFrame[panel="stat"]:hover {{ background: #D4D4D4; }}
QFrame[panel="stat"][active="true"] {{ background: #E6E6E6; {PRESSED} }}
QFrame[panel="fact"] {{ background: {WHITE}; {SUNKEN} }}
QFrame[panel="callout"] {{ background: {WHITE}; {SUNKEN} }}
QFrame[panel="hero"] {{ background: #D6DEFF; {RAISED} }}
QFrame[panel="composer"] {{ background: {WHITE}; {SUNKEN} }}
QFrame[panel="suggest"] {{ background: {FACE}; {RAISED} }}
QFrame[panel="suggest"]:hover {{ background: #D4D4D4; }}
QFrame[divider="true"] {{ background: {SHADOW}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#TopBar {{ background: {FACE}; border: none; border-bottom: 2px solid {SHADOW}; }}
QFrame#NavBar {{ background: {FACE}; border: none; border-right: 2px solid {SHADOW}; }}
QLabel#Wordmark {{ font-size: 20px; font-weight: 700; letter-spacing: 3px; color: {NAVY}; }}
QLabel#WordmarkSub {{ font-size: 12px; color: #000000; }}
QFrame#DemoBanner {{ background: #2F3FA8; border: none; }}
QFrame#DemoBanner QLabel {{ color: #FFFFFF; font-weight: 600; }}

/* ---- buttons ---- */
QPushButton {{ background: {FACE}; {RAISED} padding: 3px 12px; min-height: 16px; }}
QPushButton:hover {{ background: #CFCFCF; }}
QPushButton:pressed {{ {PRESSED} padding: 4px 11px 2px 13px; }}
QPushButton:focus {{ border-color: {WHITE} #000000 #000000 {WHITE}; }}
QPushButton:disabled {{ color: {SHADOW}; }}
QPushButton[variant="primary"] {{ font-weight: 700; color: {NAVY}; }}
QPushButton[variant="ghost"] {{ padding: 2px 8px; }}
QPushButton[variant="danger"] {{ color: #B00000; font-weight: 700; }}
QPushButton[variant="success"] {{ color: #006400; font-weight: 700; }}
QPushButton[variant="chip"] {{ padding: 2px 10px; }}
QPushButton[variant="chip"]:checked {{ background: #E6E6E6; {PRESSED} font-weight: 700; }}
QPushButton[nav="true"] {{ text-align: left; padding: 7px 10px; font-size: 13px; }}
QPushButton[nav="true"]:checked {{ background: {NAVY}; color: {TITLE_TEXT}; font-weight: 700; {RAISED} }}
QPushButton[nav="true"]:hover:!checked {{ background: #D4D4D4; }}
QToolButton {{ background: transparent; border: 2px solid transparent; padding: 3px; }}
QToolButton:hover {{ background: {FACE}; {RAISED} }}
QToolButton:pressed {{ {PRESSED} }}
QToolButton::menu-indicator {{ image: none; }}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{ background: {WHITE}; {SUNKEN} padding: 3px 5px; selection-background-color: {NAVY}; selection-color: #FFFFFF; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {DARK} {WHITE} {WHITE} {DARK}; }}
QLineEdit:read-only {{ background: #E8E8E8; }}
QLineEdit[search="true"] {{ padding: 4px 6px 4px 32px; }}
QComboBox {{ background: {WHITE}; {SUNKEN} padding: 2px 6px; min-height: 18px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 16px; background: {FACE}; {RAISED} }}
QComboBox::down-arrow {{ {arrow} }}
QComboBox QAbstractItemView {{ background: {WHITE}; border: 1px solid #000000; selection-background-color: {NAVY}; selection-color: #FFFFFF; outline: none; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; background: {WHITE}; {SUNKEN} }}
QCheckBox::indicator:checked {{ {check} }}
QRadioButton::indicator {{ width: 13px; height: 13px; border-radius: 7px; background: {WHITE}; {SUNKEN} }}
QRadioButton::indicator:checked {{ {dot} }}

/* ---- tables & lists ---- */
QTableView, QTreeView, QListView, QListWidget {{ background: {WHITE}; {SUNKEN} alternate-background-color: #F2F2F2; gridline-color: #D0D0D0; selection-background-color: {NAVY}; selection-color: #FFFFFF; }}
QTableView::item {{ padding: 3px 6px; border-bottom: 1px solid #E2E2E2; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ background: {NAVY}; color: #FFFFFF; }}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{ background: #DCE4FF; }}
QListWidget::item {{ padding: 4px 6px; }}
QHeaderView::section {{ background: {FACE}; color: #000000; {RAISED} padding: 3px 6px; font-size: 11px; font-weight: 700; }}
QTableCornerButton::section {{ background: {FACE}; {RAISED} }}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ background: #DEDEDE; width: 16px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {FACE}; {RAISED} min-height: 28px; }}
QScrollBar:horizontal {{ background: #DEDEDE; height: 16px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {FACE}; {RAISED} min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QScrollArea {{ border: none; background: transparent; }}

/* ---- tabs ---- */
QTabWidget::pane {{ background: {FACE}; {RAISED} top: -2px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: #B4B4B4; border: 2px solid; border-color: {WHITE} {DARK} transparent {WHITE}; padding: 4px 12px; margin-right: 1px; margin-top: 3px; }}
QTabBar::tab:selected {{ background: {FACE}; font-weight: 700; margin-top: 0; padding-top: 6px; }}
QTabBar::tab:hover:!selected {{ background: #C8C8C8; }}

/* ---- misc ---- */
QProgressBar {{ background: {WHITE}; {SUNKEN} height: 16px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {NAVY}; width: 10px; margin: 1px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QMenuBar {{ background: {FACE}; border-bottom: 1px solid {SHADOW}; padding: 1px; }}
QMenuBar::item {{ background: transparent; padding: 3px 9px; }}
QMenuBar::item:selected {{ background: {NAVY}; color: #FFFFFF; }}
QMenu {{ background: {FACE}; {RAISED} padding: 2px; }}
QMenu::item {{ padding: 4px 24px 4px 20px; }}
QMenu::item:selected {{ background: {NAVY}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: {SHADOW}; }}
QMenu::separator {{ height: 2px; background: {SHADOW}; margin: 3px 2px; }}
QGroupBox {{ border: 2px groove {SHADOW}; margin-top: 12px; padding: 12px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; background: {FACE}; }}
QStatusBar {{ background: {FACE}; }}
QStatusBar::item {{ border: 1px solid; border-color: {SHADOW} {WHITE} {WHITE} {SHADOW}; }}
QStatusBar QLabel {{ color: #000000; padding: 0 6px; }}
QMessageBox {{ background: {FACE}; }}
QCalendarWidget QWidget {{ background: {WHITE}; }}

/* ---- badges ---- */
QLabel[badge="true"] {{ padding: 0 7px; min-height: 16px; font-size: 11px; font-weight: 700; border: 2px solid; border-color: {WHITE} {DARK} {DARK} {WHITE}; }}
{badge}
QLabel[dot="true"] {{ min-width: 9px; max-width: 9px; min-height: 9px; max-height: 9px; border: 1px solid #000000; }}
{dots}
"""
