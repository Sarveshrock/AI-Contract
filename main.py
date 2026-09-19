"""ContractLens Enterprise — desktop entry point."""
from __future__ import annotations

import sys


def main() -> int:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

    from app import APP_NAME
    from app.config.settings import get_settings
    from app.core.container import AppContainer
    from app.core.errors import ContractLensError
    from app.ui.login import LoginDialog
    from app.ui.main_window import MainWindow, apply_theme
    from app.ui.theme.motion import Motion

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ContractLens")
    qsettings = QSettings("ContractLens", "Enterprise")
    apply_theme(app, qsettings.value("high_contrast", False, type=bool))
    try:
        settings = get_settings()
        Motion.set_reduced(qsettings.value("reduced_motion", settings.reduced_motion, type=bool))
        container = AppContainer.build(settings)
    except ContractLensError as exc:
        QMessageBox.critical(None, APP_NAME, exc.user_message)
        return 2
    while True:
        login = LoginDialog(container)
        if login.exec() != QDialog.DialogCode.Accepted or not login.principals:
            return 0
        principals = login.principals
        window = MainWindow(container, principals, principals[0], qsettings)
        window.show()
        app.exec()
        if not window.sign_out_requested:
            return 0
        container.sign_out()


if __name__ == "__main__":
    raise SystemExit(main())
