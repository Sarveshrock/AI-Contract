"""Restrained motion. Every animation honours the global reduced-motion switch."""
from __future__ import annotations

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget


class Motion:
    enabled: bool = True

    @classmethod
    def set_reduced(cls, reduced: bool) -> None:
        cls.enabled = not reduced


def fade_in(widget: QWidget, ms: int = 180, on_done=None) -> None:
    """Fade a widget in. With reduced motion the widget simply appears."""
    if not Motion.enabled:
        widget.setGraphicsEffect(None)
        if on_done:
            on_done()
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def done() -> None:
        widget.setGraphicsEffect(None)  # effects are costly to keep on large widgets
        if on_done:
            on_done()

    anim.finished.connect(done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._fade_anim = anim  # type: ignore[attr-defined]  # keep a reference


def slide_fade_in(widget: QWidget, dx: int = 16, ms: int = 240) -> None:
    """Fade in while sliding a few pixels horizontally (screen transitions)."""
    if not Motion.enabled:
        widget.setGraphicsEffect(None)
        return
    end = widget.pos()
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(ms)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    slide = QPropertyAnimation(widget, b"pos", widget)
    slide.setDuration(ms)
    slide.setStartValue(end + QPoint(dx, 0))
    slide.setEndValue(end)
    slide.setEasingCurve(QEasingCurve.Type.OutCubic)
    group = QParallelAnimationGroup(widget)
    group.addAnimation(fade)
    group.addAnimation(slide)
    group.finished.connect(lambda: widget.setGraphicsEffect(None))
    group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._slide_anim = group  # type: ignore[attr-defined]


def stagger(widgets: list[QWidget], step_ms: int = 45, ms: int = 200) -> None:
    """Reveal widgets one after another (timeline reveal)."""
    for i, w in enumerate(widgets):
        if not Motion.enabled:
            continue
        w.setVisible(False)
        QTimer.singleShot(i * step_ms, lambda w=w: (w.setVisible(True), fade_in(w, ms)))
    if not Motion.enabled:
        for w in widgets:
            w.setVisible(True)
