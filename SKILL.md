---
name: garmin-pulse
version: 1.3.1
description: Syncs daily health and fitness data from Garmin Connect into markdown files. Provides sleep, activity, heart rate, stress, body battery, HRV, SpO2, and weight data.
homepage: https://github.com/freakyflow/garminskill
metadata: {"openclaw":{"emoji":"💪","requires":{"bins":["uv"]},"install":[{"id":"uv","kind":"brew","formula":"uv","bins":["uv"],"label":"Install uv via Homebrew"}]}}
---

# Garmin Connect

This skill syncs your daily health data from Garmin Connect into readable markdown files.

## Setup

Authentication is required before the first sync. This only needs to happen once — tokens are cached for approximately one year.

If the sync command fails with "No cached tokens found", tell the user to run the setup command in their terminal:

```bash
uv run {baseDir}/scripts/sync_garmin.py --setup --email you@example.com
```

For Garmin China accounts, add `--cn` so the script uses `garmin.cn` endpoints:

```bash
uv run {baseDir}/scripts/sync_garmin.py --setup --email you@example.com --cn
```

If Garmin China blocks the API token flow, use the browser path instead. It
uses the user's logged-in Chrome Garmin Connect page via AppleScript and does
not need a password after the user is logged in to Chrome:

```bash
uv run {baseDir}/scripts/sync_garmin.py --cn --browser
```

The password is prompted interactively via `getpass` — it is never echoed to screen, stored in shell history, or passed as a command argument. On success the user will see `Success! Tokens cached in ~/.garminconnect`. After that, all syncs use cached tokens only — no credentials are needed.

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

For Garmin China accounts where browser sync is configured:

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

## Reading Health Data

Health files are stored at `{baseDir}/health/YYYY-MM-DD.md` — one file per day.

To answer health or fitness questions, read the relevant date's file from the `{baseDir}/health/` directory. If the file doesn't exist for the requested date, run the sync command for that date first.

## Dependencies

This skill uses [uv](https://docs.astral.sh/uv/) to run the sync script. `uv` is a fast Python package manager by Astral that reads inline script metadata (PEP 723) and automatically installs `garminconnect` in an isolated environment — no manual `pip install` needed.

## Credentials & Stored Data

Garmin Connect does not offer a public OAuth API, so a one-time email/password login is required. During setup, the password is used once to obtain OAuth tokens, then discarded. The tokens are cached locally in `~/.garminconnect/` for approximately one year. At runtime, only the cached tokens are used — no email or password is needed. If tokens expire, re-run the setup command.

For Garmin China browser sync, the skill reads the currently logged-in Chrome
Garmin Connect web UI via AppleScript. It does not read or copy the Chrome
profile and does not require a password on the command line.

**Paths written by this skill:**

- `~/.garminconnect/` — cached OAuth tokens (sensitive; grants access to the user's Garmin account)
- `{baseDir}/health/` — daily health markdown files (contains personal health data)

## Cron Setup

Schedule the sync script to run every morning using OpenClaw's `cron` tool so your health data stays up to date automatically. No environment variables or credentials are needed — the sync uses cached tokens from the one-time setup.
