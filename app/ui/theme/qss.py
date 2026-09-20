"""Compiles the design tokens into one QSS stylesheet."""
from __future__ import annotations

from app.ui.theme.tokens import FONT_FAMILY, MONO_FAMILY, RADIUS, TYPE, Theme


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {int(alpha * 255)})"


def build_qss(t: Theme) -> str:
    if t.style == "classic":
        from app.ui.theme.classic import build_classic_qss

        return build_classic_qss(t)
    r = RADIUS
    return f"""
* {{ font-family: {FONT_FAMILY}; font-size: {TYPE['base']}px; color: {t.text}; outline: none; }}
QWidget {{ background: transparent; }}
QMainWindow, QDialog {{ background: {t.bg0}; }}
QToolTip {{ background: {t.panel_hi}; color: {t.text}; border: 1px solid {t.border_hi}; padding: 6px 8px; border-radius: {r['sm']}px; }}

/* ---- typography roles (set via property "role") ---- */
QLabel[role="h1"] {{ font-size: {TYPE['h1']}px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: {TYPE['xl']}px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: {TYPE['lg']}px; font-weight: 600; }}
QLabel[role="eyebrow"] {{ font-size: {TYPE['xs']}px; font-weight: 600; color: {t.cyan}; }}
QLabel[role="muted"] {{ color: {t.text_dim}; }}
QLabel[role="faint"] {{ color: {t.text_faint}; font-size: {TYPE['sm']}px; }}
QLabel[role="kpi"] {{ font-size: {TYPE['kpi']}px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: {MONO_FAMILY}; font-size: {TYPE['sm']}px; color: {t.text_dim}; }}
QLabel[role="quote"] {{ font-family: {MONO_FAMILY}; font-size: {TYPE['sm']}px; color: {t.text}; background: {t.bg1}; border-left: 2px solid {t.cyan}; padding: 8px 10px; border-radius: 4px; }}

/* ---- panels ---- */
QFrame[panel="glass"] {{ background: {_rgba(t.panel, 0.82)}; border: 1px solid {t.border}; border-radius: {r['lg']}px; }}
QFrame[panel="sunken"] {{ background: {t.bg1}; border: 1px solid {t.border}; border-radius: {r['md']}px; }}
QFrame[panel="flat"] {{ background: transparent; border: none; }}
QFrame[panel="card"] {{ background: {_rgba(t.panel, 0.9)}; border: 1px solid {t.border}; border-radius: {r['md']}px; }}
QFrame[panel="card"]:hover {{ border-color: {t.border_hi}; background: {t.panel_hi}; }}
QFrame[panel="card"][selected="true"] {{ border: 1px solid {t.cyan}; background: {_rgba(t.cyan, 0.08)}; }}
QFrame[panel="stat"] {{ background: {_rgba(t.panel, 0.9)}; border: 1px solid {t.border}; border-radius: {r['lg']}px; }}
QFrame[panel="stat"]:hover {{ border-color: {t.border_hi}; background: {t.panel_hi}; }}
QFrame[panel="stat"][active="true"] {{ border: 1px solid {t.cyan}; background: {_rgba(t.cyan, 0.10)}; }}
QFrame[panel="stat"]:focus {{ border-color: {t.focus}; }}
QFrame[panel="fact"] {{ background: {_rgba(t.bg1, 0.75)}; border: 1px solid {t.border}; border-radius: {r['md']}px; }}
QFrame[panel="callout"] {{ background: {_rgba(t.bg1, 0.8)}; border: 1px solid {t.border}; border-radius: {r['md']}px; }}
QFrame[panel="callout"]:hover {{ border-color: {t.border_hi}; }}
QFrame[panel="hero"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {_rgba(t.indigo, 0.22)}, stop:0.55 {_rgba(t.panel, 0.9)}, stop:1 {_rgba(t.cyan, 0.10)}); border: 1px solid {t.border_hi}; border-radius: {r['lg']}px; }}
QFrame[panel="composer"] {{ background: {_rgba(t.bg2, 0.95)}; border: 1px solid {t.border_hi}; border-radius: 22px; }}
QFrame[panel="suggest"] {{ background: {_rgba(t.panel, 0.85)}; border: 1px solid {t.border}; border-radius: {r['lg']}px; }}
QFrame[panel="suggest"]:hover {{ border-color: {t.cyan_dim}; background: {t.panel_hi}; }}
QFrame#TopBar {{ background: rgba(6,10,18,0.85); border: none; border-bottom: 1px solid {t.border}; }}
QFrame#NavBar {{ background: rgba(8,13,24,0.7); border: none; border-right: 1px solid {t.border}; }}
QFrame#DemoBanner {{ background: rgba(139,124,255,0.16); border: none; border-bottom: 1px solid rgba(139,124,255,0.45); }}
QFrame#DemoBanner QLabel {{ color: {t.violet}; font-size: 11px; font-weight: 600; }}
QLabel#Wordmark {{ font-size: 15px; font-weight: 700; letter-spacing: 2px; }}
QLabel#WordmarkSub {{ font-size: 9px; font-weight: 600; color: {t.cyan}; }}
QFrame[divider="true"] {{ background: {t.border}; max-height: 1px; min-height: 1px; border: none; }}

/* ---- buttons ---- */
QPushButton {{ background: {t.bg2}; border: 1px solid {t.border_hi}; border-radius: {r['md']}px; padding: 7px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {t.panel_hi}; border-color: {t.cyan_dim}; }}
QPushButton:pressed {{ background: {t.bg1}; }}
QPushButton:focus {{ border: 1px solid {t.focus}; }}
QPushButton:disabled {{ color: {t.text_faint}; border-color: {t.border}; background: {t.bg1}; }}
QPushButton[variant="primary"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {t.cyan}, stop:1 {t.indigo}); color: #04121A; border: none; font-weight: 700; }}
QPushButton[variant="primary"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #67E8F9, stop:1 #818CF8); }}
QPushButton[variant="primary"]:disabled {{ background: {t.border}; color: {t.text_faint}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {t.text_dim}; }}
QPushButton[variant="ghost"]:hover {{ background: {_rgba(t.cyan, 0.08)}; color: {t.text}; border-color: {t.border}; }}
QPushButton[variant="danger"] {{ background: {_rgba(t.danger, 0.14)}; border: 1px solid {_rgba(t.danger, 0.6)}; color: {t.danger}; }}
QPushButton[variant="danger"]:hover {{ background: {_rgba(t.danger, 0.26)}; }}
QPushButton[variant="success"] {{ background: {_rgba(t.success, 0.14)}; border: 1px solid {_rgba(t.success, 0.6)}; color: {t.success}; }}
QPushButton[variant="success"]:hover {{ background: {_rgba(t.success, 0.26)}; }}
QPushButton[variant="chip"] {{ background: {t.bg1}; border: 1px solid {t.border}; border-radius: 14px; padding: 5px 12px; color: {t.text_dim}; }}
QPushButton[variant="chip"]:hover {{ border-color: {t.cyan_dim}; color: {t.text}; }}
QPushButton[variant="chip"]:checked {{ background: {_rgba(t.cyan, 0.16)}; border-color: {t.cyan}; color: {t.cyan}; }}
QPushButton[nav="true"] {{ text-align: left; padding: 10px 14px; border-radius: {r['md']}px; border: 1px solid transparent; background: transparent; color: {t.text_dim}; font-weight: 500; }}
QPushButton[nav="true"]:hover {{ background: {_rgba(t.cyan, 0.07)}; color: {t.text}; }}
QPushButton[nav="true"]:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {_rgba(t.cyan, 0.20)}, stop:1 {_rgba(t.indigo, 0.05)}); color: {t.text}; border: 1px solid {_rgba(t.cyan, 0.35)}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {r['md']}px; padding: 6px; }}
QToolButton:hover {{ background: {_rgba(t.cyan, 0.10)}; border-color: {t.border}; }}
QToolButton:focus {{ border-color: {t.focus}; }}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{ background: {t.bg1}; border: 1px solid {t.border}; border-radius: {r['md']}px; padding: 7px 10px; selection-background-color: {t.cyan_dim}; selection-color: #FFFFFF; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateEdit:focus {{ border: 1px solid {t.focus}; }}
QLineEdit:read-only {{ color: {t.text_dim}; }}
QLineEdit[search="true"] {{ border-radius: 18px; padding: 8px 14px 8px 36px; background: {_rgba(t.bg1, 0.9)}; }}
QComboBox {{ background: {t.bg1}; border: 1px solid {t.border}; border-radius: {r['md']}px; padding: 6px 10px; min-height: 20px; }}
QComboBox:hover {{ border-color: {t.border_hi}; }}
QComboBox:focus {{ border-color: {t.focus}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t.panel}; border: 1px solid {t.border_hi}; selection-background-color: {_rgba(t.cyan, 0.22)}; padding: 4px; outline: none; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t.border_hi}; border-radius: 4px; background: {t.bg1}; }}
QCheckBox::indicator:checked {{ background: {t.cyan}; border-color: {t.cyan}; }}
QCheckBox::indicator:focus {{ border-color: {t.focus}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {t.border_hi}; border-radius: 8px; background: {t.bg1}; }}
QRadioButton::indicator:checked {{ background: {t.cyan}; border-color: {t.cyan}; }}

/* ---- tables & lists ---- */
QTableView, QTreeView, QListView, QListWidget {{ background: transparent; alternate-background-color: {_rgba(t.bg1, 0.35)}; border: none; gridline-color: transparent; selection-background-color: {_rgba(t.cyan, 0.16)}; selection-color: {t.text}; }}
QTableView::item {{ padding: 6px 8px; border-bottom: 1px solid {_rgba(t.border, 0.7)}; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ background: {_rgba(t.cyan, 0.16)}; color: {t.text}; }}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{ background: {_rgba(t.cyan, 0.07)}; }}
QListWidget::item {{ padding: 8px 10px; border-radius: {r['sm']}px; margin: 1px 0; }}
QHeaderView::section {{ background: transparent; color: {t.text_faint}; border: none; border-bottom: 1px solid {t.border}; padding: 8px; font-size: {TYPE['xs']}px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {t.text}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border_hi}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.cyan_dim}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border_hi}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.cyan_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QScrollArea {{ border: none; background: transparent; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {t.text_dim}; padding: 9px 16px; border: none; border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{ color: {t.cyan}; border-bottom: 2px solid {t.cyan}; }}
QTabBar::tab:focus {{ color: {t.text}; }}

/* ---- misc ---- */
QProgressBar {{ background: {t.bg1}; border: 1px solid {t.border}; border-radius: 6px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {t.cyan}, stop:1 {t.violet}); }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QSplitter::handle:hover {{ background: {_rgba(t.cyan, 0.25)}; }}
QMenu {{ background: {t.panel}; border: 1px solid {t.border_hi}; border-radius: {r['md']}px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px 8px 14px; border-radius: {r['sm']}px; }}
QMenu::item:selected {{ background: {_rgba(t.cyan, 0.20)}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 6px 4px; }}
QGroupBox {{ border: 1px solid {t.border}; border-radius: {r['md']}px; margin-top: 14px; padding: 14px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {t.text_dim}; }}
QStatusBar {{ background: {t.bg1}; border-top: 1px solid {t.border}; color: {t.text_dim}; }}
QStatusBar QLabel {{ color: {t.text_dim}; }}
QMessageBox {{ background: {t.bg0}; }}
QCalendarWidget QWidget {{ background: {t.panel}; }}
""" + badge_qss(t)


def badge_qss(t: Theme) -> str:
    """Status badge rules (appended to the main stylesheet)."""
    tones = {"success": t.success, "warning": t.warning, "danger": t.danger, "info": t.info, "neutral": t.text_dim, "violet": t.violet, "cyan": t.cyan}
    out = [f'QLabel[badge="true"] {{ border-radius: 9px; padding: 2px 9px; font-size: {TYPE["xs"]}px; font-weight: 600; }}']
    for name, color in tones.items():
        out.append(f'QLabel[badge="true"][tone="{name}"] {{ color: {color}; background: {_rgba(color, 0.14)}; border: 1px solid {_rgba(color, 0.45)}; }}')
    out.append(f'QLabel[dot="true"] {{ border-radius: 4px; min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px; }}')
    for name, color in tones.items():
        out.append(f'QLabel[dot="true"][tone="{name}"] {{ background: {color}; }}')
    return "\n".join(out)
