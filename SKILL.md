---
name: garmin-pulse
version: 1.5.0
description: Use when the user asks about Garmin Connect, Garmin health data, sleep, activities, running, heart rate, stress, body battery, HRV, SpO2, weight, long-term health trends, or whether Garmin data is connected. Syncs daily health and fitness data into markdown files and maintains a long-term local health profile.
homepage: https://github.com/freakyflow/garminskill
metadata: {"openclaw":{"emoji":"💪","requires":{"bins":["uv"]},"install":[{"id":"uv","kind":"brew","formula":"uv","bins":["uv"],"label":"Install uv via Homebrew"}]}}
---

# Garmin Connect

This skill syncs your daily health data from Garmin Connect into readable markdown files.

## Setup

Authentication is required before the first sync. Global Garmin accounts cache DI tokens; Garmin China accounts cache signed-in web-session material for the `/gc-api` JSON API.

For this local OpenClaw deployment, read `references/garmin-cn-api-runbook.md`
when you need the exact Garmin CN API flow, Keychain credential setup,
verification commands, or cron repair steps.

If the sync command fails with "No cached tokens found", tell the user to run the setup command in their terminal:

```bash
uv run {baseDir}/scripts/sync_garmin.py --setup --email you@example.com
```

For Garmin China accounts, add `--cn` so the script logs in through Garmin CN
once and caches local web-session auth for `https://connect.garmin.cn/gc-api/...`:

```bash
uv run {baseDir}/scripts/sync_garmin.py --setup --email you@example.com --cn
```

For a configured Garmin China account, the password should live in macOS
Keychain under service `openclaw.garmin-connect.cn` and the account's email
address. Do not write the password into this skill, chat, OpenClaw config,
cron payloads, or shell history. Store/check it with:

```bash
python3 {baseDir}/scripts/garmin_keychain.py put --account you@example.com --cn
python3 {baseDir}/scripts/garmin_keychain.py status --account you@example.com --cn
```

When the Keychain password exists, setup can refresh the Garmin CN API session
without an interactive password prompt:

```bash
uv run {baseDir}/scripts/sync_garmin.py --setup --email you@example.com --cn --password-source keychain
```

The normal sync path is API JSON: global accounts use the Garmin Connect token
path; Garmin China uses the cached CN web-session `/gc-api` path. Only use the
browser path as a manual fallback when the API path is blocked or the user
explicitly asks for browser sync. The browser fallback reads the user's
logged-in Chrome Garmin Connect page via AppleScript and does not need a
password after the user is logged in to Chrome:

```bash
uv run {baseDir}/scripts/sync_garmin.py --cn --browser
```

When using `--password-source prompt`, the password is prompted interactively via
`getpass` — it is never echoed to screen, stored in shell history, or passed as
a command argument. When using `--password-source keychain`, the script reads
macOS Keychain without printing the password. On success the user will see
either `Success! Tokens cached in ~/.garminconnect` for global accounts or
`Success! Garmin CN web-session API cache saved...` for Garmin China. After
that, normal syncs use cached local auth only — no credentials are needed.

Do not ask the user for their password in chat and do not pass passwords as command-line arguments or via stdin piping, as these methods can expose credentials in process listings or conversation history.

## Syncing Data

Sync today's data:

```bash
uv run {baseDir}/scripts/sync_garmin.py
```

For Garmin China accounts, keep using `--cn` on syncs too:

```bash
uv run {baseDir}/scripts/sync_garmin.py --cn
```

Manual browser fallback for Garmin China accounts when the API token flow is
blocked:

```bash
uv run {baseDir}/scripts/sync_garmin.py --cn --browser
```

Sync a specific date:

```bash
uv run {baseDir}/scripts/sync_garmin.py --date 2026-02-07
```

Sync the last N days:

```bash
uv run {baseDir}/scripts/sync_garmin.py --days 7
```

Sync richer local data for deeper health management:

```bash
uv run {baseDir}/scripts/sync_garmin.py --cn --days 30 --raw-json
```

