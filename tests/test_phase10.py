"""Phase 10: several saved servers, text size, poster grid width, Quick Connect page."""
import os
import unittest

from helpers import IsolatedTest

from bloomsarchive.api import auth
from bloomsarchive.config import Settings
from bloomsarchive.session import Session
from bloomsarchive.ui import theme

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:            # the API layer is tested without Qt
    HAVE_QT = False


def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


class Servers(IsolatedTest):
    def signed_in(self, srv) -> Session:
        s = Session(self.settings, self.tokens)
        self.assertTrue(s.connect().needs_pin)
        s.pin_server()
        s.complete_login(auth.login_password(s.login_client(), "Alice", "hunter2"))
        return s

    def test_existing_setup_becomes_the_first_server(self):
        srv = self.serve(name="Home Media")
        self.set_addresses((srv.url, False))
        s = self.signed_in(srv)
        servers = s.servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["key"], s.active_server_key)
        self.assertEqual(servers[0]["name"], "Home Media", "named after the server once confirmed")
        self.assertEqual(servers[0]["server_id"], srv.cfg.server_id)
        self.assertEqual(servers[0]["last_user"]["name"], "Alice")

    def test_switch_and_back_keeps_each_sign_in(self):
        a = self.serve(name="Home")
        b = self.serve(name="Friend")
        self.set_addresses((a.url, False))
        s = self.signed_in(a)
        home = s.active_server_key
        friend = s.add_server("", [{"label": "LAN", "url": b.url, "public": False}])
        self.assertEqual(s.server_name(next(x for x in s.servers() if x["key"] == friend)), b.url,
                         "no name yet: shown by its address")

        s.switch_server(friend)
        self.assertIsNone(s.user)
        self.assertIsNone(s.client.token)
        self.assertEqual(self.settings.get("addresses")[0]["url"], b.url)
        self.assertIsNone(self.settings.get("server_id"))
        d = s.connect()
        self.assertTrue(d.needs_pin, "a new server is confirmed like on first run")
        s.pin_server()
        self.assertIsNone(s.restore_login(), "nobody has signed in here yet")
        s.complete_login(auth.login_password(s.login_client(), "Alice", "hunter2"))
        self.assertEqual(s.server_name(), "Friend")

        s.switch_server(home)
        self.assertEqual(self.settings.get("server_id"), a.cfg.server_id)
        d = s.connect()
        self.assertFalse(d.needs_pin)
        self.assertIsNotNone(s.restore_login(), "back home: the stored token still works")
        self.assertEqual(s.client.base_url.rstrip("/"), a.url.rstrip("/"))

        # and the friend's sign-in survived too
        s.switch_server(friend)
        s.connect()
        self.assertIsNotNone(s.restore_login())

    def test_remove(self):
        a = self.serve()
        self.set_addresses((a.url, False))
        s = self.signed_in(a)
        other = s.add_server("Other", [{"label": "x", "url": "http://10.9.9.9:8096", "public": False}])
        with self.assertRaises(ValueError):
            s.remove_server(s.active_server_key)
        s.remove_server(other)
        self.assertEqual([x["key"] for x in s.servers()], [s.active_server_key])

    def test_find_by_id_and_rename(self):
        a = self.serve()
        self.set_addresses((a.url, False))
        s = self.signed_in(a)
        self.assertEqual(s.find_server_by_id(a.cfg.server_id)["key"], s.active_server_key)
        self.assertIsNone(s.find_server_by_id("nope"))
        s.rename_server(s.active_server_key, "  Living room  ")
        self.assertEqual(s.server_name(), "Living room")

    def test_saved_and_reset_keeps_servers(self):
        a = self.serve()
        self.set_addresses((a.url, False))
        s = self.signed_in(a)
        s.add_server("Other", [{"label": "x", "url": "http://10.9.9.9:8096", "public": False}])
        self.settings.reset_preferences()
        again = Session(Settings(self.settings.path), self.tokens)
        self.assertEqual(len(again.servers()), 2)


class TextScale(unittest.TestCase):
    def tearDown(self):
        theme.set_text_scale(100)

    def test_clamped(self):
        self.assertEqual(theme.set_text_scale(500), theme.TEXT_SCALE_MAX)
        self.assertEqual(theme.set_text_scale(10), theme.TEXT_SCALE_MIN)
        self.assertEqual(theme.set_text_scale("junk"), theme.TEXT_SCALE_DEFAULT)
        self.assertEqual(theme.set_text_scale(115), 115)

    def test_stylesheet_scales_except_fixed(self):
        theme.set_text_scale(100)
        base = theme.qss()
        self.assertIn("font-size: 14px", base)
        theme.set_text_scale(130)
        big = theme.qss()
        self.assertIn("font-size: 18px", big)                 # 14 → 18
        self.assertIn("font-size: 11px /* fixed */", big, "A–Z letters keep their size")

    def test_setting_default(self):
        from bloomsarchive.config import DEFAULTS
        self.assertEqual(DEFAULTS["ui"]["text_scale"], 100)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Widgets(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.app = qapp()

    def tearDown(self):
        theme.set_text_scale(100)
        super().tearDown()

    def _grid(self):
        from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, Signal

        from bloomsarchive.ui.widgets.cards import CardDelegate, CardGridView, CardStyle

        class Model(QAbstractListModel):
            def rowCount(self, parent=QModelIndex()):
                return 0 if parent.isValid() else 300

            def data(self, index, role=Qt.ItemDataRole.DisplayRole):
                return None

        class Loader(QObject):
            loaded = Signal(str)

            def get(self, _key):
                return None

        self._keep = (Model(), Loader())
        return CardGridView(self._keep[0], CardDelegate(CardStyle.POSTER, self._keep[1]))

    def test_grid_has_no_empty_column_at_any_width(self):
        g = self._grid()
        g.show()
        card = g.card_delegate.style.size().width()
        for width in range(420, 3900, 23):
            g.resize(width, 800)
            self.app.processEvents()
            cols = len({g.visualRect(g.model().index(i, 0)).x() for i in range(40)})
            used = cols * g.gridSize().width()
            self.assertLess(g.viewport().width() - used, card,
                            f"at {width}px a whole column's worth of space is left empty")

    def test_grid_follows_text_size(self):
        g = self._grid()
        g.show()
        g.resize(1200, 800)
        h0 = g.gridSize().height()
        theme.apply_text_scale(self.app, 130)
        self.app.processEvents()
        self.assertGreater(g.gridSize().height(), h0, "taller cells for bigger captions")

    def test_settings_and_quick_connect_pages_build(self):
        from bloomsarchive.ui.quickconnect import QuickConnectPage
        from bloomsarchive.ui.settings import SettingsPage
        from bloomsarchive.ui.widgets.image_loader import ImageLoader

        s = Session(self.settings, self.tokens)
        s.add_server("Friend", [{"label": "x", "url": "http://10.9.9.9:8096", "public": False}])
        page = SettingsPage(s, ImageLoader(s))
        page.page_shown()
        self.assertEqual(page.srv_box.count(), 2)
        self.assertEqual(len(page._addr_rows), len(self.settings.get("addresses")))
        self.assertFalse(hasattr(page, "qc_code"), "Quick Connect moved to its own page")
        for pct in (theme.TEXT_SCALE_MIN, theme.TEXT_SCALE_MAX):
            page.text_size.setValue(pct)
            page._apply_text_size()
            self.assertEqual(self.settings.get("ui.text_scale"), pct)
        qc = QuickConnectPage(s)
        qc.page_shown()
        qc._approve()
        self.assertIn("Type the code", qc.msg.text())


if __name__ == "__main__":
    unittest.main()
