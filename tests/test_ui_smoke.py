"""UI smoke tests (offscreen Qt): every screen loads against demo data without raising; key interactions work."""
from __future__ import annotations

import os
import sys
import traceback
from datetime import date

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import NAV, MainWindow, apply_theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, False)
    return app


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settle(win: MainWindow, timeout: int = 20000) -> None:
    waited = 0
    while win.runner.active and waited < timeout:
        pump(100)
        waited += 100
    pump(150)


@pytest.fixture()
def errors(monkeypatch):
    """Collect exceptions raised inside Qt slots/paint events (PyQt would otherwise abort)."""
    caught: list[str] = []

    def hook(t, v, tb):
        caught.append("".join(traceback.format_exception(t, v, tb)))

    monkeypatch.setattr(sys, "excepthook", hook)
    return caught


@pytest.fixture()
def window(qapp, clone_demo, tmp_path, errors):
    ws = clone_demo()
    from app.core.container import AppContainer

    container = ws.container
    qs = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    win = MainWindow(container, [ws.principal], ws.principal, qs)
    win.resize(1400, 860)
    win.show()
    settle(win)
    yield win
    win.runner.wait_idle(3000)
    win.close()


def test_every_screen_loads_with_demo_data(window, errors):
    for nid, _title, _icon in NAV:
        window.navigate(nid)
        settle(window)
        screen = window.ctx.screens[nid]
        assert screen.view.currentWidget() is screen.view.content, f"{nid} did not reach its content state"
        window.grab()  # force a paint pass so paintEvent bugs surface
    assert errors == [], "\n".join(errors[:2])


def test_navigation_shortcuts_and_state(window, errors):
    window.navigate("obligations", {"view": "overdue"})
    settle(window)
    table = window.ctx.screens["obligations"].table
    assert all(r.overdue for r in table.rows())
    window.navigate("risk", {"tab": "review"})
    settle(window)
    assert window.ctx.screens["risk"].tabs.currentIndex() == 2
    assert window.ctx.screens["risk"].queue.count() > 0
    window.navigate("evidence", {"query": "governing law"})
    settle(window)
    from PyQt6.QtWidgets import QLabel

    assert any(isinstance(w, QLabel) and "Delaware" in w.text() for w in window.ctx.screens["evidence"].findChildren(QLabel))
    assert errors == [], "\n".join(errors[:2])


def test_contract_workspace_opens_with_evidence(window, errors):
    window.navigate("contracts")
    settle(window)
    scr = window.ctx.screens["contracts"]
    first = scr._all[0].contract  # noqa: SLF001
    window.open_contract(first.id)
    settle(window)
    assert scr.stack.currentIndex() == 1 and scr._pages and scr.clause_tree.topLevelItemCount() > 0  # noqa: SLF001
    assert "Delaware" in scr.doc_view.toPlainText() or "Agreement" in scr.doc_view.toPlainText()
    scr.dl_table.select_row(0)
    pump(200)
    assert errors == [], "\n".join(errors[:2])


def test_copilot_screen_answers_structured_question(window, errors):
    window.navigate("copilot")
    settle(window)
    scr = window.ctx.screens["copilot"]
    scr._ask("Show all obligations without an explicit deadline")  # noqa: SLF001
    settle(window)
    from PyQt6.QtWidgets import QLabel

    texts = " ".join(w.text() for w in scr.findChildren(QLabel))
    assert "no explicit or calculated deadline" in texts
    assert errors == [], "\n".join(errors[:2])


def test_empty_workspace_shows_empty_state(qapp, store, principal, tmp_path, errors):
    from tests.helpers import make_workspace

    ws = make_workspace(store, principal, tmp_path)
    qs = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    win = MainWindow(ws.container, [principal], principal, qs)
    win.show()
    settle(win)
    scr = win.ctx.screens["command"]
    assert scr.view.currentWidget() is scr.view.empty
    win.navigate("contracts")
    settle(win)
    assert win.ctx.screens["contracts"].view.currentWidget() is win.ctx.screens["contracts"].view.empty
    win.close()
    assert errors == [], "\n".join(errors[:2])


def test_high_contrast_and_reduced_motion_toggle(window, errors):
    from app.ui.theme.motion import Motion

    window.act_contrast.setChecked(True)
    settle(window)
    window.act_motion.setChecked(True)
    assert Motion.enabled is False
    window.act_motion.setChecked(False)
    window.act_contrast.setChecked(False)
    settle(window)
    assert errors == [], "\n".join(errors[:2])


