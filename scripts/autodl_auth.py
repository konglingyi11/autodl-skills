"""Headless main-account login and restoration of encrypted browser state."""

import getpass
from contextlib import contextmanager
import io
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from autodl_instance import AutoDLError, AuthenticationExpired, BASE


LOGIN_TIMEOUT_SECONDS = 300
VIEWPORT = {"width": 1280, "height": 900}
SESSION_SCRIPT = """() => {
    if (location.origin !== 'https://www.autodl.com') return null;
    try {
        const token = localStorage.getItem('token');
        const version = JSON.parse(localStorage.getItem('app_version')).version;
        return token && typeof version === 'string' && version.startsWith('v')
            ? {token, version} : null;
    } catch (_) { return null; }
}"""


@dataclass(frozen=True)
class LoginSession:
    token: str = field(repr=False)
    version: str

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token.strip():
            raise AutoDLError("登录结果缺少令牌，请重新登录")
        if not isinstance(self.version, str) or not self.version.startswith("v"):
            raise AutoDLError("登录结果缺少客户端版本，请重新登录")


def read_session(page):
    data = page.evaluate(SESSION_SCRIPT)
    return LoginSession(data.get("token"), data.get("version")) if data else None


def browser_state(page):
    state = page.context.storage_state()
    state['cookies'] = [cookie for cookie in state['cookies']
                        if cookie.get('domain', '').lstrip('.') in ('autodl.com', 'www.autodl.com')]
    state['origins'] = [origin for origin in state['origins'] if origin.get('origin') == BASE]
    return state


@contextmanager
def restore_browser(state):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
            try:
                context = browser.new_context(viewport=VIEWPORT, storage_state=state)
                page = context.new_page()
                page.goto(BASE + '/console/instance/list', wait_until='domcontentloaded', timeout=30000)
                if not read_session(page):
                    raise AuthenticationExpired('已保存的登录会话失效')
                yield page
            finally:
                browser.close()
    except (AutoDLError, AuthenticationExpired):
        raise
    except Exception as exc:
        raise AutoDLError(f'恢复登录会话失败：{type(exc).__name__}') from exc


