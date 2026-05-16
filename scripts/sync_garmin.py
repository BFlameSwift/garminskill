# /// script
# requires-python = ">=3.12"
# dependencies = ["garminconnect>=0.3.3"]
# ///
"""Sync daily health data from Garmin Connect into markdown files."""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, date, timedelta
from getpass import getpass
from pathlib import Path
from typing import Any

import requests
from garminconnect import Garmin
from garminconnect import client as garmin_client


BASE_DIR = Path(__file__).resolve().parent.parent
TOKEN_DIR = Path.home() / ".garminconnect"
CN_WEB_SESSION_FILE = TOKEN_DIR / "garmin_cn_web_session.json"
VERBOSE = False
API_TIMEOUT = 30
KEYCHAIN_SERVICE_GLOBAL = "openclaw.garmin-connect"
KEYCHAIN_SERVICE_CN = "openclaw.garmin-connect.cn"

GARMIN_CN_APP = "https://connect.garmin.cn/app"
GARMIN_GLOBAL_APP = "https://connect.garmin.com/app"

NAV_LINES = {
    "主页",
    "挑战",
    "日历",
    "新消息",
    "活动",
    "健康统计",
    "营养",
    "表现统计",
    "高尔夫",
    "训练和计划",
    "装备",
    "Insights",
    "报告",
    "朋友",
    "群组",
    "徽章",
    "个人纪录",
    "目标",
    "活动追踪准确性",
    "如何同步",
    "导入数据",
}


def env_flag(name: str) -> bool:
    """Return true for common truthy environment variable values."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def keychain_service(is_cn: bool) -> str:
    """Return the macOS Keychain service name for this Garmin account type."""
    return KEYCHAIN_SERVICE_CN if is_cn else KEYCHAIN_SERVICE_GLOBAL


def read_keychain_password(email: str, is_cn: bool) -> str:
    """Read a Garmin password from macOS Keychain without printing it."""
    proc = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            keychain_service(is_cn),
            "-a",
            email,
            "-w",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "No Garmin password found in macOS Keychain for "
            f"service={keychain_service(is_cn)!r}, account={email!r}. "
            "Store it with scripts/garmin_keychain.py put first, or use "
            "--password-source prompt."
        )
    password = proc.stdout.rstrip("\n")
    if not password:
        raise RuntimeError("Garmin password in macOS Keychain is empty.")
    return password


def get_setup_password(email: str, is_cn: bool, password_source: str) -> str:
    """Resolve the password for setup without using command-line arguments."""
    if password_source == "keychain":
        return read_keychain_password(email, is_cn)
    if password_source != "prompt":
        raise RuntimeError(f"Unsupported password source: {password_source}")
    password = getpass("Garmin Connect password: ")
    if not password:
        print("Error: Password cannot be empty.", file=sys.stderr)
        sys.exit(1)
    return password


def configure_garmin_region(is_cn: bool) -> None:
    """Patch garminconnect globals that are not fully domain-aware for CN API sync."""
    if not is_cn:
        return

    garmin_client.DI_TOKEN_URL = "https://diauth.garmin.cn/di-oauth2-service/oauth/token"
    # Garmin China rejects a .cn grant_type with unsupported_grant_type; keep
    # the library's .com grant_type while using China login/service endpoints.
    garmin_client.IOS_SERVICE_URL = "https://connect.garmin.cn/app/"
    garmin_client.MOBILE_SSO_SERVICE_URL = "https://connect.garmin.cn/app/"
    garmin_client.PORTAL_SSO_SERVICE_URL = "https://connect.garmin.cn/app/"


def write_secret_json(path: Path, payload: dict[str, Any]) -> None:
    """Write sensitive session/token material with user-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        path.parent.chmod(0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except Exception:
        with contextlib.suppress(Exception):
            os.close(fd)
        raise
    os.replace(tmp, path)
    with contextlib.suppress(Exception):
        path.chmod(0o600)


def json_default(value: Any) -> str:
    """Serialize non-standard values from Garmin responses conservatively."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    """Write personal health JSON with user-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        path.parent.chmod(0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=json_default)
            handle.write("\n")
    except Exception:
        with contextlib.suppress(Exception):
            os.close(fd)
        raise
    os.replace(tmp, path)
    with contextlib.suppress(Exception):
        path.chmod(0o600)


def extract_window_json(html: str, name: str) -> Any | None:
    """Extract a JSON object assigned as window.NAME = ...; from Garmin HTML."""
    match = re.search(rf"window\.{re.escape(name)}\s*=\s*(.*?);", html, re.S)
    if not match:
        return None
    return json.loads(match.group(1))


def parse_cn_modern_state(html: str) -> dict[str, Any]:
    """Parse signed-in Garmin CN modern app state from HTML."""
    profile = extract_window_json(html, "VIEWER_SOCIAL_PROFILE")
    if not isinstance(profile, dict) or not profile.get("displayName"):
        raise RuntimeError("Garmin CN session is not signed in")
    csrf_match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
    if not csrf_match:
        raise RuntimeError("Garmin CN session is missing csrf token")
    return {
        "csrf": csrf_match.group(1),
        "profile": profile,
        "userPreferences": extract_window_json(html, "VIEWER_USERPREFERENCES") or {},
        "sessionExpires": extract_window_json(html, "SESSION_EXPIRES") or {},
    }


def serialize_cookies(session: Any) -> list[dict[str, Any]]:
    """Serialize cookies from requests or curl_cffi sessions."""
    cookies = []
    jar = getattr(getattr(session, "cookies", None), "jar", None) or getattr(
        session, "cookies", []
    )
    for cookie in jar:
        domain = getattr(cookie, "domain", "") or ""
        if "garmin.cn" not in domain:
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": domain,
                "path": getattr(cookie, "path", "/") or "/",
                "expires": getattr(cookie, "expires", None),
                "secure": bool(getattr(cookie, "secure", False)),
            }
        )
    return cookies


def save_cn_web_session(session: Any, state: dict[str, Any]) -> None:
    """Persist Garmin CN web-session auth for later /gc-api syncs."""
    payload = {
        "kind": "garmin-cn-web-session-v1",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "csrf": state["csrf"],
        "sessionExpires": state.get("sessionExpires") or {},
        "profile": {
            "displayName": state["profile"].get("displayName"),
            "profileId": state["profile"].get("profileId"),
            "garminGUID": state["profile"].get("garminGUID"),
            "fullName": state["profile"].get("fullName"),
            "userName": state["profile"].get("userName"),
        },
        "userPreferences": state.get("userPreferences") or {},
        "cookies": serialize_cookies(session),
    }
    write_secret_json(CN_WEB_SESSION_FILE, payload)


