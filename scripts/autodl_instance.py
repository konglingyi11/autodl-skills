#!/usr/bin/env python3
"""AutoDL ordinary containers and images through direct HTTP requests."""

import argparse
import hashlib
import json
import os
import re
import sys

if __name__ == "__main__":
    try:
        from autodl_update import bootstrap
        bootstrap()
    except Exception:
        pass  # Updating is best-effort and must not block the requested operation.

try:
    from curl_cffi import requests
except ImportError as exc:
    raise SystemExit("Install curl_cffi: python -m pip install curl_cffi") from exc


BASE = "https://www.autodl.com"
# AutoDL also returns a short hexadecimal instance ID for ordinary containers.
UUID_RE = re.compile(r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{10}-[0-9a-fA-F]{8})$")
PAGE_SIZE = 10
MAX_PAGES = 1000
HTTP_CHROME_MAJOR = "120"
HTTP_CHROME_VERSION = HTTP_CHROME_MAJOR + ".0.0.0"
HTTP_PROFILE = "chrome" + HTTP_CHROME_MAJOR
HTTP_OS = "Windows"
HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   f"(KHTML, like Gecko) Chrome/{HTTP_CHROME_VERSION} Safari/537.36"),
    "Sec-CH-UA": f'"Not_A Brand";v="8", "Chromium";v="{HTTP_CHROME_MAJOR}", "Google Chrome";v="{HTTP_CHROME_MAJOR}"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": f'"{HTTP_OS}"',
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class AutoDLError(Exception):
    pass


class AuthenticationExpired(AutoDLError):
    pass


