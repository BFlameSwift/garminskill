#!/usr/bin/env python3
"""Manage Garmin credentials in macOS Keychain for the Garmin OpenClaw skill."""

import argparse
import subprocess
import sys
from getpass import getpass


SERVICE_GLOBAL = "openclaw.garmin-connect"
SERVICE_CN = "openclaw.garmin-connect.cn"


def service_for_region(is_cn: bool) -> str:
    return SERVICE_CN if is_cn else SERVICE_GLOBAL


def run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/security", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def status(account: str, is_cn: bool) -> int:
    service = service_for_region(is_cn)
    proc = run_security(["find-generic-password", "-s", service, "-a", account])
    if proc.returncode == 0:
        print(f"OK: password exists in macOS Keychain for service={service}, account={account}")
        return 0
    print(f"MISSING: no password found for service={service}, account={account}")
    return 1


def put(account: str, is_cn: bool) -> int:
    service = service_for_region(is_cn)
    password = getpass("Garmin Connect password: ")
    if not password:
        print("Error: password cannot be empty", file=sys.stderr)
        return 2
    proc = run_security(
        [
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-w",
            password,
        ]
    )
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
        return proc.returncode
    print(f"OK: stored Garmin password in macOS Keychain for service={service}, account={account}")
    return 0


def delete(account: str, is_cn: bool) -> int:
    service = service_for_region(is_cn)
    proc = run_security(["delete-generic-password", "-s", service, "-a", account])
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
        return proc.returncode
    print(f"OK: deleted Garmin password from macOS Keychain for service={service}, account={account}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Garmin password in macOS Keychain.")
    parser.add_argument("command", choices=["status", "put", "delete"])
    parser.add_argument("--account", required=True, help="Garmin account email.")
    parser.add_argument("--cn", action="store_true", help="Use Garmin China Keychain service.")
    args = parser.parse_args()

    if args.command == "status":
        return status(args.account, args.cn)
    if args.command == "put":
        return put(args.account, args.cn)
    if args.command == "delete":
        return delete(args.account, args.cn)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