class SmsLoginWindow:
    """Forward user input to the official page, and close on successful login."""

    def __init__(self, page, deadline):
        try:
            import tkinter as tk
            from PIL import Image, ImageTk
        except ImportError as exc:
            raise AutoDLError("短信登录需要 tkinter 和 Pillow，详见 README") from exc
        self.page, self.deadline = page, deadline
        self.session = None
        self.error = None
        self.refresh_id = None
        self.Image, self.ImageTk = Image, ImageTk
        self.window = tk.Tk()
        self.window.title("AutoDL 短信登录")
        # Fit the user's screen while keeping the cancel action visible.
        self.scale = min(1, (self.window.winfo_screenwidth() - 80) / VIEWPORT["width"],
                         (self.window.winfo_screenheight() - 160) / VIEWPORT["height"])
        toolbar = tk.Frame(self.window)
        toolbar.pack(fill="x")
        tk.Label(toolbar, text="完成图形验证后，在下方输入短信验证码并点击登录。").pack(side="left")
        tk.Button(toolbar, text="取消登录", command=self._close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.label = tk.Label(self.window, takefocus=True)
        self.label.pack()
        self.label.bind("<ButtonPress-1>", lambda event: self._guard(self._down, event))
        self.label.bind("<B1-Motion>", lambda event: self._guard(self._move, event))
        self.label.bind("<ButtonRelease-1>", lambda event: self._guard(self._up, event))
        self.label.bind("<Key>", lambda event: self._guard(self._key, event))

    def _guard(self, callback, *args):
        try:
            callback(*args)
        except Exception as exc:
            self.error = exc
            self._close()

    def _close(self):
        if self.refresh_id is not None:
            self.window.after_cancel(self.refresh_id)
            self.refresh_id = None
        self.window.destroy()

    def _move(self, event):
        self.page.mouse.move(event.x / self.scale, event.y / self.scale)

    def _down(self, event):
        self.label.focus_set()
        self._move(event)
        self.page.mouse.down()

    def _up(self, event):
        self._move(event)
        self.page.mouse.up()

    def _key(self, event):
        if event.state & 0x4:  # Ctrl shortcuts apply to the focused web input.
            if event.keysym.lower() == "v":
                self.page.keyboard.insert_text(self.window.clipboard_get())
            elif event.keysym.lower() == "a":
                self.page.keyboard.press("Control+a")
            return
        keys = {"BackSpace": "Backspace", "Return": "Enter", "Delete": "Delete",
                "Tab": "Tab", "Left": "ArrowLeft", "Right": "ArrowRight",
                "Home": "Home", "End": "End"}
        if event.keysym in keys:
            self.page.keyboard.press(keys[event.keysym])
        elif event.char and event.char.isprintable():
            self.page.keyboard.insert_text(event.char)

    def _refresh(self):
        if time.monotonic() >= self.deadline:
            raise AutoDLError("短信登录超时，请重新运行命令")
        self.session = read_session(self.page)
        if self.session:
            self._close()
            return
        picture = self.Image.open(io.BytesIO(self.page.screenshot(timeout=5000)))
        if self.scale != 1:
            picture = picture.resize((round(VIEWPORT["width"] * self.scale),
                                      round(VIEWPORT["height"] * self.scale)))
        self.label.image = self.ImageTk.PhotoImage(picture)
        self.label.configure(image=self.label.image)
        self.refresh_id = self.window.after(80, lambda: self._guard(self._refresh))

    def run(self):
        self._guard(self._refresh)
        self.window.mainloop()
        if self.error:
            raise self.error
        if not self.session:
            raise AutoDLError("已取消短信登录")
        return self.session


@contextmanager
def headless_login(method):
    if method not in ("wechat", "sms"):
        raise AutoDLError("不支持的主账号登录方式")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as exc:
        raise AutoDLError("请安装 playwright 和 Chromium，详见 README") from exc
    phone = None
    if method == "sms":
        if not sys.stdin.isatty():
            raise AutoDLError("短信登录需要可交互的本机终端，请在终端运行 --auth sms")
        phone = getpass.getpass("手机号（输入不回显）: ").strip()
        if not phone:
            raise AutoDLError("请输入手机号")
    try:
        with tempfile.TemporaryDirectory(prefix="autodl-login-") as temp, sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                page = browser.new_page(viewport=VIEWPORT)
                page.goto(BASE + ("/wx-login" if method == "wechat" else "/login"),
                          wait_until="domcontentloaded", timeout=30000)
                if method == "sms":
                    page.get_by_text("短信登录", exact=True).click()
                    page.locator('input[name="phone"]').fill(phone)
                    # Sending SMS remains an explicit user action in the window.
                    SmsLoginWindow(page, time.monotonic() + LOGIN_TIMEOUT_SECONDS).run()
                else:
                    qr = page.locator(".wx img")
                    qr.wait_for(state="visible", timeout=30000)
                    page.wait_for_function("""() => {
                    const image = document.querySelector('.wx img');
                    return image && image.complete && image.naturalWidth > 0;
                }""", timeout=30000)
                    path = Path(temp) / "wechat-qr.png"
                    qr.screenshot(path=str(path))
                    print(f"请用微信扫描二维码：{path}\n等待登录完成（最多 5 分钟）…",
                          file=sys.stderr, flush=True)
                    page.wait_for_function(SESSION_SCRIPT, timeout=LOGIN_TIMEOUT_SECONDS * 1000)
                if not read_session(page):
                    raise AutoDLError("登录结果缺少会话，请重新登录")
                yield page
            finally:
                browser.close()
    except PlaywrightTimeout as exc:
        raise AutoDLError("登录页面加载或验证超时，请重新运行命令") from exc
    except AutoDLError:
        raise
    except Exception as exc:
        # Do not echo server messages, tokens, phone numbers, or browser internals.
        raise AutoDLError(f"登录未完成：{type(exc).__name__}，请重新运行命令") from exc
