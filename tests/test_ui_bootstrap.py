from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.ui.main_window import NAV, screen_factories


def test_every_navigation_entry_has_a_real_screen():
    """No placeholder/stub screens: each nav entry maps to a concrete, implemented screen class."""
    factories = screen_factories()
    assert set(factories) == {nid for nid, _title, _icon in NAV}
    for nid, cls in factories.items():
        assert cls.__name__ != "PlaceholderScreen", nid
        assert "not implemented" not in (cls.__doc__ or "").lower()
        assert cls.__module__.startswith("app.ui."), nid
