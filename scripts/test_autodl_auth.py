"""Authentication boundaries; optional GUI checks use only a local HTML fixture."""

import contextlib
import io
import os
import sys
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from autodl_instance import AutoDLError, Client, main
from autodl_auth import LoginSession, SmsLoginWindow, headless_login, read_session


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'AUTODL_SUB_ACCOUNT': 'fixture@123', 'AUTODL_SUB_PASSWORD': 'fixture'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.transport = SimpleNamespace(headers={}, cookies=SimpleNamespace(jar=[]))
        self.client = Client(self.transport)
        self.client.store = Mock()
        self.client.store.load.return_value = None

    def test_main_login_routes_and_hands_off_session(self):
        browser = MagicMock()
        with patch("autodl_auth.headless_login", return_value=browser) as login, \
                patch('autodl_auth.browser_state', return_value={'cookies': [], 'origins': []}):
            self.client.login("wechat")
        login.assert_called_once_with("wechat")
        self.assertEqual(self.client.account_type, "main")
        self.assertIs(self.client.page, browser.__enter__.return_value)
        self.assertNotIn("Authorization", self.transport.headers)

    def test_failed_login_does_not_reuse_previous_account(self):
        self.client.account_type = "main"
        self.transport.headers.update(Authorization="old-secret", AppVersion="v0")
        browser = MagicMock()
        browser.__enter__.side_effect = AutoDLError("cancelled")
        with patch("autodl_auth.headless_login", return_value=browser):
            with self.assertRaises(AutoDLError):
                self.client.login("sms")
        self.assertNotIn("Authorization", self.transport.headers)
        self.assertNotIn("AppVersion", self.transport.headers)

    def test_default_login_preserves_subaccount_flow(self):
        def login_values():
            self.transport.headers.update(Authorization='synthetic', AppVersion='v1')
        with patch.object(self.client, "_subaccount_login", side_effect=login_values) as login, \
                patch("autodl_auth.headless_login") as browser:
            self.client.login()
        login.assert_called_once_with()
        browser.assert_not_called()
        self.assertEqual(self.client.account_type, "sub")

    def test_login_session_never_exposes_token_in_repr(self):
        self.assertNotIn("secret", repr(LoginSession("secret", "v1")))
        for token, version in (("", "v1"), (None, "v1"), ("secret", None), ("secret", "1")):
            with self.subTest(token=bool(token), version=version), self.assertRaises(AutoDLError):
                LoginSession(token, version)

    def test_no_session_before_login_finishes(self):
        page = Mock()
        page.evaluate.return_value = None
        self.assertIsNone(read_session(page))
        page.evaluate.return_value = {"token": "secret", "version": "v1"}
        self.assertEqual(read_session(page), LoginSession("secret", "v1"))

    def test_public_billing_command_does_not_authenticate(self):
        with patch("autodl_instance.Client") as client, \
                patch("autodl_resources.image_billing", return_value={"text": "rules"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--auth", "sms", "image-billing"]), 0)
        client.return_value.login.assert_not_called()
        client.return_value.close.assert_called_once_with()

    def test_wechat_qr_is_temporary_and_browser_always_closes(self):
        # Fake the optional dependency so these tests still run without Chromium.
        for fail in (False, True):
            with self.subTest(timeout=fail):
                browser, page = Mock(), Mock()
                browser.new_page.return_value = page
                page.evaluate.return_value = {"token": "secret", "version": "v1"}
                saved = []

                def screenshot(path):
                    saved.append(Path(path))
                    Path(path).write_bytes(b"synthetic-qr")

                page.locator.return_value.screenshot.side_effect = screenshot
                if fail:
                    page.wait_for_function.side_effect = [None, TimeoutError()]
                sync_api = ModuleType("playwright.sync_api")
                sync_api.TimeoutError = TimeoutError
                sync_api.sync_playwright = MagicMock()
                runtime = sync_api.sync_playwright.return_value.__enter__.return_value
                runtime.chromium.launch.return_value = browser
                with patch.dict(sys.modules, {"playwright.sync_api": sync_api}), \
                        contextlib.redirect_stderr(io.StringIO()):
                    if fail:
                        with self.assertRaisesRegex(AutoDLError, "超时"):
                            with headless_login("wechat"):
                                pass
                    else:
                        with headless_login("wechat") as active_page:
                            self.assertIs(active_page, page)
                            browser.close.assert_not_called()
                self.assertTrue(runtime.chromium.launch.call_args.kwargs["headless"])
                self.assertEqual(len(saved), 1)
                self.assertFalse(saved[0].exists())
                browser.close.assert_called_once_with()


@unittest.skipUnless(os.environ.get("AUTODL_TEST_GUI") == "1",
                     "Set AUTODL_TEST_GUI=1 for local Chromium/tkinter integration checks")
class LocalLoginWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.context.route("**/*", lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body="""<!doctype html><html lang="zh">
            <meta charset="utf-8"><style>body{font:24px sans-serif;padding:40px}</style>
            <h1>本地登录流程检查</h1><p>此页面不发送短信</p>
            <input id="code" placeholder="短信验证码" style="font:inherit">
            <button id="login" style="font:inherit" onclick="
                localStorage.setItem('token', 'fixture-secret');
                localStorage.setItem('app_version', JSON.stringify({version:'v-fixture'}));
            ">登录</button></html>"""))
        self.page = self.context.new_page()
        self.page.goto("https://www.autodl.com/login")

    def tearDown(self):
        self.context.close()

    def test_user_input_scaled_coordinates_and_automatic_completion(self):
        window = SmsLoginWindow(self.page, time.monotonic() + 15)
        window.window.title("AutoDL 本地流程检查")
        # Force a smaller display to cover coordinate conversion as well.
        window.scale = min(window.scale, 0.6)

        def click(selector):
            box = self.page.locator(selector).bounding_box()
            x, y = int((box["x"] + 10) * window.scale), int((box["y"] + 10) * window.scale)
            window.label.event_generate("<ButtonPress-1>", x=x, y=y)
            window.label.event_generate("<ButtonRelease-1>", x=x, y=y)

        def interact():
            window.window.focus_force()
            click("#code")
            for digit in "123456":
                window.label.event_generate("<KeyPress>", keysym=digit)
            self.assertEqual(self.page.locator("#code").input_value(), "123456")
            # Clipboard contents must go to the focused input, never logs.
            with patch.object(window.window, "clipboard_get", return_value="654321"):
                window._key(SimpleNamespace(state=4, keysym="a"))
                window._key(SimpleNamespace(state=4, keysym="v"))
            self.assertEqual(self.page.locator("#code").input_value(), "654321")
            click("#login")

        window.window.after(200, lambda: window._guard(interact))
        self.assertEqual(window.run(), LoginSession("fixture-secret", "v-fixture"))

    def test_cancel_exits_without_a_session(self):
        window = SmsLoginWindow(self.page, time.monotonic() + 15)
        window.window.after(100, window._close)
        with self.assertRaisesRegex(AutoDLError, "取消"):
            window.run()
        self.assertIsNone(read_session(self.page))

    def test_timeout_closes_the_window(self):
        window = SmsLoginWindow(self.page, time.monotonic() - 1)
        with self.assertRaisesRegex(AutoDLError, "超时"):
            window.run()


if __name__ == "__main__":
    unittest.main()