class Client:
    def __init__(self, session=None):
        from autodl_session import SessionStore
        self.session = session or requests.Session(impersonate=HTTP_PROFILE)
        self.store = SessionStore()
        self.browser_context = None
        self.page = None
        self.account_type = "sub"
        self.session.headers.update({
            **HTTP_HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/console/instance/list",
        })

    def _request(self, method, path, payload=None):
        if self.page is not None:
            try:
                result = self.page.evaluate("""async ({method, path, payload, origin}) => {
                    if (location.origin !== origin || !path.startsWith('/api/v1/'))
                        throw new Error('Invalid origin or path');
                    const url = new URL(path, location.origin);
                    const headers = {'Accept': 'application/json, text/plain, */*',
                                     'Content-Type': 'application/json',
                                     'Authorization': localStorage.getItem('token'),
                                     'AppVersion': JSON.parse(localStorage.getItem('app_version')).version};
                    if (!headers.Authorization || !headers.AppVersion) throw new Error('Missing session');
                    if (method === 'GET' && payload) {
                        for (const [key, value] of Object.entries(payload)) url.searchParams.set(key, value);
                    }
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), 30000);
                    try {
                        const response = await fetch(url, {method, headers, credentials: 'same-origin',
                            redirect: 'error', signal: controller.signal,
                            ...(method === 'GET' ? {} : {body: JSON.stringify(payload)})});
                        if (response.status === 401) return {__autodl_http_401: true};
                        if (!response.ok) throw new Error('HTTP error');
                        return await response.json();
                    } finally { clearTimeout(timer); }
                }""", {"method": method, "path": path, "payload": payload, "origin": BASE})
            except Exception as exc:
                raise AutoDLError(f"Request failed for {path}: {type(exc).__name__}") from exc
            if isinstance(result, dict) and result.get('__autodl_http_401') is True:
                raise AuthenticationExpired('登录会话已失效')
            return self._result(path, result)
        try:
            response = self.session.request(
                method, BASE + path,
                **({"params": payload} if method == "GET" else {"json": payload}),
                timeout=30, allow_redirects=False,
            )
            if getattr(response, 'status_code', None) == 401:
                raise AuthenticationExpired('登录会话已失效')
            response.raise_for_status()
            result = response.json()
        except (requests.RequestsError, ValueError) as exc:
            raise AutoDLError(f"Request failed for {path}: {type(exc).__name__}") from exc
        return self._result(path, result)

    def _result(self, path, result):
        if not isinstance(result, dict):
            raise AutoDLError(f"Unexpected response for {path}")
        if result.get('code') == 'AuthorizeFailed':
            raise AuthenticationExpired('登录会话已失效')
        if result.get("code") != "Success":
            # Site messages can include account details; keep CLI output minimal.
            raise AutoDLError(f"{path}: {result.get('code', 'UnknownError')}")
        return result.get("data")

    def api(self, method, resource, payload=None):
        """The official client inserts sub_user for these ordinary-container APIs."""
        prefix = "sub_user/" if self.account_type == "sub" else ""
        return self._request(method, "/api/v1/" + prefix + resource, payload)

    def browser_login(self, method):
        """Compatibility entry point for callers using the main-account login."""
        return self.login(method)

    def login(self, method="sub", fresh=False):
        if method not in ("sub", "wechat", "sms"):
            raise AutoDLError("Unsupported authentication method")
        # A failed new login must never leave an older account's token active.
        if self.browser_context is not None:
            self.browser_context.__exit__(None, None, None)
            self.browser_context, self.page = None, None
        self.session.headers.pop("Authorization", None)
        self.session.headers.pop("AppVersion", None)
        self.account_type = "sub"
        from autodl_session import SessionStoreError
        account = os.environ.get('AUTODL_SUB_ACCOUNT', '') if method == 'sub' else ''
        if method == 'sub' and (not account or '@' not in account):
            raise AutoDLError('Set AUTODL_SUB_ACCOUNT to <username>@<main-account-id>')
        if fresh:
            self.store.clear(method, account)
        try:
            saved = None if fresh else self.store.load(method, account)
        except SessionStoreError as exc:
            raise AutoDLError(str(exc)) from exc
        if saved is not None:
            try:
                if method == 'sub':
                    if not isinstance(saved, dict) or not isinstance(saved.get('token'), str) or not isinstance(saved.get('version'), str):
                        raise AuthenticationExpired('已保存的登录会话无效')
                    self.session.headers.update({'Authorization': saved['token'], 'AppVersion': saved['version']})
                    for cookie in saved.get('cookies', []):
                        self.session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
                else:
                    from autodl_auth import restore_browser
                    context = restore_browser(saved)
                    self.page = context.__enter__()
                    self.browser_context = context
                    self.account_type = 'main'
                self.instances()  # Only a read-only request may trigger reauthentication.
                self._save_session(method, account)
                return
            except AuthenticationExpired:
                if self.browser_context is not None:
                    self.browser_context.__exit__(None, None, None)
                self.browser_context, self.page = None, None
                self.session.headers.pop('Authorization', None)
                self.session.headers.pop('AppVersion', None)
                self.session.cookies.clear()
                self.account_type = 'sub'
                self.store.clear(method, account)
        if method == "sub":
            self._subaccount_login()
        else:
            from autodl_auth import headless_login
            context = headless_login(method)
            self.page = context.__enter__()
            self.browser_context = context
            self.account_type = "main"
        self._save_session(method, account)

    def _save_session(self, method, account):
        from autodl_session import SessionStoreError
        try:
            if method == 'sub':
                cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
                           for c in self.session.cookies.jar]
                state = {'token': self.session.headers['Authorization'],
                         'version': self.session.headers['AppVersion'], 'cookies': cookies}
            else:
                from autodl_auth import browser_state
                state = browser_state(self.page)
            self.store.save(method, state, account)
        except (SessionStoreError, OSError) as exc:
            print(f'AutoDL: 无法保存登录会话：{type(exc).__name__}', file=sys.stderr)

    def close(self):
        try:
            if self.browser_context is not None:
                self.browser_context.__exit__(None, None, None)
                self.browser_context, self.page = None, None
        finally:
            self.session.close()

    def _subaccount_login(self):
        account = os.environ.get("AUTODL_SUB_ACCOUNT", "")
        password = os.environ.get("AUTODL_SUB_PASSWORD", "")
        if not account or not password:
            raise AutoDLError("Set AUTODL_SUB_ACCOUNT and AUTODL_SUB_PASSWORD")
        if "@" not in account:
            raise AutoDLError("AUTODL_SUB_ACCOUNT must be <username>@<main-account-id>")

        try:
            version_response = self.session.request("GET", BASE + "/version.json", timeout=30)
            version_response.raise_for_status()
            version = version_response.json().get("version")
        except (requests.RequestsError, ValueError, AttributeError) as exc:
            raise AutoDLError("Could not read AutoDL client version") from exc
        if not isinstance(version, str) or not version.startswith("v"):
            raise AutoDLError("AutoDL client version response changed")
        self.session.headers["AppVersion"] = version

        # Same SHA-1 hex digest sent by the web client's sub-account form.
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest()
        login_data = self._request("POST", "/api/v1/sub_user/login", {
            "sub_name": account,
            "password": digest,
        })
        ticket = login_data.get("ticket") if isinstance(login_data, dict) else None
        if not ticket:
            raise AutoDLError("Login response has no ticket")
        passport_data = self._request("POST", "/api/v1/sub_user/passport", {
            "ticket": ticket,
            "device": {
                "os": {"name": HTTP_OS},
                "browser": {"name": "Chrome", "version": HTTP_CHROME_VERSION, "major": HTTP_CHROME_MAJOR},
            },
        })
        token = passport_data.get("token") if isinstance(passport_data, dict) else None
        if not token:
            raise AutoDLError("Passport response has no token")
        self.session.headers["Authorization"] = token

    def instances(self):
        all_rows = []
        for page in range(1, MAX_PAGES + 1):
            data = self.api("POST", "instance", {
                "page_index": page,
                "page_size": PAGE_SIZE,
                "status": [],
                "charge_type": [],
                "sub_name": "",
                "unbind_sub_user": False,
            })
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise AutoDLError("Instance list response changed")
            all_rows.extend(data["list"])
            max_page = data.get("max_page")
            if not isinstance(max_page, int) or max_page < 0:
                raise AutoDLError("Instance pagination response changed")
            if page >= max_page:
                return all_rows
        raise AutoDLError("Instance list exceeds page limit")

    def power(self, action, uuid):
        if action not in ("start", "stop"):
            raise AutoDLError("Unknown power action")
        if not UUID_RE.fullmatch(uuid):
            raise AutoDLError("Supply an exact instance UUID")
        matches = [row for row in self.instances() if row.get("uuid") == uuid]
        if len(matches) != 1:
            raise AutoDLError("Instance UUID not found or not unique")
        status = matches[0].get("status")
        expected = "shutdown" if action == "start" else "running"
        if status != expected:
            raise AutoDLError(f"Expected {expected} before {action}; found {status}")
        resource = f"instance/power_{'on' if action == 'start' else 'off'}"
        self.api("POST", resource, {"instance_uuid": uuid})
        return {"action": action, "uuid": uuid, "accepted": True, "previous_status": status}


