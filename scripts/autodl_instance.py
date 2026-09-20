#!/usr/bin/env python3
"""AutoDL sub-account container instance operations via browser-like HTTP requests."""

import argparse
import hashlib
import json
import os
import re
import sys

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
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/console/instance/list",
        })

    def _request(self, method, path, payload=None):
        try:
            response = self.session.request(
                method, BASE + path, json=payload, timeout=30
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

    def login(self):
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
            data = self._request("POST", "/api/v1/sub_user/instance", {
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
        if not UUID_RE.fullmatch(uuid):
            raise AutoDLError("Supply an exact instance UUID")
        matches = [row for row in self.instances() if row.get("uuid") == uuid]
        if len(matches) != 1:
            raise AutoDLError("Instance UUID not found or not unique")
        status = matches[0].get("status")
        expected = "shutdown" if action == "start" else "running"
        if status != expected:
            raise AutoDLError(f"Expected {expected} before {action}; found {status}")
        path = f"/api/v1/sub_user/instance/power_{'on' if action == 'start' else 'off'}"
        self._request("POST", path, {"instance_uuid": uuid})
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
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list", help="List instances with SSH commands")
    list_parser.add_argument(
        "--show-ssh-password", action="store_true",
        help="Also print SSH passwords (sensitive terminal output)",
    )
    for action in ("start", "stop"):
        sub.add_parser(action, help=f"{action.title()} one instance").add_argument("uuid")
    args = parser.parse_args(argv)

    client = Client()
    try:
        client.login()
        if args.command == "list":
            rows = client.instances()
            result = {"total": len(rows), "instances": [
                public_row(row, args.show_ssh_password) for row in rows
            ]}
        else:
            result = client.power(args.command, args.uuid)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AutoDLError as exc:
        print(f"AutoDL: {exc}", file=sys.stderr)
        return 1
    finally:
        client.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
