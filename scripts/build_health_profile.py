#!/usr/bin/env python3
"""Build a long-term Garmin health profile from synced daily markdown files."""

import argparse
import contextlib
import json
import os
import re
import statistics
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


METRIC_FIELDS = [
    "sleep_minutes",
    "sleep_score",
    "deep_minutes",
    "light_minutes",
    "rem_minutes",
    "awake_minutes",
    "steps",
    "calories",
    "distance_km",
    "floors",
    "resting_hr",
    "max_hr",
    "hrv",
    "body_battery",
    "stress_avg",
    "training_readiness",
    "fitness_age",
    "intensity_total",
    "activity_count",
]


@dataclass
class DailyRecord:
    date: str
    sleep_minutes: float | None = None
    sleep_score: float | None = None
    deep_minutes: float | None = None
    light_minutes: float | None = None
    rem_minutes: float | None = None
    awake_minutes: float | None = None
    steps: float | None = None
    calories: float | None = None
    distance_km: float | None = None
    floors: float | None = None
    resting_hr: float | None = None
    max_hr: float | None = None
    hrv: float | None = None
    body_battery: float | None = None
    stress_avg: float | None = None
    training_readiness: float | None = None
    fitness_age: float | None = None
    intensity_total: float | None = None
    activity_count: int = 0
    activities: list[str] | None = None


def parse_duration_minutes(text: str) -> float | None:
    text = text.strip()
    match = re.fullmatch(r"(?:(\d+)h\s*)?(\d{1,2})m", text)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        return float(hours * 60 + minutes)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if match:
        return float(int(match.group(1)) + int(match.group(2)) / 60)
    return None


def number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def first_number(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, re.M)
    return number(match.group(1)) if match else None


def parse_daily_file(path: Path) -> DailyRecord | None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name):
        return None
    text = path.read_text(errors="replace")
    day = path.stem
    record = DailyRecord(date=day)

    sleep_match = re.search(r"^## Sleep:\s*([^\n(]+)", text, re.M)
    if sleep_match:
        record.sleep_minutes = parse_duration_minutes(sleep_match.group(1).strip())
    record.sleep_score = first_number(r"^Sleep Score:\s*([0-9.]+)", text)

    stages = re.search(
        r"^Deep:\s*([^|]+)\|\s*Light:\s*([^|]+)\|\s*REM:\s*([^|]+)\|\s*Awake:\s*([^\n]+)",
        text,
        re.M,
    )
    if stages:
        record.deep_minutes = parse_duration_minutes(stages.group(1).strip())
        record.light_minutes = parse_duration_minutes(stages.group(2).strip())
        record.rem_minutes = parse_duration_minutes(stages.group(3).strip())
        record.awake_minutes = parse_duration_minutes(stages.group(4).strip())

    body = re.search(r"^## Body(?::\s*([^\n]+))?", text, re.M)
    if body and body.group(1):
        record.steps = first_number(r"([0-9,]+)\s+steps", body.group(1))
        record.calories = first_number(r"([0-9,]+)\s+cal", body.group(1))

    record.distance_km = first_number(r"Distance:\s*([0-9.]+)\s*km", text)
    record.floors = first_number(r"Floors:\s*([0-9.]+)", text)
    record.resting_hr = first_number(r"Resting HR:\s*([0-9.]+)\s*bpm", text)
    record.max_hr = first_number(r"Max HR:\s*([0-9.]+)\s*bpm", text)
    record.hrv = first_number(r"HRV:\s*([0-9.]+)\s*ms", text)
    record.body_battery = first_number(r"Body Battery:\s*(?:latest\s*)?([0-9.]+)", text)
    record.stress_avg = first_number(r"^## Stress:\s*Avg\s*([0-9.]+)", text)
    record.training_readiness = first_number(r"^## Training Readiness:\s*([0-9.]+)", text)
    record.fitness_age = first_number(r"^## Fitness Age:\s*([0-9.]+)", text)
    record.intensity_total = first_number(r"^## Intensity Minutes:\s*([0-9.]+)\s+weekly", text)

    activities: list[str] = []
    in_activities = False
    for line in text.splitlines():
        if line.startswith("## Activities"):
            in_activities = True
            continue
        if in_activities and line.startswith("## "):
            break
        if in_activities and line.startswith("- "):
            name_match = re.search(r"\*\*(.*?)\*\*", line)
            if name_match:
                activities.append(name_match.group(1))
    record.activity_count = len(activities)
    record.activities = activities

    return record


def mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(statistics.fmean(present), 2)


def summarize(records: list[DailyRecord]) -> dict[str, Any]:
    summary: dict[str, Any] = {"days": len(records)}
    for field in METRIC_FIELDS:
        if field == "activity_count":
            summary[field] = sum(getattr(record, field) for record in records)
        else:
            summary[field] = mean([getattr(record, field) for record in records])
    return summary


def window(records: list[DailyRecord], days: int) -> list[DailyRecord]:
    return records[-days:] if len(records) > days else list(records)


def previous_window(records: list[DailyRecord], days: int) -> list[DailyRecord]:
    if len(records) <= days:
        return []
    return records[-days * 2 : -days]


def delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


def fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}{suffix}"


def format_minutes(value: float | None) -> str:
    if value is None:
        return "—"
    total = int(round(value))
    return f"{total // 60}h {total % 60:02d}m"


def build_profile(records: list[DailyRecord], generated_at: str) -> tuple[dict[str, Any], str]:
    records = sorted(records, key=lambda item: item.date)
    windows = {
        "7d": summarize(window(records, 7)),
        "30d": summarize(window(records, 30)),
        "90d": summarize(window(records, 90)),
        "365d": summarize(window(records, 365)),
        "all": summarize(records),
    }
    prev7 = summarize(previous_window(records, 7))
    prev30 = summarize(previous_window(records, 30))
    trend = {
        "7d_vs_prev7d": {
            field: delta(windows["7d"].get(field), prev7.get(field)) for field in METRIC_FIELDS
        },
        "30d_vs_prev30d": {
            field: delta(windows["30d"].get(field), prev30.get(field)) for field in METRIC_FIELDS
        },
    }
    latest = asdict(records[-1]) if records else {}
    payload = {
        "generatedAt": generated_at,
        "coverage": {
            "days": len(records),
            "firstDate": records[0].date if records else None,
            "latestDate": records[-1].date if records else None,
        },
        "latest": latest,
        "windows": windows,
        "trend": trend,
        "records": [asdict(record) for record in records],
    }

    lines = [
        "# Garmin Long-Term Health Profile",
        "",
        f"_Generated: {generated_at}_",
        "",
    ]
    if not records:
        lines.append("No daily Garmin health files were found.")
        return payload, "\n".join(lines) + "\n"

    lines.extend(
        [
            f"Coverage: {len(records)} day(s), {records[0].date} to {records[-1].date}.",
            "",
            "## Latest Day",
            "",
            f"- Date: {latest.get('date')}",
            f"- Sleep: {format_minutes(latest.get('sleep_minutes'))}, score {fmt(latest.get('sleep_score'))}",
            f"- Steps: {fmt(latest.get('steps'))}, distance {fmt(latest.get('distance_km'), ' km')}",
            f"- Resting HR: {fmt(latest.get('resting_hr'), ' bpm')}, HRV {fmt(latest.get('hrv'), ' ms')}, Body Battery {fmt(latest.get('body_battery'))}",
            f"- Stress: {fmt(latest.get('stress_avg'))}, readiness {fmt(latest.get('training_readiness'))}",
            f"- Activities: {fmt(latest.get('activity_count'))}",
            "",
            "## Rolling Windows",
            "",
            "| Window | Days | Sleep | Sleep Score | Steps | Resting HR | HRV | Body Battery | Stress | Readiness | Activities |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name in ["7d", "30d", "90d", "365d", "all"]:
        item = windows[name]
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(item["days"]),
                    format_minutes(item.get("sleep_minutes")),
                    fmt(item.get("sleep_score")),
                    fmt(item.get("steps")),
                    fmt(item.get("resting_hr")),
                    fmt(item.get("hrv")),
                    fmt(item.get("body_battery")),
                    fmt(item.get("stress_avg")),
                    fmt(item.get("training_readiness")),
                    fmt(item.get("activity_count")),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Trend Deltas", ""])
    trend_specs = [
        ("7d vs previous 7d", trend["7d_vs_prev7d"]),
        ("30d vs previous 30d", trend["30d_vs_prev30d"]),
    ]
    for label, item in trend_specs:
        lines.append(f"### {label}")
        lines.append(
            "- "
            + "; ".join(
                [
                    f"sleep {fmt(item.get('sleep_minutes'), ' min')}",
                    f"sleep score {fmt(item.get('sleep_score'))}",
                    f"steps {fmt(item.get('steps'))}",
                    f"resting HR {fmt(item.get('resting_hr'), ' bpm')}",
                    f"HRV {fmt(item.get('hrv'), ' ms')}",
                    f"stress {fmt(item.get('stress_avg'))}",
                    f"readiness {fmt(item.get('training_readiness'))}",
                ]
            )
        )
        lines.append("")

    watchlist: list[str] = []
    latest_sleep_score = latest.get("sleep_score")
    latest_readiness = latest.get("training_readiness")
    latest_body_battery = latest.get("body_battery")
    trend7 = trend["7d_vs_prev7d"]
    if latest_sleep_score is not None and latest_sleep_score < 75:
        watchlist.append(f"Latest sleep score is low ({latest_sleep_score}).")
    if latest_readiness is not None and latest_readiness < 50:
        watchlist.append(f"Latest training readiness is below 50 ({latest_readiness}).")
    if latest_body_battery is not None and latest_body_battery < 30:
        watchlist.append(f"Latest Body Battery is low ({latest_body_battery}).")
    if trend7.get("hrv") is not None and trend7["hrv"] < -5:
        watchlist.append(f"7-day HRV average is down {abs(trend7['hrv'])} ms versus previous 7 days.")
    if trend7.get("resting_hr") is not None and trend7["resting_hr"] > 3:
        watchlist.append(f"7-day resting HR average is up {trend7['resting_hr']} bpm versus previous 7 days.")
    if trend7.get("sleep_minutes") is not None and trend7["sleep_minutes"] < -30:
        watchlist.append(f"7-day sleep average is down {abs(trend7['sleep_minutes'])} minutes versus previous 7 days.")

    lines.extend(["## Watchlist", ""])
    if watchlist:
        lines.extend(f"- {item}" for item in watchlist)
    else:
        lines.append("- No automatic watchlist item from the synced metrics.")
    lines.extend(
        [
            "",
            "Use this profile for longitudinal coaching. For a specific day, read the matching `YYYY-MM-DD.md`; for raw API details, inspect `raw/YYYY-MM-DD.json` when available.",
            "",
        ]
    )
    return payload, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build long-term Garmin profile from health markdown files.")
    parser.add_argument("--health-dir", type=Path, default=Path(__file__).resolve().parent.parent / "health")
    parser.add_argument("--profile-out", type=Path)
    parser.add_argument("--metrics-out", type=Path)
    args = parser.parse_args()

    health_dir = args.health_dir
    profile_out = args.profile_out or health_dir / "profile.md"
    metrics_out = args.metrics_out or health_dir / "metrics.json"
    records = [
        record
        for record in (parse_daily_file(path) for path in sorted(health_dir.glob("*.md")))
        if record is not None
    ]
    generated_at = datetime.now().isoformat(timespec="seconds")
    payload, profile = build_profile(records, generated_at)

    health_dir.mkdir(parents=True, exist_ok=True)
    profile_out.write_text(profile, encoding="utf-8")
    metrics_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(Exception):
        os.chmod(profile_out, 0o600)
        os.chmod(metrics_out, 0o600)
    print(f"Wrote {profile_out}")
    print(f"Wrote {metrics_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