def test_viewer_role_sees_read_only_admin(qapp, clone_demo, tmp_path, errors):
    from app.models.enums import Role

    ws = clone_demo(role=Role.VIEWER)
    qs = QSettings(str(tmp_path / "ui2.ini"), QSettings.Format.IniFormat)
    win = MainWindow(ws.container, [ws.principal], ws.principal, qs)
    win.show()
    settle(win)
    win.navigate("admin")
    settle(win)
    assert not win.ctx.screens["admin"].email.isEnabled()
    win.close()
    assert errors == [], "\n".join(errors[:2])


def test_login_dialog_local_mode_and_error_display(qapp, store, tmp_path, errors):
    from app.core.errors import AuthenticationError
    from tests.helpers import make_workspace
    from app.security.auth import LocalAuthService
    from app.ui.login import LoginDialog

    ws = make_workspace(store, LocalAuthService(store).ensure_demo_principal(), tmp_path)
    dlg = LoginDialog(ws.container)
    dlg._sign_in()  # noqa: SLF001
    assert dlg.principals and dlg.principals[0].is_demo

    def boom(*a, **k):
        raise AuthenticationError("bad", user_message="Incorrect e-mail or password.")

    ws.container.auth.sign_in = boom
    dlg2 = LoginDialog(ws.container)
    dlg2.principals = []
    dlg2._sign_in()  # noqa: SLF001
    assert not dlg2.principals and "Incorrect" in dlg2.error.text() and dlg2.btn.isEnabled()
    assert errors == []


def test_obligation_kpi_tiles_filter_and_detail_panel(window, errors):
    window.navigate("obligations")
    settle(window)
    scr = window.ctx.screens["obligations"]
    assert scr._kpis["open"]._value.text().isdigit()  # noqa: SLF001
    scr._kpis["overdue"].clicked.emit()  # noqa: SLF001
    assert scr._view == "overdue"  # noqa: SLF001
    scr._kpis["overdue"].clicked.emit()  # noqa: SLF001 - clicking the active tile clears the filter
    assert scr._view == "all"  # noqa: SLF001
    assert scr.detail_stack.currentIndex() == 0
    scr.table.select_row(0)
    settle(window)
    assert scr.detail_stack.currentIndex() == 1 and scr.d_name.text()
    assert errors == [], "\n".join(errors[:2])


def test_copilot_hero_hides_on_first_question_and_new_chat_restores_it(window, errors):
    window.navigate("copilot")
    settle(window)
    scr = window.ctx.screens["copilot"]
    assert scr.chat.indexOf(scr.hero) >= 0
    scr._ask("Which contracts expire in the next 90 days?")  # noqa: SLF001
    settle(window)
    assert scr.chat.indexOf(scr.hero) < 0
    scr._reset()  # noqa: SLF001
    assert scr.chat.indexOf(scr.hero) >= 0 and scr.hero.isVisibleTo(scr)
    assert errors == [], "\n".join(errors[:2])


def test_contract_workspace_ask_tab_shows_chat_answer(window, errors):
    window.navigate("contracts")
    settle(window)
    scr = window.ctx.screens["contracts"]
    window.open_contract(scr._all[0].contract.id, tab="ask")  # noqa: SLF001
    settle(window)
    scr._ask("Which clauses mention service credits?")  # noqa: SLF001
    settle(window)
    from PyQt6.QtWidgets import QLabel

    texts = " ".join(w.text() for w in scr.ask_scroll.findChildren(QLabel))
    assert "Service Credits" in texts
    assert scr.ask_intro.isHidden()
    assert errors == [], "\n".join(errors[:2])


def test_theme_switch_between_classic_and_modern_rebuilds_the_window(window, errors):
    from app.ui.theme.tokens import is_classic

    assert is_classic()  # the classic Windows 9x look is the default
    window.navigate("obligations")
    settle(window)
    window.act_classic.setChecked(False)
    settle(window)
    assert not is_classic() and window.menuBar().actions()
    window.navigate("command")
    settle(window)
    assert window.ctx.screens["command"].view.currentWidget() is window.ctx.screens["command"].view.content
    window.act_classic.setChecked(True)
    settle(window)
    assert is_classic()
    assert errors == [], "\n".join(errors[:2])
