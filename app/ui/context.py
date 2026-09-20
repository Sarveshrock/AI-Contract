"""Shared UI context and the base class for screens."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.core.container import WorkspaceContext
from app.core.errors import ContractLensError
from app.core.logging import get_logger
from app.ui.components.base import label
from app.ui.components.states import StatefulView
from app.ui.theme.tokens import is_classic
from app.workers.tasks import TaskRunner

log = get_logger(__name__)


def user_message(exc: BaseException) -> str:
    if isinstance(exc, ContractLensError):
        return exc.user_message
    return "An unexpected error occurred. Details were written to the log."


@dataclass
class UiContext:
    ws: WorkspaceContext
    runner: TaskRunner
    settings: QSettings
    toast: Callable[[str, str], None] = lambda message, tone="info": None
    navigate: Callable[[str, dict[str, Any] | None], None] = lambda nav_id, params=None: None
    open_contract: Callable[..., None] = lambda contract_id, **kw: None
    open_evidence: Callable[[Any], None] = lambda vm: None
    refresh_badges: Callable[[], None] = lambda: None
    screens: dict[str, "BaseScreen"] = field(default_factory=dict)

    def error(self, exc: BaseException) -> None:
        msg = user_message(exc)
        log.info("ui error shown: %s", msg)
        self.toast(msg, "danger")

    def run(self, fn: Callable[..., Any], on_result: Callable[[Any], None] | None = None, *, name: str = "task", on_progress=None, on_error=None):
        return self.runner.submit(fn, on_result=on_result, on_error=on_error or self.error, on_progress=on_progress, name=name)

    def invalidate_all(self, except_: str | None = None) -> None:
        for nid, s in self.screens.items():
            if nid != except_:
                s.mark_stale()
        self.refresh_badges()


class BaseScreen(QWidget):
    """Header + StatefulView. Subclasses implement :meth:`build`, :meth:`fetch` and :meth:`render`."""

    nav_id = "screen"
    title = "Screen"
    eyebrow = ""
    icon_name = "command"
    empty_title = "Nothing to show yet"
    empty_message = ""

    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._stale = True
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(16)
        self.header = QWidget()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        if is_classic():
            # one navy title bar: "Eyebrow - Screen title" with the screen actions on the right
            self.header.setObjectName("ScreenHeader")
            self.header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            head.setContentsMargins(10, 5, 8, 5)
            titles.addWidget(label(f"{self.eyebrow} - {self.title}" if self.eyebrow else self.title, "titlebar"))
        else:
            titles.addWidget(label(self.eyebrow.upper(), "eyebrow"))
            titles.addWidget(label(self.title, "h1"))
        head.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        root.addWidget(self.header)
        self.content = self.build()
        self.view = StatefulView(self.content)
        self.view.retryRequested.connect(self.load)
        self.view.emptyAction.connect(self.on_empty_action)
        root.addWidget(self.view, 1)
        self.view.show_loading()

    # -- to implement
    def build(self) -> QWidget:  # pragma: no cover - abstract
        raise NotImplementedError

    def fetch(self) -> Any:  # runs in a worker thread
        return None

    def render(self, data: Any) -> None:
        pass

    def is_empty(self, data: Any) -> bool:
        return False

    # -- lifecycle
    def mark_stale(self) -> None:
        self._stale = True

    def on_show(self, params: dict[str, Any] | None = None) -> None:
        if self._stale and not self._loading:
            self.load()

    def load(self) -> None:
        if self._loading:
            return
        self._loading = True
        if self.view.currentWidget() is not self.view.content:
            self.view.show_loading()
        self.ctx.run(self.fetch, self._loaded, name=f"load {self.nav_id}", on_error=self._failed)

    def _loaded(self, data: Any) -> None:
        self._loading = False
        self._stale = False
        if self.is_empty(data):
            self.view.show_empty(self.empty_title, self.empty_message, self.empty_action())
            return
        self.render(data)
        self.view.show_content()

    def empty_action(self) -> str | None:
        return None

    def on_empty_action(self) -> None:
        self.ctx.navigate("contracts", {"upload": True})

    def _failed(self, exc: BaseException) -> None:
        self._loading = False
        log.info("screen %s failed to load: %s", self.nav_id, type(exc).__name__)
        self.view.show_error(user_message(exc))
