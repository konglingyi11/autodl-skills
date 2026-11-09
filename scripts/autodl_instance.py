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


class AutoDLError(Exception):
    pass


class Client:
    def __init__(self, session=None):
        self.session = session or requests.Session(impersonate="chrome")
        self.account_type = "sub"
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/console/instance/list",
        })

    def _request(self, method, path, payload=None):
        try:
            response = self.session.request(
                method, BASE + path,
                **({"params": payload} if method == "GET" else {"json": payload}),
                timeout=30, allow_redirects=False,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestsError, ValueError) as exc:
            raise AutoDLError(f"Request failed for {path}: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise AutoDLError(f"Unexpected response for {path}")
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

    def login(self, method="sub"):
        if method not in ("sub", "wechat", "sms"):
            raise AutoDLError("Unsupported authentication method")
        # A failed new login must never leave an older account's token active.
        self.session.headers.pop("Authorization", None)
        self.session.headers.pop("AppVersion", None)
        self.account_type = "sub"
        if method == "sub":
            self._subaccount_login()
        else:
            from autodl_auth import headless_login
            result = headless_login(method)
            self.session.headers.update({"Authorization": result.token, "AppVersion": result.version})
            self.account_type = "main"

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
                "os": {"name": "Windows"},
                "browser": {"name": "Chrome", "version": "120.0.0.0", "major": "120"},
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
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list", help="List instances with SSH commands")
    list_parser.add_argument(
        "--show-ssh-password", action="store_true",
        help="Also print SSH passwords (sensitive terminal output)",
    )
    for action in ("start", "stop"):
        sub.add_parser(action, help=f"{action.title()} one instance").add_argument("uuid")
    add_commands(sub)
    args = parser.parse_args(argv)

    client = Client()
    try:
        if args.command == "image-billing":
            print(json.dumps(image_billing(), ensure_ascii=False, indent=2))
            return 0
        client.login(args.auth)
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
        client.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
