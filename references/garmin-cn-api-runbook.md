# Garmin CN API Runbook

Use this runbook when maintaining the local Garmin Health OpenClaw agent, repairing Garmin authentication, or explaining how the Garmin CN API path works.

## Local Source Of Truth

- Skill directory: `/Users/liyu/.openclaw/skills/garmin-connect`
- Sync script: `/Users/liyu/.openclaw/skills/garmin-connect/scripts/sync_garmin.py`
- Health markdown output: `/Users/liyu/.openclaw/skills/garmin-connect/health/YYYY-MM-DD.md`
- Long-term profile: `/Users/liyu/.openclaw/skills/garmin-connect/health/profile.md`
- Machine metrics: `/Users/liyu/.openclaw/skills/garmin-connect/health/metrics.json`
- Optional raw API snapshots: `/Users/liyu/.openclaw/skills/garmin-connect/health/raw/YYYY-MM-DD.json`
- Garmin CN web-session cache: `/Users/liyu/.garminconnect/garmin_cn_web_session.json`
- macOS Keychain service: `openclaw.garmin-connect.cn`
- macOS Keychain account: the Garmin account email

The password itself must not be written to this file, `SKILL.md`, OpenClaw config, cron payloads, shell scripts, or command-line arguments. Store or refresh it with macOS Keychain only.

## Credential Setup

Store the password in macOS Keychain:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
python3 scripts/garmin_keychain.py put --account you@example.com --cn
```

Check that the password exists without printing it:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
python3 scripts/garmin_keychain.py status --account you@example.com --cn
```

Refresh the Garmin CN web-session API cache from Keychain:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --setup --email you@example.com --cn --password-source keychain
```

Expected success:

```text
Success! Garmin CN web-session API cache saved in /Users/liyu/.garminconnect/garmin_cn_web_session.json
You can now run the sync command without credentials.
```

The cache file should remain private:

```bash
ls -ld /Users/liyu/.garminconnect
ls -l /Users/liyu/.garminconnect/garmin_cn_web_session.json
```

Expected permissions are `drwx------` for the directory and `-rw-------` for the cache file.

## CN API Flow

Garmin China currently rejects the normal DI bearer-token exchange used by the global Garmin Connect flow. The working local path is a signed-in Garmin CN web session plus Garmin CN `/gc-api` JSON endpoints:

1. `--setup --cn` logs in through Garmin's portal flow for `garmin.cn`.
2. The script opens `https://connect.garmin.cn/modern/` through the authenticated session.
3. It parses `window.VIEWER_SOCIAL_PROFILE`, `window.VIEWER_USERPREFERENCES`, `window.SESSION_EXPIRES`, and the page CSRF token.
4. It writes CN cookies, CSRF token, and safe profile metadata into `/Users/liyu/.garminconnect/garmin_cn_web_session.json` with mode `0600`.
5. Normal syncs instantiate `GarminCnWebClient`, refresh `https://connect.garmin.cn/modern/`, and call `https://connect.garmin.cn/gc-api/...` endpoints with `Connect-Csrf-Token`.
6. If the API returns `401` or `403`, the client refreshes the web session once and retries the same API call.

Core endpoints used by the script:

- `/wellness-service/wellness/dailySleepData/{displayName}`
- `/usersummary-service/usersummary/daily/{displayName}`
- `/wellness-service/wellness/dailyHeartRate/{displayName}`
- `/wellness-service/wellness/bodyBattery/reports/daily`
- `/hrv-service/hrv/{date}`
- `/wellness-service/wellness/daily/spo2/{date}`
- `/weight-service/weight/dayview/{date}`
- `/wellness-service/wellness/dailyStress/{date}`
- `/metrics-service/metrics/trainingreadiness/{date}`
- `/wellness-service/wellness/daily/respiration/{date}`
- `/fitnessage-service/fitnessage/{date}`
- `/wellness-service/wellness/daily/im/{date}`
- `/activitylist-service/activities/search/activities`

This is API JSON data. The Chrome/AppleScript `--browser` path is only a manual fallback when Garmin's session/API path is blocked or the owner explicitly requests browser sync.

## Sync Commands

Sync today through Garmin CN API:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --cn --verbose
```

Sync one specific date:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --cn --date 2026-05-16 --verbose
```

Sync the last three days:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --cn --days 3 --verbose
```

Sync a richer recent history and refresh long-term profile files:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --cn --days 30 --verbose --raw-json
```

Backfill longer history for trend management:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --cn --days 180 --verbose --raw-json
```

For a full-year baseline, use `--days 365` if Garmin rate limits are not
triggered. If the backfill is interrupted, rerun the same command; existing
daily files are overwritten with fresh API data.

Expected success contains:

```text
Authenticated with Garmin Connect.
Syncing N day(s)...
YYYY-MM-DD: Written to /Users/liyu/.openclaw/skills/garmin-connect/health/YYYY-MM-DD.md
Wrote /Users/liyu/.openclaw/skills/garmin-connect/health/profile.md
Wrote /Users/liyu/.openclaw/skills/garmin-connect/health/metrics.json
Done.
```

Health management reading order for the Garmin agent:

1. Read `health/profile.md` for coverage, rolling windows, trend deltas, and watchlist.
2. Read `health/metrics.json` for exact time-series metrics when comparing windows.
3. Read specific `health/YYYY-MM-DD.md` files for daily summaries.
4. Inspect `health/raw/YYYY-MM-DD.json` only when the markdown omits a needed Garmin field.

## OpenClaw Cron Integration

The Garmin cron jobs should run as `agentId=garmin` with `toolsAllow=["exec"]`. The Garmin agent must keep a `full` tool profile because the cron task needs `exec`.

Current local jobs:

- `61db6bed-f7ed-464a-bc58-0bc6330e97cb` — `Garmin daily health sync`, 07:00 Asia/Shanghai, syncs recent data with `--raw-json`, refreshes long-term profile, no Feishu delivery.
- `f35c6610-f44e-4d28-aeab-2f9216671a09` — `Garmin nightly analysis`, 22:00 Asia/Shanghai, syncs a recent window, reads `health/profile.md`, and sends the report to Feishu using the `garmin` account.

Verify latest runs:

```bash
openclaw cron runs --id 61db6bed-f7ed-464a-bc58-0bc6330e97cb --limit 2
openclaw cron runs --id f35c6610-f44e-4d28-aeab-2f9216671a09 --limit 2
```

Expected:

- daily sync: latest `status=ok`, summary includes `GARMIN_SYNC_OK`.
- nightly analysis: latest `status=ok`, `delivered=true`, `deliveryStatus=delivered`, `resolvedAccountId=garmin`.

## Repair Checklist

1. Validate OpenClaw config:

```bash
openclaw config validate --json
```

2. Confirm Feishu and Garmin account are running:

```bash
openclaw channels status --json
```

3. Confirm Garmin cache exists and is private:

```bash
ls -ld /Users/liyu/.garminconnect
ls -l /Users/liyu/.garminconnect/garmin_cn_web_session.json
```

4. If sync returns `403`, refresh auth through Keychain:

```bash
cd /Users/liyu/.openclaw/skills/garmin-connect
uv run scripts/sync_garmin.py --setup --email you@example.com --cn --password-source keychain
```

5. Re-run API sync and only use `--browser` if API refresh still fails and the owner explicitly approves browser fallback.