class GarminCnWebClient:
    """Garmin China client using the signed-in web session's /gc-api JSON API."""

    def __init__(self, session_file: Path = CN_WEB_SESSION_FILE) -> None:
        self.session_file = session_file
        self.session = requests.Session()
        self.csrf = ""
        self.display_name = ""
        self.full_name = ""
        self.unit_system: str | None = None
        self._load()
        self.refresh_session()

    def _load(self) -> None:
        data = json.loads(self.session_file.read_text())
        if data.get("kind") != "garmin-cn-web-session-v1":
            raise RuntimeError("Unsupported Garmin CN session cache")
        for cookie in data.get("cookies") or []:
            name = cookie.get("name")
            value = cookie.get("value")
            domain = cookie.get("domain")
            if not name or value is None or not domain:
                continue
            self.session.cookies.set(
                name,
                value,
                domain=domain,
                path=cookie.get("path") or "/",
            )
        self._apply_state(data)

    def _apply_state(self, state: dict[str, Any]) -> None:
        self.csrf = state.get("csrf") or self.csrf
        profile = state.get("profile") or {}
        self.display_name = profile.get("displayName") or self.display_name
        self.full_name = profile.get("fullName") or self.full_name
        preferences = state.get("userPreferences") or {}
        user_data = preferences.get("userData") if isinstance(preferences, dict) else {}
        if isinstance(user_data, dict):
            self.unit_system = user_data.get("measurementSystem") or self.unit_system

    def refresh_session(self) -> None:
        response = self.session.get(
            "https://connect.garmin.cn/modern/",
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=API_TIMEOUT,
        )
        response.raise_for_status()
        state = parse_cn_modern_state(response.text)
        save_cn_web_session(self.session, state)
        self._apply_state(state)

    def connectapi(self, path: str, **kwargs: Any) -> Any:
        url = f"https://connect.garmin.cn/gc-api/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://connect.garmin.cn",
            "Referer": "https://connect.garmin.cn/modern/",
            "Connect-Csrf-Token": self.csrf,
        }
        response = self.session.get(
            url,
            headers=headers,
            params=kwargs.get("params"),
            timeout=kwargs.get("timeout", API_TIMEOUT),
        )
        if response.status_code in {401, 403}:
            self.refresh_session()
            headers["Connect-Csrf-Token"] = self.csrf
            response = self.session.get(
                url,
                headers=headers,
                params=kwargs.get("params"),
                timeout=kwargs.get("timeout", API_TIMEOUT),
            )
        if response.status_code == 204:
            return {}
        if response.status_code >= 400:
            raise RuntimeError(f"Garmin CN API {response.status_code}: {response.text[:300]}")
        return response.json()

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(
            f"/wellness-service/wellness/dailySleepData/{self.display_name}",
            params={"date": cdate, "nonSleepBufferMinutes": 60},
        )

    def get_user_summary(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(
            f"/usersummary-service/usersummary/daily/{self.display_name}",
            params={"calendarDate": cdate},
        )

    def get_heart_rates(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(
            f"/wellness-service/wellness/dailyHeartRate/{self.display_name}",
            params={"date": cdate},
        )

    def get_body_battery(self, startdate: str, enddate: str | None = None) -> list[dict[str, Any]]:
        return self.connectapi(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            params={"startDate": startdate, "endDate": enddate or startdate},
        )

    def get_hrv_data(self, cdate: str) -> dict[str, Any] | None:
        return self.connectapi(f"/hrv-service/hrv/{cdate}")

    def get_spo2_data(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(f"/wellness-service/wellness/daily/spo2/{cdate}")

    def get_daily_weigh_ins(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(
            f"/weight-service/weight/dayview/{cdate}",
            params={"includeAll": "true"},
        )

    def get_all_day_stress(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(f"/wellness-service/wellness/dailyStress/{cdate}")

    def get_training_readiness(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(f"/metrics-service/metrics/trainingreadiness/{cdate}")

    def get_respiration_data(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(f"/wellness-service/wellness/daily/respiration/{cdate}")

    def get_fitnessage_data(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(f"/fitnessage-service/fitnessage/{cdate}")

    def get_intensity_minutes_data(self, cdate: str) -> dict[str, Any]:
        return self.connectapi(f"/wellness-service/wellness/daily/im/{cdate}")

    def get_activities_by_date(
        self,
        startdate: str,
        enddate: str | None = None,
        activitytype: str | None = None,
        sortorder: str | None = None,
    ) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        start = 0
        limit = 20
        while True:
            params: dict[str, str] = {
                "startDate": startdate,
                "start": str(start),
                "limit": str(limit),
            }
            if enddate:
                params["endDate"] = enddate
            if activitytype:
                params["activityType"] = activitytype
            if sortorder:
                params["sortOrder"] = sortorder
            batch = self.connectapi(
                "/activitylist-service/activities/search/activities",
                params=params,
            )
            if not batch:
                break
            activities.extend(batch)
            start += limit
        return activities


def setup_cn_web_session(email: str, password_source: str = "prompt") -> None:
    """Authenticate Garmin China via portal session and cache /gc-api cookies."""
    password = get_setup_password(email, True, password_source)
    client = garmin_client.Client(domain="garmin.cn")
    try:
        client._portal_web_login_cffi(email, password)
    except Exception:
        client._portal_web_login_requests(email, password)
    response = client.cs.get("https://connect.garmin.cn/modern/", timeout=30)
    response.raise_for_status()
    state = parse_cn_modern_state(response.text)
    save_cn_web_session(client.cs, state)
    print(f"Success! Garmin CN web-session API cache saved in {CN_WEB_SESSION_FILE}")
    print("You can now run the sync command without credentials.")


def applescript_quote(value: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_osascript(script: str, timeout: int = 60) -> str:
    """Run AppleScript and return stdout."""
    proc = subprocess.run(
        ["/usr/bin/osascript"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(err or f"osascript exited with {proc.returncode}")
    return proc.stdout.strip()


def chrome_exec_js(js_code: str, timeout: int = 60) -> str:
    """Execute JavaScript in Chrome's active tab via Apple Events."""
    script = f"""
tell application "Google Chrome"
  if (count of windows) = 0 then error "Google Chrome is not running"
  return execute active tab of front window javascript {applescript_quote(js_code)}
end tell
"""
    return run_osascript(script, timeout=timeout)


def chrome_open_work_tab(url: str) -> None:
    """Open a temporary Chrome tab for browser-based Garmin sync."""
    script = f"""
tell application "Google Chrome"
  activate
  if (count of windows) = 0 then make new window
  make new tab at end of tabs of front window with properties {{URL:{applescript_quote(url)}}}
  set active tab index of front window to (count of tabs of front window)
end tell
"""
    run_osascript(script)


def chrome_set_url(url: str) -> None:
    """Navigate the active Chrome tab."""
    script = f"""
tell application "Google Chrome"
  if (count of windows) = 0 then error "Google Chrome is not running"
  set URL of active tab of front window to {applescript_quote(url)}
end tell
"""
    run_osascript(script)


def chrome_close_active_tab() -> None:
    """Close the active Chrome tab, ignoring failures."""
    script = """
tell application "Google Chrome"
  if (count of windows) > 0 then close active tab of front window
end tell
"""
    try:
        run_osascript(script)
    except Exception:
        pass


def collect_chrome_snapshot(url: str, label: str, wait_seconds: int = 8) -> dict:
    """Navigate to a Garmin page and collect visible text from Chrome."""
    chrome_set_url(url)
    expected_prefix = url.rstrip("/")
    time.sleep(2)
    deadline = time.time() + max(wait_seconds, 3)
    last: dict | None = None
    js_code = """
(() => {
  const lines = document.body.innerText
    .split(/\\n+/)
    .map(s => s.trim())
    .filter(Boolean);
  return JSON.stringify({
    href: location.href,
    title: document.title,
    ready: document.readyState,
    lineCount: lines.length,
    lines: lines.slice(0, 260)
  });
})()
"""
    while time.time() < deadline:
        try:
            raw = chrome_exec_js(js_code)
            last = json.loads(raw)
            text = "\n".join(last.get("lines") or [])
            if (
                str(last.get("href", "")).rstrip("/").startswith(expected_prefix)
                and last.get("ready") == "complete"
                and last.get("lineCount", 0) > 8
                and "Garmin Connect" in last.get("title", "")
                and not looks_like_login_page(text)
            ):
                break
        except Exception as e:
            last = {"label": label, "error": str(e)}
        time.sleep(1)
    if last is None:
        last = {"label": label, "error": "No snapshot returned"}
    last["label"] = label
    return last


def looks_like_login_page(text: str) -> bool:
    """Detect Garmin login/error pages instead of authenticated app pages."""
    lowered = text.lower()
    return (
        ("password" in lowered or "密码" in text)
        and ("sign in" in lowered or "登录" in text)
        and "主页" not in text
    )


def clean_browser_lines(lines: list[str], limit: int = 120) -> list[str]:
    """Remove repeated chrome/navigation noise while preserving data lines."""
    cleaned: list[str] = []
    previous = None
    for line in lines:
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line == previous:
            continue
        previous = line
        if line in NAV_LINES:
            continue
        if line in {"查看全部", "隐藏", "编辑主页", "预览"}:
            continue
        cleaned.append(line)
        if len(cleaned) >= limit:
            break
    return cleaned


def browser_routes_for_day(day: date, is_cn: bool) -> list[tuple[str, str]]:
    """Return Garmin web routes worth scraping for one day."""
    base = GARMIN_CN_APP if is_cn else GARMIN_GLOBAL_APP
    day_str = day.isoformat()
    return [
        ("Home", f"{base}/home"),
        ("Sleep", f"{base}/sleep/{day_str}"),
        ("Heart Rate", f"{base}/heart-rate/{day_str}"),
        ("Stress", f"{base}/stress/{day_str}/0"),
        ("Training Readiness", f"{base}/training-readiness/{day_str}"),
        ("Activities", f"{base}/activities"),
    ]


def sync_days_from_browser(days: list[date], output_dir: Path, is_cn: bool) -> None:
    """Sync Garmin data by reading the user's logged-in Chrome session."""
    base = GARMIN_CN_APP if is_cn else GARMIN_GLOBAL_APP
    chrome_open_work_tab(f"{base}/home")
    try:
        for day in sorted(days):
            sync_day_from_browser(day, output_dir, is_cn)
    finally:
        chrome_close_active_tab()


def sync_day_from_browser(day: date, output_dir: Path, is_cn: bool) -> None:
    """Write a health markdown file from Garmin Connect web UI snapshots."""
    day_str = day.isoformat()
    display_date = day.strftime("%B %-d, %Y")
    sections = [
        f"# Health — {display_date}",
        (
            "_Source: Garmin Connect web UI via the user's logged-in Chrome "
            f"session. Synced at {datetime.now().isoformat(timespec='seconds')}._"
        ),
    ]

    snapshots: list[dict] = []
    for label, url in browser_routes_for_day(day, is_cn):
        if VERBOSE:
            print(f"  [browser] Reading {label}: {url}", file=sys.stderr)
        snapshots.append(collect_chrome_snapshot(url, label))

    login_errors = []
    for snap in snapshots:
        lines = snap.get("lines") or []
        text = "\n".join(lines)
        if looks_like_login_page(text):
            login_errors.append(snap.get("href", snap.get("label", "unknown")))

    if login_errors:
        raise RuntimeError(
            "Garmin Connect is not logged in in Chrome. Open "
            f"{GARMIN_CN_APP if is_cn else GARMIN_GLOBAL_APP}/home, log in, "
            "then rerun with --browser."
        )

    for snap in snapshots:
        label = snap.get("label", "Snapshot")
        if snap.get("error"):
            sections.append(f"## {label}\nUnable to read page: {snap['error']}")
            continue
        lines = clean_browser_lines(snap.get("lines") or [])
        if not lines:
            continue
        sections.append(f"## {label}\n" + "\n".join(f"- {line}" for line in lines))

    if len(sections) <= 2:
        print(f"  {day_str}: No browser-visible Garmin data available, skipping.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{day_str}.md"
    output_file.write_text("\n\n".join(sections) + "\n")
    print(f"  {day_str}: Written to {output_file}")


def setup(email: str, is_cn: bool = False, password_source: str = "prompt") -> None:
    """One-time interactive setup: authenticate with email/password and cache tokens."""
    if is_cn:
        setup_cn_web_session(email, password_source=password_source)
        return

    configure_garmin_region(is_cn)
    password = get_setup_password(email, is_cn, password_source)

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    tokenstore = str(TOKEN_DIR)
    client = Garmin(email, password, is_cn=is_cn)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            client.login(tokenstore)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if attempt < 2 and "no profile" in str(e).lower():
                time.sleep(2**attempt)
                continue
            break

    if last_exc is not None:
        msg = str(last_exc).lower()
        print(f"Error: Authentication failed — {last_exc}", file=sys.stderr)
        if "account_locked" in msg or "generalLoginAccountLocked" in str(last_exc):
            print(
                "\nGarmin reports this account is temporarily locked. Stop retrying\n"
                "from the script, sign in once in a browser to unlock/check the\n"
                "account, then rerun setup.",
                file=sys.stderr,
            )
        elif "no profile" in msg or "connectapi" in msg:
            print(
                "\nThis usually means Garmin's servers are temporarily blocking requests.\n"
                "Try again in a few minutes. If it persists, double-check your password.",
                file=sys.stderr,
            )
        elif "401" in msg or "unauthorized" in msg or "credentials" in msg:
            print(
                "\nDouble-check your email and password. If you have two-factor\n"
                "authentication (2FA) enabled on your Garmin account, you may need\n"
                "to disable it — the garminconnect library does not support 2FA.",
                file=sys.stderr,
            )
        elif "cloudflare" in msg or "captcha" in msg or "403" in msg:
            print(
                "\nGarmin's Cloudflare protection may be blocking this request.\n"
                "Wait a few minutes and try again.",
                file=sys.stderr,
            )
        sys.exit(1)

    print(f"Success! Tokens cached in {TOKEN_DIR}")
    print("You can now run the sync command without credentials.")


def authenticate(is_cn: bool = False) -> Garmin:
    """Authenticate with Garmin Connect using cached tokens only."""
    if is_cn:
        if not CN_WEB_SESSION_FILE.exists():
            print(
                "Error: No Garmin CN web-session API cache found.\n"
                "Run setup first:\n\n"
                "  uv run scripts/sync_garmin.py --setup --email you@example.com --cn\n",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            return GarminCnWebClient()  # type: ignore[return-value]
        except Exception as e:
            print(
                f"Error: Garmin CN cached web session is not usable: {e}\n"
                "Run setup again:\n\n"
                "  uv run scripts/sync_garmin.py --setup --email you@example.com --cn\n",
                file=sys.stderr,
            )
            sys.exit(1)

    configure_garmin_region(is_cn)
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    tokenstore = str(TOKEN_DIR)
    if not any(TOKEN_DIR.iterdir()):
        cn_suffix = " --cn" if is_cn else ""
        print(
            "Error: No cached tokens found.\n"
            "Run setup first:\n\n"
            f"  uv run scripts/sync_garmin.py --setup --email you@example.com{cn_suffix}\n",
            file=sys.stderr,
        )
        sys.exit(1)
    client = Garmin(is_cn=is_cn)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            client.login(tokenstore)
            return client
        except FileNotFoundError:
            cn_suffix = " --cn" if is_cn else ""
            print(
                "Error: No cached tokens found.\n"
                "Run setup first:\n\n"
                f"  uv run scripts/sync_garmin.py --setup --email you@example.com{cn_suffix}\n",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            last_exc = e
            if attempt < 2 and "no profile" in str(e).lower():
                time.sleep(2**attempt)
                continue
            break

    msg = str(last_exc).lower()
    if "no profile" in msg or "connectapi" in msg:
        print(
            "Error: Garmin's servers returned 'No profile'. This is usually\n"
            "temporary — wait a few minutes and try again. If it persists,\n"
            "re-run setup:\n\n"
            "  uv run scripts/sync_garmin.py --setup --email you@example.com\n",
            file=sys.stderr,
        )
    else:
        print(
            f"Error: Authentication failed — {last_exc}\n"
            "Your cached tokens may have expired. Re-run setup:\n\n"
            "  uv run scripts/sync_garmin.py --setup --email you@example.com\n",
            file=sys.stderr,
        )
    sys.exit(1)


def fmt_duration(seconds: float | int | None) -> str:
    """Format seconds into 'Xh Ym' string."""
    if seconds is None:
        return "—"
    total_minutes = int(seconds) // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes:02d}m"


def fmt_duration_mmss(seconds: float | int | None) -> str:
    """Format seconds into 'MM:SS' string."""
    if seconds is None:
        return "—"
    total_seconds = int(seconds)
    minutes = total_seconds // 60
    secs = total_seconds % 60
    return f"{minutes}:{secs:02d}"


def fetch_sleep(client: Garmin, day: str) -> str | None:
    """Fetch and format sleep data."""
    try:
        data = client.get_sleep_data(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Sleep fetch failed: {e}", file=sys.stderr)
        return None

    daily = data.get("dailySleepDTO", {})
    if not daily or not daily.get("sleepTimeSeconds"):
        return None

    total = fmt_duration(daily.get("sleepTimeSeconds"))
    deep = fmt_duration(daily.get("deepSleepSeconds"))
    light = fmt_duration(daily.get("lightSleepSeconds"))
    rem = fmt_duration(daily.get("remSleepSeconds"))
    awake = fmt_duration(daily.get("awakeSleepSeconds"))

    score = daily.get("sleepScores", {}).get("overall", {}).get("value")
    qualifier = daily.get("sleepScores", {}).get("overall", {}).get("qualifierKey", "")
    # Clean up qualifier like "GOOD" -> "Good"
    qualifier_str = qualifier.replace("_", " ").title() if qualifier else ""

    header = f"## Sleep: {total}"
    if qualifier_str:
        header += f" ({qualifier_str})"

    lines = [header]
    lines.append(f"Deep: {deep} | Light: {light} | REM: {rem} | Awake: {awake}")
    if score is not None:
        lines.append(f"Sleep Score: {score}")

    return "\n".join(lines)


def fetch_body(client: Garmin, day: str) -> str | None:
    """Fetch and format body/activity summary data."""
    parts = []

    # User summary (steps, calories, distance, floors, active minutes)
    summary = None
    try:
        summary = client.get_user_summary(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] User summary fetch failed: {e}", file=sys.stderr)

    # Heart rates
    hr_data = None
    try:
        hr_data = client.get_heart_rates(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Heart rate fetch failed: {e}", file=sys.stderr)

    # Body battery
    battery = None
    try:
        bb_data = client.get_body_battery(day, day)
        if bb_data and isinstance(bb_data, list) and len(bb_data) > 0:
            # Get the latest charged value
            values = [
                e.get("chargedValue", 0)
                for e in bb_data
                if e.get("chargedValue") is not None
            ]
            if values:
                battery = max(values)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Body battery fetch failed: {e}", file=sys.stderr)

    # HRV
    hrv = None
    try:
        hrv_data = client.get_hrv_data(day)
        if hrv_data:
            summary_hrv = hrv_data.get("hrvSummary", {})
            if summary_hrv:
                hrv = summary_hrv.get("weeklyAvg") or summary_hrv.get("lastNightAvg")
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] HRV fetch failed: {e}", file=sys.stderr)

    # SpO2
    spo2 = None
    try:
        spo2_data = client.get_spo2_data(day)
        if spo2_data:
            spo2 = spo2_data.get("averageSpO2")
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] SpO2 fetch failed: {e}", file=sys.stderr)

    # Weight
    weight = None
    try:
        weight_data = client.get_daily_weigh_ins(day)
        if weight_data:
            entries = weight_data.get("dateWeightList", [])
            if entries:
                grams = entries[0].get("weight")
                if grams:
                    weight = round(grams / 1000, 1)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Weight fetch failed: {e}", file=sys.stderr)

    if not summary and not hr_data and battery is None and hrv is None:
        return None

    # Build header line
    steps = summary.get("totalSteps") if summary else None
    calories = summary.get("totalKilocalories") if summary else None

    header_parts = []
    if steps is not None:
        header_parts.append(f"{steps:,} steps")
    if calories is not None:
        header_parts.append(f"{int(calories):,} cal")

    header = "## Body"
    if header_parts:
        header += ": " + " | ".join(header_parts)

    lines = [header]

    # Distance and floors
    detail_parts = []
    if summary:
        distance_m = summary.get("totalDistanceMeters")
        if distance_m is not None:
            detail_parts.append(f"Distance: {distance_m / 1000:.1f} km")
        floors = summary.get("floorsAscended")
        if floors is not None:
            detail_parts.append(f"Floors: {int(floors)}")
    if detail_parts:
        lines.append(" | ".join(detail_parts))

    # HR line
    hr_parts = []
    if hr_data:
        resting = hr_data.get("restingHeartRate")
        if resting:
            hr_parts.append(f"Resting HR: {resting} bpm")
        max_hr = hr_data.get("maxHeartRate")
        if max_hr:
            hr_parts.append(f"Max HR: {max_hr} bpm")
    if hr_parts:
        lines.append(" | ".join(hr_parts))

    # Battery, HRV, SpO2, Weight
    extra_parts = []
    if battery is not None:
        extra_parts.append(f"Body Battery: {battery}")
    if hrv is not None:
        extra_parts.append(f"HRV: {hrv} ms")
    if extra_parts:
        lines.append(" | ".join(extra_parts))

    if spo2 is not None:
        lines.append(f"SpO2: {spo2}%")

    if weight is not None:
        lines.append(f"Weight: {weight} kg")

    return "\n".join(lines)


def fetch_stress(client: Garmin, day: str) -> str | None:
    """Fetch and format stress data."""
    try:
        data = client.get_all_day_stress(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Stress fetch failed: {e}", file=sys.stderr)
        return None

    if not data:
        return None

    avg = data.get("overallStressLevel")
    if avg is None:
        return None

    if avg < 26:
        level = "Rest"
    elif avg < 51:
        level = "Low"
    elif avg < 76:
        level = "Medium"
    else:
        level = "High"

    return f"## Stress: Avg {avg} ({level})"


def fetch_training_readiness(client: Garmin, day: str) -> str | None:
    """Fetch and format training readiness data."""
    try:
        data = client.get_training_readiness(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Training readiness fetch failed: {e}", file=sys.stderr)
        return None

    if not data or not isinstance(data, list) or len(data) == 0:
        return None

    entry = data[0]
    score = entry.get("score")
    if score is None:
        return None

    level = entry.get("level", "").replace("_", " ").title()
    feedback = entry.get("feedbackShort", "").replace("_", " ").title()

    line = f"## Training Readiness: {score}"
    if level:
        line += f" ({level})"
    if feedback:
        line += f" — {feedback}"
    return line


def fetch_respiration(client: Garmin, day: str) -> str | None:
    """Fetch and format respiration data."""
    try:
        data = client.get_respiration_data(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Respiration fetch failed: {e}", file=sys.stderr)
        return None

    if not data:
        return None

    parts = []
    avg_waking = data.get("avgWakingRespirationValue")
    if avg_waking:
        parts.append(f"Waking: {avg_waking:.0f} brpm")
    avg_sleeping = data.get("avgSleepRespirationValue")
    if avg_sleeping:
        parts.append(f"Sleeping: {avg_sleeping:.0f} brpm")
    lowest = data.get("lowestRespirationValue")
    highest = data.get("highestRespirationValue")
    if lowest and highest:
        parts.append(f"Range: {lowest:.0f}–{highest:.0f}")

    if not parts:
        return None

    return "## Respiration: " + " | ".join(parts)


def fetch_fitness_age(client: Garmin, day: str) -> str | None:
    """Fetch and format fitness age data."""
    try:
        data = client.get_fitnessage_data(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Fitness age fetch failed: {e}", file=sys.stderr)
        return None

    if not data:
        return None

    fitness_age = data.get("fitnessAge")
    chrono_age = data.get("chronologicalAge")
    if fitness_age is None:
        return None

    line = f"## Fitness Age: {int(fitness_age)}"
    if chrono_age is not None:
        diff = int(fitness_age) - chrono_age
        if diff < 0:
            line += f" ({abs(diff)} years younger)"
        elif diff > 0:
            line += f" ({diff} years older)"
    return line


def fetch_intensity_minutes(client: Garmin, day: str) -> str | None:
    """Fetch and format weekly intensity minutes."""
    try:
        data = client.get_intensity_minutes_data(day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Intensity minutes fetch failed: {e}", file=sys.stderr)
        return None

    if not data:
        return None

    moderate = data.get("weeklyModerate")
    vigorous = data.get("weeklyVigorous")
    total = data.get("weeklyTotal")
    goal = data.get("weekGoal")

    if total is None:
        return None

    parts = [f"## Intensity Minutes: {total} weekly"]
    detail = []
    if moderate is not None:
        detail.append(f"Moderate: {moderate}")
    if vigorous is not None:
        detail.append(f"Vigorous: {vigorous}")
    if goal is not None:
        detail.append(f"Goal: {goal}")
    if detail:
        parts.append(" | ".join(detail))

    return "\n".join(parts)


def fetch_activities(client: Garmin, day: str) -> str | None:
    """Fetch and format activities for the day."""
    try:
        activities = client.get_activities_by_date(day, day)
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] Activities fetch failed: {e}", file=sys.stderr)
        return None

    if not activities:
        return None

    lines = ["## Activities"]
    for act in activities:
        name = act.get("activityName", "Activity")
        duration = fmt_duration_mmss(act.get("duration"))
        header_parts = [f"**{name}** — {duration}"]

        distance = act.get("distance")
        if distance and distance > 0:
            header_parts.append(f"{distance / 1000:.1f} km")

        calories = act.get("calories")
        if calories and calories > 0:
            header_parts.append(f"{int(calories)} cal")

        lines.append("- " + ", ".join(header_parts))

        # Detail lines
        details = []

        avg_hr = act.get("averageHR")
        max_hr = act.get("maxHR")
        if avg_hr and avg_hr > 0:
            hr_str = f"Avg HR {int(avg_hr)}"
            if max_hr and max_hr > 0:
                hr_str += f" / Max {int(max_hr)}"
            details.append(hr_str)

        elev = act.get("elevationGain")
        if elev and elev > 0:
            details.append(f"Elevation: +{int(elev)}m")

        avg_speed = act.get("averageSpeed")
        if avg_speed and avg_speed > 0 and distance and distance > 0:
            pace_sec = 1000 / avg_speed
            pace_min = int(pace_sec) // 60
            pace_s = int(pace_sec) % 60
            details.append(f"Pace: {pace_min}:{pace_s:02d}/km")

        cadence = act.get("averageRunningCadenceInStepsPerMinute")
        if cadence and cadence > 0:
            details.append(f"Cadence: {int(cadence)} spm")

        avg_power = act.get("avgPower")
        if avg_power and avg_power > 0:
            power_str = f"Power: {int(avg_power)}W"
            max_power = act.get("maxPower")
            if max_power and max_power > 0:
                power_str += f" / Max {int(max_power)}W"
            details.append(power_str)

        aero_te = act.get("aerobicTrainingEffect")
        anaero_te = act.get("anaerobicTrainingEffect")
        if aero_te and aero_te > 0:
            te_str = f"Training Effect: {aero_te:.1f} aerobic"
            if anaero_te and anaero_te > 0:
                te_str += f" / {anaero_te:.1f} anaerobic"
            details.append(te_str)

        vo2 = act.get("vO2MaxValue")
        if vo2 and vo2 > 0:
            details.append(f"VO2 Max: {int(vo2)}")

        if details:
            lines.append("  " + " | ".join(details))

    return "\n".join(lines)


def fetch_metric(label: str, call: Any) -> tuple[Any | None, str | None]:
    """Fetch one Garmin metric while preserving partial daily syncs."""
    try:
        return call(), None
    except Exception as e:
        if VERBOSE:
            print(f"    [verbose] {label} fetch failed: {e}", file=sys.stderr)
        return None, str(e)


def collect_daily_data(client: Garmin, day: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Collect the daily Garmin API payloads used for markdown and raw JSON."""
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}

    specs = [
        ("sleep", "Sleep", lambda: client.get_sleep_data(day)),
        ("userSummary", "User summary", lambda: client.get_user_summary(day)),
        ("heartRates", "Heart rate", lambda: client.get_heart_rates(day)),
        ("bodyBattery", "Body battery", lambda: client.get_body_battery(day, day)),
        ("hrv", "HRV", lambda: client.get_hrv_data(day)),
        ("spo2", "SpO2", lambda: client.get_spo2_data(day)),
        ("weight", "Weight", lambda: client.get_daily_weigh_ins(day)),
        ("stress", "Stress", lambda: client.get_all_day_stress(day)),
        ("trainingReadiness", "Training readiness", lambda: client.get_training_readiness(day)),
        ("respiration", "Respiration", lambda: client.get_respiration_data(day)),
        ("fitnessAge", "Fitness age", lambda: client.get_fitnessage_data(day)),
        ("intensityMinutes", "Intensity minutes", lambda: client.get_intensity_minutes_data(day)),
        ("activities", "Activities", lambda: client.get_activities_by_date(day, day)),
    ]

    for key, label, call in specs:
        value, error = fetch_metric(label, call)
        if error:
            errors[key] = error
        else:
            data[key] = value
    return data, errors


def values_from_pairs(items: Any) -> list[float]:
    """Extract numeric values from Garmin time-series pair arrays."""
    values: list[float] = []
    if not isinstance(items, list):
        return values
    for item in items:
        value = None
        if isinstance(item, list) and len(item) >= 2:
            value = item[1]
        elif isinstance(item, dict):
            value = item.get("value") or item.get("heartRate") or item.get("stressLevel")
        if isinstance(value, (int, float)) and value > 0:
            values.append(float(value))
    return values


def fmt_avg(values: list[float]) -> str | None:
    """Format average/min/max for a numeric series."""
    if not values:
        return None
    avg = round(sum(values) / len(values))
    return f"Avg {avg} | Low {round(min(values))} | High {round(max(values))}"


def format_sleep_data(data: dict[str, Any]) -> str | None:
    """Format sleep data from a collected Garmin payload."""
    payload = data.get("sleep")
    if not isinstance(payload, dict):
        return None
    daily = payload.get("dailySleepDTO", {})
    if not daily or not daily.get("sleepTimeSeconds"):
        return None

    total = fmt_duration(daily.get("sleepTimeSeconds"))
    deep = fmt_duration(daily.get("deepSleepSeconds"))
    light = fmt_duration(daily.get("lightSleepSeconds"))
    rem = fmt_duration(daily.get("remSleepSeconds"))
    awake = fmt_duration(daily.get("awakeSleepSeconds"))
    scores = daily.get("sleepScores", {}) or {}
    overall = scores.get("overall", {}) if isinstance(scores, dict) else {}
    score = overall.get("value")
    qualifier = overall.get("qualifierKey", "")
    qualifier_str = qualifier.replace("_", " ").title() if qualifier else ""

    header = f"## Sleep: {total}"
    if qualifier_str:
        header += f" ({qualifier_str})"
    lines = [header, f"Deep: {deep} | Light: {light} | REM: {rem} | Awake: {awake}"]
    if score is not None:
        lines.append(f"Sleep Score: {score}")

    score_parts = []
    if isinstance(scores, dict):
        for key, item in scores.items():
            if key == "overall" or not isinstance(item, dict):
                continue
            value = item.get("value")
            qualifier_key = item.get("qualifierKey")
            if value is None:
                continue
            label = key.replace("Percentage", "").replace("Seconds", "").replace("_", " ").title()
            part = f"{label}: {value}"
            if qualifier_key:
                part += f" ({str(qualifier_key).replace('_', ' ').title()})"
            score_parts.append(part)
    if score_parts:
        lines.append("Sleep Score Details: " + " | ".join(score_parts[:8]))

    return "\n".join(lines)


def format_body_data(data: dict[str, Any]) -> str | None:
    """Format body, heart, battery, HRV, SpO2, and weight payloads."""
    summary = data.get("userSummary") if isinstance(data.get("userSummary"), dict) else None
    hr_data = data.get("heartRates") if isinstance(data.get("heartRates"), dict) else None
    body_battery = data.get("bodyBattery")
    hrv_data = data.get("hrv") if isinstance(data.get("hrv"), dict) else None
    spo2_data = data.get("spo2") if isinstance(data.get("spo2"), dict) else None
    weight_data = data.get("weight") if isinstance(data.get("weight"), dict) else None

    if not any([summary, hr_data, body_battery, hrv_data, spo2_data, weight_data]):
        return None

    steps = summary.get("totalSteps") if summary else None
    calories = summary.get("totalKilocalories") if summary else None
    header_parts = []
    if steps is not None:
        header_parts.append(f"{steps:,} steps")
    if calories is not None:
        header_parts.append(f"{int(calories):,} cal")

    header = "## Body"
    if header_parts:
        header += ": " + " | ".join(header_parts)
    lines = [header]

    if summary:
        detail_parts = []
        distance_m = summary.get("totalDistanceMeters")
        if distance_m is not None:
            detail_parts.append(f"Distance: {distance_m / 1000:.1f} km")
        floors = summary.get("floorsAscended")
        if floors is not None:
            detail_parts.append(f"Floors: {int(floors)}")
        active_cal = summary.get("activeKilocalories")
        if active_cal is not None:
            detail_parts.append(f"Active Calories: {int(active_cal)}")
        active_seconds = summary.get("activeSeconds")
        if active_seconds is not None:
            detail_parts.append(f"Active Time: {fmt_duration(active_seconds)}")
        if detail_parts:
            lines.append(" | ".join(detail_parts))

    if hr_data:
        hr_parts = []
        resting = hr_data.get("restingHeartRate")
        if resting:
            hr_parts.append(f"Resting HR: {resting} bpm")
        max_hr = hr_data.get("maxHeartRate")
        if max_hr:
            hr_parts.append(f"Max HR: {max_hr} bpm")
        series_summary = fmt_avg(values_from_pairs(hr_data.get("heartRateValues")))
        if series_summary:
            hr_parts.append(f"Daily HR: {series_summary}")
        if hr_parts:
            lines.append(" | ".join(hr_parts))

    battery_values = []
    if isinstance(body_battery, list):
        for item in body_battery:
            if isinstance(item, dict):
                for key in ("chargedValue", "drainedValue", "maxBodyBattery", "minBodyBattery"):
                    value = item.get(key)
                    if isinstance(value, (int, float)) and value > 0:
                        battery_values.append(float(value))
                battery_values.extend(values_from_pairs(item.get("bodyBatteryValuesArray")))
    extra_parts = []
    if battery_values:
        extra_parts.append(
            f"Body Battery: latest {round(battery_values[-1])} | low {round(min(battery_values))} | high {round(max(battery_values))}"
        )
    if hrv_data:
        hrv_summary = hrv_data.get("hrvSummary", {})
        if isinstance(hrv_summary, dict):
            hrv = hrv_summary.get("weeklyAvg") or hrv_summary.get("lastNightAvg")
            if hrv is not None:
                extra_parts.append(f"HRV: {hrv} ms")
    if extra_parts:
        lines.append(" | ".join(extra_parts))

    if spo2_data:
        spo2 = spo2_data.get("averageSpO2")
        lowest = spo2_data.get("lowestSpO2")
        if spo2 is not None:
            line = f"SpO2: {spo2}%"
            if lowest is not None:
                line += f" | Lowest: {lowest}%"
            lines.append(line)

    if weight_data:
        entries = weight_data.get("dateWeightList", [])
        if entries:
            grams = entries[0].get("weight")
            if grams:
                lines.append(f"Weight: {grams / 1000:.1f} kg")

    return "\n".join(lines)


def format_stress_data(data: dict[str, Any]) -> str | None:
    """Format stress data from a collected Garmin payload."""
    payload = data.get("stress")
    if not isinstance(payload, dict):
        return None
    avg = payload.get("overallStressLevel")
    if avg is None:
        return None
    if avg < 26:
        level = "Rest"
    elif avg < 51:
        level = "Low"
    elif avg < 76:
        level = "Medium"
    else:
        level = "High"
    details = []
    for key, label in [
        ("restStressDuration", "Rest"),
        ("lowStressDuration", "Low"),
        ("mediumStressDuration", "Medium"),
        ("highStressDuration", "High"),
    ]:
        value = payload.get(key)
        if isinstance(value, (int, float)) and value > 0:
            details.append(f"{label}: {fmt_duration(value)}")
    line = f"## Stress: Avg {avg} ({level})"
    if details:
        line += "\n" + " | ".join(details)
    return line


def format_training_readiness_data(data: dict[str, Any]) -> str | None:
    """Format training readiness from a collected Garmin payload."""
    payload = data.get("trainingReadiness")
    if not isinstance(payload, list) or not payload:
        return None
    entry = payload[0]
    score = entry.get("score") if isinstance(entry, dict) else None
    if score is None:
        return None
    level = entry.get("level", "").replace("_", " ").title()
    feedback = entry.get("feedbackShort", "").replace("_", " ").title()
    line = f"## Training Readiness: {score}"
    if level:
        line += f" ({level})"
    if feedback:
        line += f" — {feedback}"
    contributors = []
    for key, label in [
        ("sleepScore", "Sleep"),
        ("recoveryTime", "Recovery Time"),
        ("hrvStatus", "HRV Status"),
        ("acuteLoad", "Acute Load"),
        ("sleepHistory", "Sleep History"),
        ("stressHistory", "Stress History"),
    ]:
        value = entry.get(key)
        if value is not None:
            contributors.append(f"{label}: {value}")
    if contributors:
        line += "\nReadiness Factors: " + " | ".join(contributors[:8])
    return line


def format_respiration_data(data: dict[str, Any]) -> str | None:
    """Format respiration from a collected Garmin payload."""
    payload = data.get("respiration")
    if not isinstance(payload, dict):
        return None
    parts = []
    avg_waking = payload.get("avgWakingRespirationValue")
    if avg_waking:
        parts.append(f"Waking: {avg_waking:.0f} brpm")
    avg_sleeping = payload.get("avgSleepRespirationValue")
    if avg_sleeping:
        parts.append(f"Sleeping: {avg_sleeping:.0f} brpm")
    lowest = payload.get("lowestRespirationValue")
    highest = payload.get("highestRespirationValue")
    if lowest and highest:
        parts.append(f"Range: {lowest:.0f}–{highest:.0f}")
    if not parts:
        return None
    return "## Respiration: " + " | ".join(parts)


def format_fitness_age_data(data: dict[str, Any]) -> str | None:
    """Format fitness age from a collected Garmin payload."""
    payload = data.get("fitnessAge")
    if not isinstance(payload, dict):
        return None
    fitness_age = payload.get("fitnessAge")
    chrono_age = payload.get("chronologicalAge")
    if fitness_age is None:
        return None
    line = f"## Fitness Age: {int(fitness_age)}"
    if chrono_age is not None:
        diff = int(fitness_age) - chrono_age
        if diff < 0:
            line += f" ({abs(diff)} years younger)"
        elif diff > 0:
            line += f" ({diff} years older)"
    return line


def format_intensity_minutes_data(data: dict[str, Any]) -> str | None:
    """Format weekly intensity minutes from a collected Garmin payload."""
    payload = data.get("intensityMinutes")
    if not isinstance(payload, dict):
        return None
    moderate = payload.get("weeklyModerate")
    vigorous = payload.get("weeklyVigorous")
    total = payload.get("weeklyTotal")
    goal = payload.get("weekGoal")
    if total is None:
        return None
    parts = [f"## Intensity Minutes: {total} weekly"]
    detail = []
    if moderate is not None:
        detail.append(f"Moderate: {moderate}")
    if vigorous is not None:
        detail.append(f"Vigorous: {vigorous}")
    if goal is not None:
        detail.append(f"Goal: {goal}")
    if detail:
        parts.append(" | ".join(detail))
    return "\n".join(parts)


def format_activities_data(data: dict[str, Any]) -> str | None:
    """Format activities from a collected Garmin payload."""
    activities = data.get("activities")
    if not isinstance(activities, list) or not activities:
        return None
    lines = ["## Activities"]
    for act in activities:
        if not isinstance(act, dict):
            continue
        name = act.get("activityName", "Activity")
        duration = fmt_duration_mmss(act.get("duration"))
        header_parts = [f"**{name}** — {duration}"]
        distance = act.get("distance")
        if distance and distance > 0:
            header_parts.append(f"{distance / 1000:.1f} km")
        calories = act.get("calories")
        if calories and calories > 0:
            header_parts.append(f"{int(calories)} cal")
        lines.append("- " + ", ".join(header_parts))

        details = []
        avg_hr = act.get("averageHR")
        max_hr = act.get("maxHR")
        if avg_hr and avg_hr > 0:
            hr_str = f"Avg HR {int(avg_hr)}"
            if max_hr and max_hr > 0:
                hr_str += f" / Max {int(max_hr)}"
            details.append(hr_str)
        elev = act.get("elevationGain")
        if elev and elev > 0:
            details.append(f"Elevation: +{int(elev)}m")
        avg_speed = act.get("averageSpeed")
        if avg_speed and avg_speed > 0 and distance and distance > 0:
            pace_sec = 1000 / avg_speed
            details.append(f"Pace: {int(pace_sec) // 60}:{int(pace_sec) % 60:02d}/km")
        cadence = act.get("averageRunningCadenceInStepsPerMinute")
        if cadence and cadence > 0:
            details.append(f"Cadence: {int(cadence)} spm")
        avg_power = act.get("avgPower")
        if avg_power and avg_power > 0:
            power_str = f"Power: {int(avg_power)}W"
            max_power = act.get("maxPower")
            if max_power and max_power > 0:
                power_str += f" / Max {int(max_power)}W"
            details.append(power_str)
        aero_te = act.get("aerobicTrainingEffect")
        anaero_te = act.get("anaerobicTrainingEffect")
        if aero_te and aero_te > 0:
            te_str = f"Training Effect: {aero_te:.1f} aerobic"
            if anaero_te and anaero_te > 0:
                te_str += f" / {anaero_te:.1f} anaerobic"
            details.append(te_str)
        vo2 = act.get("vO2MaxValue")
        if vo2 and vo2 > 0:
            details.append(f"VO2 Max: {int(vo2)}")
        if details:
            lines.append("  " + " | ".join(details))
    return "\n".join(lines)


def format_collected_day(data: dict[str, Any]) -> list[str]:
    """Format all collected Garmin payloads into markdown sections."""
    sections = []
    for formatter in [
        format_sleep_data,
        format_body_data,
        format_stress_data,
        format_training_readiness_data,
        format_respiration_data,
        format_fitness_age_data,
        format_intensity_minutes_data,
        format_activities_data,
    ]:
        section = formatter(data)
        if section:
            sections.append(section)
    return sections


def build_health_profile(output_dir: Path) -> None:
    """Refresh long-term profile files after sync."""
    script = BASE_DIR / "scripts" / "build_health_profile.py"
    if not script.exists():
        return
    proc = subprocess.run(
        [sys.executable, str(script), "--health-dir", str(output_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    if VERBOSE and proc.stdout:
        print(proc.stdout.strip(), file=sys.stderr)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        print(f"Warning: health profile build failed: {err}", file=sys.stderr)


def sync_day(client: Garmin, day: date, output_dir: Path, write_raw_json: bool = False) -> None:
    """Sync a single day's data and write the markdown file."""
    day_str = day.isoformat()
    display_date = day.strftime("%B %-d, %Y")
    if VERBOSE:
        print(f"  {day_str}: Fetching Garmin API data...")

    sections = [f"# Health — {display_date}"]
    data, errors = collect_daily_data(client, day_str)
    sections.extend(format_collected_day(data))

    if len(sections) == 1:
        print(f"  {day_str}: No data available, skipping.")
        if write_raw_json and (data or errors):
            write_private_json(
                output_dir / "raw" / f"{day_str}.json",
                {"date": day_str, "fetchedAt": datetime.now().isoformat(timespec="seconds"), "data": data, "errors": errors},
            )
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{day_str}.md"
    output_file.write_text("\n\n".join(sections) + "\n")
    if write_raw_json:
        write_private_json(
            output_dir / "raw" / f"{day_str}.json",
            {"date": day_str, "fetchedAt": datetime.now().isoformat(timespec="seconds"), "data": data, "errors": errors},
        )
    print(f"  {day_str}: Written to {output_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Garmin Connect health data to markdown.")
    parser.add_argument("--setup", action="store_true", help="One-time setup: authenticate and cache tokens.")
    parser.add_argument("--email", type=str, help="Garmin Connect email (used with --setup).")
    parser.add_argument("--date", type=str, help="Specific date to sync (YYYY-MM-DD). Default: today.")
    parser.add_argument("--days", type=int, help="Sync the last N days.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed error info for failed data fetches.")
    parser.add_argument(
        "--cn",
        action="store_true",
        help="Use Garmin China endpoints (garmin.cn). Can also be enabled with GARMIN_CONNECT_CN=1.",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help=(
            "Sync from the user's currently logged-in Chrome Garmin Connect web UI "
            "using AppleScript instead of cached API tokens."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="health",
        help="Output directory for markdown files (relative to skill base dir).",
    )
    parser.add_argument(
        "--password-source",
        choices=["prompt", "keychain"],
        default=os.environ.get("GARMIN_CONNECT_PASSWORD_SOURCE", "prompt"),
        help=(
            "Password source for --setup. Use 'prompt' for an interactive "
            "getpass prompt or 'keychain' to read macOS Keychain."
        ),
    )
    parser.add_argument(
        "--raw-json",
        action="store_true",
        default=env_flag("GARMIN_CONNECT_RAW_JSON"),
        help="Write private raw Garmin API payloads to health/raw/YYYY-MM-DD.json.",
    )
    parser.add_argument(
        "--api-timeout",
        type=int,
        default=int(os.environ.get("GARMIN_CONNECT_API_TIMEOUT", "30")),
        help="Per-request Garmin API timeout in seconds.",
    )
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help="Skip rebuilding health/profile.md and health/metrics.json after sync.",
    )
    args = parser.parse_args()

    global VERBOSE
    global API_TIMEOUT
    VERBOSE = args.verbose
    API_TIMEOUT = args.api_timeout
    is_cn = args.cn or env_flag("GARMIN_CONNECT_CN")
    use_browser = args.browser or env_flag("GARMIN_CONNECT_BROWSER")

    if args.setup:
        if not args.email:
            print("Error: --email is required with --setup.", file=sys.stderr)
            sys.exit(1)
        setup(args.email, is_cn=is_cn, password_source=args.password_source)
        return

    # Always resolve output-dir relative to the skill's base directory, not CWD
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir

    # Determine which days to sync
    if args.days:
        today = date.today()
        days = [today - timedelta(days=i) for i in range(args.days)]
    elif args.date:
        try:
            days = [date.fromisoformat(args.date)]
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)
    else:
        days = [date.today()]

    if use_browser:
        print("Syncing Garmin Connect from Chrome browser session...")
        sync_days_from_browser(days, output_dir, is_cn=is_cn)
        if not args.no_profile:
            build_health_profile(output_dir)
        print("Done.")
        return

    client = authenticate(is_cn=is_cn)
    print("Authenticated with Garmin Connect.")
    print(f"Syncing {len(days)} day(s)...")

    for day in sorted(days):
        sync_day(client, day, output_dir, write_raw_json=args.raw_json)

    if not args.no_profile:
        build_health_profile(output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