For scheduled Garmin China syncs, prefer a robust API command that refreshes
the CN web-session from macOS Keychain and retries once if the cached session is
expired:

```bash
cd {baseDir} && (/usr/local/bin/uv run scripts/sync_garmin.py --cn --days 45 --verbose --raw-json --api-timeout 45 || (/usr/local/bin/uv run scripts/sync_garmin.py --setup --email you@example.com --cn --password-source keychain && /usr/local/bin/uv run scripts/sync_garmin.py --cn --days 45 --verbose --raw-json --api-timeout 45))
```

`--raw-json` writes private daily API snapshots under
`{baseDir}/health/raw/YYYY-MM-DD.json`; use it for recent days or backfills
where detailed review may matter. Normal syncs also refresh the long-term
profile files unless `--no-profile` is passed.

## Reading Health Data

Health files are stored at `{baseDir}/health/YYYY-MM-DD.md` — one file per day.

For long-term health management, read in this order:

1. `{baseDir}/health/profile.md` — compact longitudinal profile and watchlist.
2. `{baseDir}/health/metrics.json` — machine-readable daily metrics and rolling windows.
3. `{baseDir}/health/YYYY-MM-DD.md` — readable daily summary for specific dates.
4. `{baseDir}/health/raw/YYYY-MM-DD.json` — raw Garmin API payloads when deeper inspection is needed.

If a requested date is missing, run the sync command for that date first. If the
question is about baseline, trend, recovery, or health management over time,
refresh a larger window first, such as `--days 30`, `--days 90`, or `--days 365`.
On this local setup, the desired Garmin management baseline is two years through
today when available. Reports should compare the latest day against 7-day,
30-day, 90-day, 365-day, and all-available rolling windows rather than using
only yesterday or a single recent week.

For nightly reports and health-management answers, structure the interpretation
by time horizon:

- Latest day: what changed today and whether the data is fresh.
- 7-day: acute sleep/recovery/training load.
- 30-day: recent habit and recovery direction.
- 90-day: medium-term training and lifestyle trend.
- 365-day: seasonal baseline.
- All available data, normally about 730 days: long-term baseline and risk
  pattern.

## Dependencies

This skill uses [uv](https://docs.astral.sh/uv/) to run the sync script. `uv` is a fast Python package manager by Astral that reads inline script metadata (PEP 723) and automatically installs `garminconnect` in an isolated environment — no manual `pip install` needed.

## Credentials & Stored Data

Garmin Connect does not offer a public OAuth API, so a one-time email/password login is required. During setup, the password is used once to obtain local auth material, then discarded. Global tokens and Garmin China web-session cookies are cached locally in `~/.garminconnect/`. At runtime, only cached auth is used — no email or password is needed. If auth expires, re-run the setup command.

For Garmin China API sync, the skill calls `https://connect.garmin.cn/gc-api/...`
with the cached web session and CSRF token. For Garmin China browser sync, the
skill reads the currently logged-in Chrome Garmin Connect web UI via
AppleScript. It does not read or copy the Chrome profile and does not require a
password on the command line.

**Paths written by this skill:**

- `~/.garminconnect/` — cached OAuth tokens or Garmin China web-session cookies (sensitive; grants access to the user's Garmin account)
- `{baseDir}/health/` — daily health markdown files, long-term profile, metrics JSON, and optional raw API snapshots (contains personal health data)

## Cron Setup

Schedule the sync script to run every morning using OpenClaw's `cron` tool so your health data stays up to date automatically. No environment variables or credentials are needed — the sync uses cached tokens from the one-time setup. For Garmin China, the cron command should normally use `--cn --raw-json` for richer daily records and should retry once with `--setup --password-source keychain` when the cached CN web session expires or returns `401`/`403`. Keep `--browser` as a manual fallback, not the default scheduled path. The nightly analysis job should read `health/profile.md` and `health/metrics.json` before daily files so feedback stays longitudinal and segmented by latest day, 7d, 30d, 90d, 365d, and all available history.
