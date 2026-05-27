# Garmin Connect — OpenClaw Skill

An [OpenClaw](https://openclaw.ai) skill that syncs your daily health data from Garmin Connect into markdown files. OpenClaw can then reference your health and fitness data in conversation.

## What it syncs

- **Sleep** — duration, stages (deep/light/REM/awake), sleep score
- **Body** — steps, calories, distance, floors
- **Heart** — resting HR, max HR, HRV
- **Body Battery & SpO2**
- **Stress** — average level
- **Training Readiness** — score and level
- **Respiration** — waking and sleeping breathing rate
- **Fitness Age**
- **Intensity Minutes** — weekly moderate/vigorous totals
- **Weight** — if recorded
- **Activities** — name, duration, distance, calories, HR, elevation, pace, cadence, power, training effect, VO2 max

## Example output

```markdown
# Health — January 26, 2026

## Sleep: 8h 39m (Good)
Deep: 1h 50m | Light: 4h 30m | REM: 2h 19m | Awake: 0h 54m
Sleep Score: 85

## Body: 9,720 steps | 2,317 cal
Distance: 8.0 km | Floors: 42
Resting HR: 37 bpm | Max HR: 111 bpm
HRV: 68 ms
SpO2: 94.0%

## Training Readiness: 100 (Prime) — Ready To Go

## Respiration: Waking: 12 brpm | Sleeping: 13 brpm | Range: 5–20

## Fitness Age: 33 (6 years younger)

## Intensity Minutes: 385 weekly
Moderate: 69 | Vigorous: 158 | Goal: 150

## Activities
- **5K Run** — 28:15, 5.0 km, 320 cal
  Avg HR 155 / Max 172 | Elevation: +45m | Pace: 5:39/km | Cadence: 168 spm | Training Effect: 3.2 aerobic | VO2 Max: 50
```

Sections are only included when data is available.

## Setup

### Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (no pip install needed — dependencies are inline)
  - macOS: `brew install uv`
  - Linux/WSL: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- A Garmin Connect account (two-factor authentication must be disabled — see [Troubleshooting](#troubleshooting))

### One-time setup

Authenticate and cache local session material. The password is prompted interactively via `getpass` — never echoed to screen or stored in shell history.

```bash
uv run scripts/sync_garmin.py --setup --email you@example.com
```

For Garmin China accounts, use the China web-session API cache:

```bash
uv run scripts/sync_garmin.py --setup --email you@example.com --cn
```

On this local OpenClaw setup, the configured Garmin China account can also read
the setup password from macOS Keychain. Store or refresh the password without
printing it:

```bash
python3 scripts/garmin_keychain.py put --account you@example.com --cn
python3 scripts/garmin_keychain.py status --account you@example.com --cn
```

Then refresh the Garmin CN API session from Keychain:

```bash
uv run scripts/sync_garmin.py --setup --email you@example.com --cn --password-source keychain
```

The plaintext password must not be committed into this skill, OpenClaw config,
cron payloads, or shell history. See
`references/garmin-cn-api-runbook.md` for the full local CN API runbook.

For Garmin China, Garmin currently rejects the non-official DI bearer-token
exchange. The `--cn` setup logs in through Garmin's portal flow once, caches the
signed-in CN web-session cookies under `~/.garminconnect/`, and subsequent syncs
read JSON from `https://connect.garmin.cn/gc-api/...`. This is API data, not a
visible-page scrape.

Use the browser path only as a manual fallback. It reads the user's already
logged-in Chrome Garmin Connect page via AppleScript and does not require saving
a password or Chrome profile copy:

```bash
uv run scripts/sync_garmin.py --cn --browser
```

After setup succeeds, the password is no longer needed for normal syncs. Global
Garmin uses cached DI tokens; Garmin China uses the cached CN web session and
refreshes it through Garmin SSO cookies when possible.

### Run it

```bash
# Sync today (no credentials needed — uses cached tokens)
uv run scripts/sync_garmin.py

# Sync today using Garmin China endpoints
uv run scripts/sync_garmin.py --cn

# Sync richer local data and rebuild the long-term profile
uv run scripts/sync_garmin.py --cn --days 30 --raw-json

# Manual fallback: sync today from the logged-in Chrome Garmin Connect web UI
uv run scripts/sync_garmin.py --cn --browser

# Sync a specific date
uv run scripts/sync_garmin.py --date 2025-01-26

# Sync the last 7 days
uv run scripts/sync_garmin.py --days 7

# Custom output directory (default: health/)
uv run scripts/sync_garmin.py --output-dir my-data
```

Markdown files are written to `health/YYYY-MM-DD.md` by default (relative to the skill's base directory).

Long-term profile files are rebuilt after sync:

- `health/profile.md` — compact longitudinal health profile, rolling-window table, trend deltas, and watchlist.
- `health/metrics.json` — machine-readable daily metrics and rolling-window summaries.
- `health/raw/YYYY-MM-DD.json` — optional private raw API snapshots when `--raw-json` is used.

Use `--no-profile` only when you intentionally want to skip profile rebuilds.

### Running report context

For daily health reports, especially after a run, build a compact running
context from the local health files:

```bash
python3 scripts/build_running_context.py --days 90 --pretty
```

The helper reads `health/metrics.json` and `health/raw/*.json` and returns:

- the latest day's running activities and the main run's distance, duration,
  pace, average/max HR, cadence, power, training effect, VO2 max, and intensity
  class;
- calendar-week, rolling-7-day, calendar-month, and rolling-30-day mileage;
- comparisons against the prior 5 runs, last-30-day hard workouts, and
  last-30-day normal/easy workouts;
- same-day recovery context from sleep score, HRV, resting HR, Body Battery,
  and Training Readiness.

Use this output in the Garmin nightly report before writing subjective training
feedback. If the latest day has no run, use the volume fields only as load
context.

### Install as an OpenClaw skill

```bash
ln -s /path/to/garminskill ~/.openclaw/skills/garmin-connect
```

### Cron

Schedule the API sync to run every morning so your data stays up to date automatically. No credentials needed — the sync uses cached auth material from the one-time setup. For Garmin China, include `--cn --raw-json`; do not include `--browser` in the scheduled command unless you intentionally want the browser fallback. OpenClaw's `cron` tool can handle this, or use a system crontab:

```bash
0 7 * * * uv run /path/to/garminskill/scripts/sync_garmin.py --cn --raw-json
```

## Troubleshooting

### "No profile from connectapi"

This is the most common setup error. It usually means Garmin's servers are temporarily rate-limiting or blocking the request (via Cloudflare). It does **not** necessarily mean your password is wrong.

1. **Wait a few minutes and try again.** This resolves it most of the time.
2. **Double-check your password.** The error can also appear for wrong credentials — Garmin doesn't always return a clear "wrong password" message.
3. **Check if Garmin Connect is down.** Try logging in at [connect.garmin.com](https://connect.garmin.com) in a browser.

### Two-factor authentication (2FA)

If your Garmin account has 2FA enabled, authentication will fail. The `garminconnect` library does not support 2FA/MFA flows. You'll need to disable 2FA on your Garmin account to use this skill:

1. Log in to [connect.garmin.com](https://connect.garmin.com)
2. Go to Account Settings → Security
3. Disable two-step verification
4. Re-run setup: `uv run scripts/sync_garmin.py --setup --email you@example.com`

### Cloudflare / random auth failures

Garmin periodically updates its anti-bot measures, which can cause temporary breakdowns. If authentication suddenly stops working after a period of stability:

1. **Update dependencies:** `uv cache clean` then re-run the sync (uv will fetch the latest versions automatically)
2. **Wait and retry.** Cloudflare blocks are often transient.
3. **Check the [garminconnect issues page](https://github.com/cyberjunky/python-garminconnect/issues)** — others may be experiencing the same problem.
4. For Garmin China, the expected API command is `--cn`. It uses `/gc-api` JSON endpoints with the cached CN web session. Use `--cn --browser` only as a manual fallback if Chrome is already logged in and the web-session API path fails.

### Tokens expired

Cached global DI tokens last about a year. Garmin China uses a cached web session: the long-lived SSO cookies may refresh the short-lived `JWT_WEB` session, but if Garmin revokes or expires the session, the sync will tell you to re-run setup. Just run the setup command again with your email — a new password prompt will appear and fresh local auth material will be cached.

## Auth notes

The script uses [garminconnect](https://github.com/cyberjunky/python-garminconnect)
for global token-based sync. For Garmin China it uses Garmin's signed-in
`/gc-api` JSON endpoints with locally cached CN web-session cookies. It can also
use Chrome AppleScript as a Garmin China fallback, but that is not the default
source of truth. Authentication is split into two phases:

1. **Setup** (`--setup`): Run once in a terminal to authenticate. `getpass` prompts for the password (never echoed to screen or stored in shell history). Global OAuth tokens or Garmin China web-session cookies are cached in `~/.garminconnect/`. The password is used once and then discarded.
2. **Keychain setup** (`--password-source keychain`): Reads the password from
   macOS Keychain service `openclaw.garmin-connect.cn` and the account email;
   it does not print the password.
3. **Sync** (default): Uses cached auth only — no credentials needed. Global token refresh is automatic where Garmin permits it; Garmin China refreshes the signed-in web session where Garmin SSO permits it. If auth expires or is revoked by Garmin, re-run setup.
4. **Browser sync** (`--browser`): Reads the logged-in Garmin Connect web UI in Chrome and writes the same daily markdown file. This is a fallback for Garmin China accounts when the non-official API token exchange is blocked, not the normal scheduled path.