def public_row(row, show_ssh_password=False):
    """Allowlist fields; include the SSH password only by explicit opt-in."""
    fields = (
        "uuid", "name", "status", "sub_status", "region_name",
        "snapshot_gpu_alias_name", "req_gpu_amount", "charge_type", "sub_name",
        "ssh_command",
    )
    result = {key: row.get(key) for key in fields}
    if show_ssh_password:
        result["ssh_password"] = row.get("root_password")
    return result


def main(argv=None):
    sys.modules.setdefault("autodl_instance", sys.modules[__name__])
    from autodl_resources import add_commands, dispatch, image_billing
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", choices=("sub", "wechat", "sms"), default="sub",
                        help="Authentication: sub-account password, WeChat QR, or SMS")
    parser.add_argument('--fresh-login', action='store_true', help='Discard the saved session and log in again')
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list", help="List instances with SSH commands")
    list_parser.add_argument(
        "--show-ssh-password", action="store_true",
        help="Also print SSH passwords (sensitive terminal output)",
    )
    for action in ("start", "stop"):
        sub.add_parser(action, help=f"{action.title()} one instance").add_argument("uuid")
    sub.add_parser('logout', help='Delete the saved local session for the selected account')
    add_commands(sub)
    args = parser.parse_args(argv)

    client = Client()
    try:
        if args.command == "image-billing":
            print(json.dumps(image_billing(), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'logout':
            account = os.environ.get('AUTODL_SUB_ACCOUNT', '') if args.auth == 'sub' else ''
            if args.auth == 'sub' and (not account or '@' not in account):
                raise AutoDLError('Set AUTODL_SUB_ACCOUNT to <username>@<main-account-id>')
            client.store.clear(args.auth, account)
            print('已删除本机保存的会话')
            return 0
        client.login(args.auth, fresh=args.fresh_login)
        if args.command == "list":
            rows = client.instances()
            result = {"total": len(rows), "instances": [
                public_row(row, args.show_ssh_password) for row in rows
            ]}
        elif args.command in ("start", "stop"):
            result = client.power(args.command, args.uuid)
        else:
            result = dispatch(client, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("AutoDL: 已取消", file=sys.stderr)
        return 130
    except (AutoDLError, OSError, ValueError) as exc:
        print(f"AutoDL: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
