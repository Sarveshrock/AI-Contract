"""Design tokens: one source of truth for QSS, custom painting, charts and icons."""
from __future__ import annotations

from dataclasses import dataclass, replace

from PyQt6.QtGui import QColor


@dataclass(frozen=True)
class Theme:
    name: str
    # surfaces
    bg0: str = "#060A12"        # window base
    bg1: str = "#0A101C"        # sunken areas / inputs
    bg2: str = "#0E1626"        # raised surface
    panel: str = "#101A2D"      # glass panel base (solid fallback)
    panel_hi: str = "#16223A"   # hover / selected panel
    border: str = "#1F2C47"
    border_hi: str = "#33477A"
    # text hierarchy
    text: str = "#E8EEFC"
    text_dim: str = "#A3B0CC"
    text_faint: str = "#7686A6"
    # accents
    cyan: str = "#22D3EE"
    cyan_dim: str = "#0E7490"
    violet: str = "#8B7CFF"
    indigo: str = "#6674F5"
    # states
    success: str = "#34D399"
    warning: str = "#F5B942"
    danger: str = "#F4718A"
    info: str = "#60A5FA"
    focus: str = "#22D3EE"

    def qcolor(self, token: str, alpha: float = 1.0) -> QColor:
        c = QColor(getattr(self, token, token))
        c.setAlphaF(alpha)
        return c


DARK = Theme("dark")
HIGH_CONTRAST = replace(
    DARK, name="high-contrast", bg0="#000000", bg1="#05070C", bg2="#090D16", panel="#0B1120", panel_hi="#131C31", border="#4C5F8F", border_hi="#8CA0D4",
    text="#FFFFFF", text_dim="#DCE4F7", text_faint="#B7C3E0", cyan="#5EEAFF", violet="#B6ABFF", success="#5CF0BB", warning="#FFD166", danger="#FF8FA3",
)

# spacing (px), radii, type scale
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 6, "md": 10, "lg": 14, "pill": 999}
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'
MONO_FAMILY = '"Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace'
TYPE = {"xs": 11, "sm": 12, "base": 13, "md": 14, "lg": 16, "xl": 20, "h1": 26, "kpi": 30}

_current: Theme = DARK


def theme() -> Theme:
    return _current


def set_theme(t: Theme) -> None:
    global _current
    _current = t


TONES = {  # tone -> (foreground token, background alpha)
    "success": "success", "warning": "warning", "danger": "danger", "info": "info", "neutral": "text_dim", "violet": "violet", "cyan": "cyan",
}


def tone_color(tone: str) -> str:
    return getattr(theme(), TONES.get(tone, "text_dim"))


def severity_tone(sev: str) -> str:
    return {"critical": "danger", "high": "danger", "medium": "warning", "low": "info", "info": "neutral"}.get(sev, "neutral")


def status_tone(status: str) -> str:
    s = status.lower()
    good = ("indexed", "completed", "succeeded", "approved", "confirmed", "active", "resolved", "done", "ok", "verified", "computed")
    warn = ("pending", "needs_review", "in_review", "pending_review", "running", "embedding", "chunking", "extracting", "ocr", "unreviewed", "open", "unread", "expiring", "in_progress", "warning", "queued")
    bad = ("failed", "overdue", "rejected", "unresolved", "critical", "disputed", "expired", "terminated")
    if s in good:
        return "success"
    if s in warn:
        return "warning"
    if s in bad:
        return "danger"
    return "neutral"
