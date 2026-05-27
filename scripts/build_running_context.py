#!/usr/bin/env python3
"""Build running-specific context for Garmin health reports.

The nightly OpenClaw report uses this helper to avoid asking the model to do
fragile arithmetic over many daily files. It reads only local Garmin health
exports and prints a compact JSON summary.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
RUN_NAME_RE = re.compile(r"(跑|run|running|track)", re.IGNORECASE)
RUN_TYPE_KEYS = {
    "running",
    "track_running",
    "trail_running",
    "treadmill_running",
    "virtual_running",
    "street_running",
}


@dataclass
class RunActivity:
    date: str
    start_time_local: str | None
    name: str
    type_key: str | None
    distance_km: float | None
    duration_min: float | None
    moving_duration_min: float | None
    pace_sec_per_km: float | None
    pace: str | None
    avg_hr: float | None
    max_hr: float | None
    cadence_spm: float | None
    avg_power_w: float | None
    max_power_w: float | None
    aerobic_te: float | None
    anaerobic_te: float | None
    training_effect_label: str | None
    vo2max: float | None
    calories: float | None
    intensity: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def activity_day(raw_day: str, activity: dict[str, Any]) -> str:
    start = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if isinstance(start, str) and len(start) >= 10:
        return start[:10]
    return raw_day


def activity_type_key(activity: dict[str, Any]) -> str | None:
    activity_type = activity.get("activityType")
    if isinstance(activity_type, dict):
        key = activity_type.get("typeKey")
        if isinstance(key, str):
            return key
    key = activity.get("activityTypeKey")
    return key if isinstance(key, str) else None


def is_running_activity(activity: dict[str, Any]) -> bool:
    type_key = activity_type_key(activity)
    if type_key in RUN_TYPE_KEYS:
        return True
    name = activity.get("activityName")
    return isinstance(name, str) and RUN_NAME_RE.search(name) is not None


def duration_minutes(activity: dict[str, Any], key: str) -> float | None:
    seconds = as_float(activity.get(key))
    if seconds is None:
        return None
    return round(seconds / 60, 2)


def pace_sec_per_km(activity: dict[str, Any], distance_km: float | None) -> float | None:
    speed = as_float(activity.get("averageSpeed"))
    if speed and speed > 0:
        return round(1000 / speed, 1)
    seconds = as_float(activity.get("movingDuration") or activity.get("duration"))
    if seconds and distance_km and distance_km > 0:
        return round(seconds / distance_km, 1)
    return None


def format_pace(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}/km"


def classify_intensity(activity: dict[str, Any]) -> str:
    label = str(activity.get("trainingEffectLabel") or "").lower()
    avg_hr = as_float(activity.get("averageHR"))
    aerobic = as_float(activity.get("aerobicTrainingEffect"))
    anaerobic = as_float(activity.get("anaerobicTrainingEffect"))
    if (
        (anaerobic is not None and anaerobic >= 2.0)
        or (aerobic is not None and aerobic >= 3.5)
        or "vo2" in label
        or "threshold" in label
        or (avg_hr is not None and avg_hr >= 170)
    ):
        return "hard"
    if (aerobic is not None and aerobic >= 2.0) or (avg_hr is not None and avg_hr >= 150):
        return "moderate"
    return "easy"


def activity_to_run(raw_day: str, activity: dict[str, Any]) -> RunActivity:
    distance_m = as_float(activity.get("distance"))
    distance_km = round(distance_m / 1000, 2) if distance_m and distance_m > 0 else None
    pace_seconds = pace_sec_per_km(activity, distance_km)
    return RunActivity(
        date=activity_day(raw_day, activity),
        start_time_local=activity.get("startTimeLocal") if isinstance(activity.get("startTimeLocal"), str) else None,
        name=str(activity.get("activityName") or "Run"),
        type_key=activity_type_key(activity),
        distance_km=distance_km,
        duration_min=duration_minutes(activity, "duration"),
        moving_duration_min=duration_minutes(activity, "movingDuration"),
        pace_sec_per_km=pace_seconds,
        pace=format_pace(pace_seconds),
        avg_hr=round_or_none(as_float(activity.get("averageHR")), 1),
        max_hr=round_or_none(as_float(activity.get("maxHR")), 1),
        cadence_spm=round_or_none(as_float(activity.get("averageRunningCadenceInStepsPerMinute")), 1),
        avg_power_w=round_or_none(as_float(activity.get("avgPower") or activity.get("averagePower")), 1),
        max_power_w=round_or_none(as_float(activity.get("maxPower")), 1),
        aerobic_te=round_or_none(as_float(activity.get("aerobicTrainingEffect")), 1),
        anaerobic_te=round_or_none(as_float(activity.get("anaerobicTrainingEffect")), 1),
        training_effect_label=activity.get("trainingEffectLabel")
        if isinstance(activity.get("trainingEffectLabel"), str)
        else None,
        vo2max=round_or_none(as_float(activity.get("vO2MaxValue") or activity.get("vo2MaxValue")), 1),
        calories=round_or_none(as_float(activity.get("calories")), 1),
        intensity=classify_intensity(activity),
    )


def sort_key(run: RunActivity) -> tuple[str, str]:
    return (run.date, run.start_time_local or "")


def load_runs(health_dir: Path, earliest: date | None = None) -> list[RunActivity]:
    runs: list[RunActivity] = []
    raw_dir = health_dir / "raw"
    for path in sorted(raw_dir.glob("*.json")):
        raw_day = path.stem
        try:
            day = parse_day(raw_day)
        except ValueError:
            continue
        if earliest is not None and day < earliest:
            continue
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        activities = data.get("activities") if isinstance(data, dict) else None
        if not isinstance(activities, list):
            continue
        for activity in activities:
            if isinstance(activity, dict) and is_running_activity(activity):
                runs.append(activity_to_run(raw_day, activity))
    return sorted(runs, key=sort_key)


def metric_record_by_date(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = metrics.get("records")
    if not isinstance(records, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("date"), str):
            result[record["date"]] = record
    return result


def average(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 2)


def sum_distance(runs: list[RunActivity]) -> float:
    return round(sum(run.distance_km or 0 for run in runs), 2)


def period_summary(runs: list[RunActivity], start: date, end: date) -> dict[str, Any]:
    selected = [run for run in runs if start <= parse_day(run.date) <= end]
    run_days = sorted({run.date for run in selected})
    avg_pace = average([run.pace_sec_per_km for run in selected if (run.distance_km or 0) >= 1])
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "run_count": len(selected),
        "run_days": len(run_days),
        "distance_km": sum_distance(selected),
        "avg_pace_sec_per_km": avg_pace,
        "avg_pace": format_pace(avg_pace),
        "avg_hr": average([run.avg_hr for run in selected]),
        "hard_count": sum(1 for run in selected if run.intensity == "hard"),
        "moderate_count": sum(1 for run in selected if run.intensity == "moderate"),
        "easy_count": sum(1 for run in selected if run.intensity == "easy"),
    }


def comparison(target: RunActivity | None, candidates: list[RunActivity]) -> dict[str, Any] | None:
    if target is None:
        return None
    comparable = [run for run in candidates if (run.distance_km or 0) >= 1]
    if not comparable:
        return None
    pace_avg = average([run.pace_sec_per_km for run in comparable])
    hr_avg = average([run.avg_hr for run in comparable])
    result = {
        "count": len(comparable),
        "avg_distance_km": average([run.distance_km for run in comparable]),
        "avg_pace_sec_per_km": pace_avg,
        "avg_pace": format_pace(pace_avg),
        "avg_hr": hr_avg,
        "avg_max_hr": average([run.max_hr for run in comparable]),
        "avg_aerobic_te": average([run.aerobic_te for run in comparable]),
        "target_pace_delta_sec_per_km": None,
        "target_avg_hr_delta": None,
    }
    if target.pace_sec_per_km is not None and pace_avg is not None:
        result["target_pace_delta_sec_per_km"] = round(target.pace_sec_per_km - pace_avg, 1)
        result["target_pace_delta_note"] = "faster" if target.pace_sec_per_km < pace_avg else "slower"
    if target.avg_hr is not None and hr_avg is not None:
        result["target_avg_hr_delta"] = round(target.avg_hr - hr_avg, 1)
    return result


def baseline_metric(metrics: dict[str, Any], window_name: str, field: str) -> Any:
    windows = metrics.get("windows")
    if not isinstance(windows, dict):
        return None
    window_data = windows.get(window_name)
    if not isinstance(window_data, dict):
        return None
    return window_data.get(field)


def build_recovery_baselines(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields = [
        "sleep_score",
        "sleep_minutes",
        "resting_hr",
        "hrv",
        "body_battery",
        "training_readiness",
    ]
    return {
        window: {field: baseline_metric(metrics, window, field) for field in fields}
        for window in ["7d", "30d", "90d"]
    }


def training_content(run: RunActivity | None) -> str | None:
    if run is None:
        return None
    label = (run.training_effect_label or "").upper()
    if run.intensity == "hard" and ("VO2" in label or (run.anaerobic_te or 0) >= 3):
        return "VO2max/速度耐力刺激课"
    if run.intensity == "hard" and (run.aerobic_te or 0) >= 4:
        return "高强度有氧-无氧混合课"
    if run.intensity == "moderate":
        return "中等强度有氧或节奏控制课"
    if run.intensity == "easy":
        return "轻松跑/恢复跑"
    return "普通跑步训练"


def body_state(latest_health: dict[str, Any], baselines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    readiness = as_float(latest_health.get("training_readiness"))
    sleep_score = as_float(latest_health.get("sleep_score"))
    hrv = as_float(latest_health.get("hrv"))
    resting_hr = as_float(latest_health.get("resting_hr"))
    body_battery = as_float(latest_health.get("body_battery"))
    base30 = baselines.get("30d", {})
    notes: list[str] = []
    flags: list[str] = []

    if readiness is not None and readiness <= 20:
        flags.append("low_training_readiness")
        notes.append(f"Training Readiness {readiness:g}，身体不适合叠加强刺激。")
    elif readiness is not None and readiness >= 70:
        notes.append(f"Training Readiness {readiness:g}，恢复状态支持训练。")

    if sleep_score is not None and sleep_score >= 85:
        notes.append(f"睡眠分 {sleep_score:g}，睡眠质量本身较好。")
    elif sleep_score is not None and sleep_score < 75:
        flags.append("low_sleep_score")
        notes.append(f"睡眠分 {sleep_score:g} 偏低，训练承受力可能下降。")

    hrv30 = as_float(base30.get("hrv"))
    if hrv is not None and hrv30 is not None:
        delta_hrv = round(hrv - hrv30, 1)
        if delta_hrv <= -3:
            flags.append("hrv_below_30d")
            notes.append(f"HRV {hrv:g} ms，比 30 天均值低 {abs(delta_hrv):g} ms。")
        elif delta_hrv >= 3:
            notes.append(f"HRV {hrv:g} ms，比 30 天均值高 {delta_hrv:g} ms。")

    rhr30 = as_float(base30.get("resting_hr"))
    if resting_hr is not None and rhr30 is not None:
        delta_rhr = round(resting_hr - rhr30, 1)
        if delta_rhr >= 3:
            flags.append("resting_hr_above_30d")
            notes.append(f"静息心率 {resting_hr:g} bpm，比 30 天均值高 {delta_rhr:g} bpm。")
        elif delta_rhr <= -3:
            notes.append(f"静息心率 {resting_hr:g} bpm，比 30 天均值低 {abs(delta_rhr):g} bpm。")

    if body_battery is not None and body_battery < 30:
        flags.append("low_body_battery")
        notes.append(f"Body Battery {body_battery:g} 偏低。")

    return {
        "status": "caution" if flags else "ok",
        "flags": flags,
        "notes": notes,
    }


def heart_performance_state(
    run: RunActivity | None,
    comparisons: dict[str, Any],
) -> dict[str, Any]:
    if run is None:
        return {"status": "no_run", "notes": []}
    notes: list[str] = []
    flags: list[str] = []
    prior5 = comparisons.get("latestVsPrior5Runs") or {}
    hard30 = comparisons.get("latestVsPrior30dHardRuns") or {}
    normal30 = comparisons.get("latestVsPrior30dNormalRuns") or {}

    if run.avg_hr is not None and run.pace:
        notes.append(f"本次主跑均心 {run.avg_hr:g} bpm，配速 {run.pace}。")
    if run.max_hr is not None and run.max_hr >= 190:
        flags.append("very_high_max_hr")
        notes.append(f"最大心率 {run.max_hr:g} bpm，属于很高的心血管刺激。")

    prior5_pace = as_float(prior5.get("target_pace_delta_sec_per_km"))
    prior5_hr = as_float(prior5.get("target_avg_hr_delta"))
    if prior5_pace is not None and prior5_hr is not None:
        if prior5_pace < -5 and prior5_hr <= 3:
            notes.append("相比最近几次，配速更快且心率没有明显上扬，是效率进步信号。")
        elif prior5_pace < -5 and prior5_hr > 5:
            flags.append("higher_hr_for_faster_pace")
            notes.append("相比最近几次，配速更快但均心也明显更高，更像主动强度课而不是轻松效率提升。")
        elif prior5_pace >= -5 and prior5_hr > 5:
            flags.append("higher_hr_without_pace_gain")
            notes.append("相比最近几次，配速没有明显更快但心率更高，需要警惕疲劳、热或恢复不足。")

    hard_pace = as_float(hard30.get("target_pace_delta_sec_per_km"))
    hard_hr = as_float(hard30.get("target_avg_hr_delta"))
    if hard_pace is not None and hard_hr is not None:
        if hard_pace > 10 and hard_hr < 0:
            notes.append("相对近 30 天强度课，本次配速更慢但均心略低，更偏高负荷有氧/VO2 刺激，不是最快的一类质量课。")
        elif hard_pace <= 0 and hard_hr <= 0:
            notes.append("相对近 30 天强度课，本次在接近或更快配速下心率不高，是较好的表现信号。")

    normal_pace = as_float(normal30.get("target_pace_delta_sec_per_km"))
    normal_hr = as_float(normal30.get("target_avg_hr_delta"))
    if normal_pace is not None and normal_hr is not None and normal_hr > 8:
        flags.append("much_higher_than_normal_hr")
        notes.append("相对近 30 天普通训练，本次心率明显更高，不能按普通训练恢复消耗来处理。")

    return {
        "status": "watch" if flags else "positive",
        "flags": flags,
        "notes": notes,
    }


def training_quality(
    run: RunActivity | None,
    body: dict[str, Any],
    heart: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    if run is None:
        return {"verdict": "no_run", "notes": []}
    notes: list[str] = []
    benefits: list[str] = []
    concerns: list[str] = []

    if run.intensity == "hard":
        benefits.append("强度刺激明确，有助于提升 VO2max、速度耐力和高心率区间耐受。")
    elif run.intensity == "moderate":
        benefits.append("中等强度有氧刺激，有助于维持节奏能力和有氧负荷。")
    else:
        benefits.append("低强度活动有助于恢复、跑姿维持和基础有氧。")

    if run.aerobic_te is not None and run.aerobic_te >= 4:
        benefits.append(f"有氧训练效果 {run.aerobic_te:g}，对有氧能力有明显刺激。")
    if run.anaerobic_te is not None and run.anaerobic_te >= 2:
        benefits.append(f"无氧训练效果 {run.anaerobic_te:g}，有速度/变速刺激收益。")

    body_flags = set(body.get("flags") or [])
    heart_flags = set(heart.get("flags") or [])
    hard_week = (volume.get("rolling7d") or {}).get("hard_count")
    if body_flags & {"low_training_readiness", "low_body_battery", "hrv_below_30d"}:
        concerns.append("恢复指标不支持继续叠加强度，本次课有效但恢复成本偏高。")
    if "higher_hr_without_pace_gain" in heart_flags or "much_higher_than_normal_hr" in heart_flags:
        concerns.append("心率相对普通训练偏高，需防止把强度课误判成常规训练。")
    if isinstance(hard_week, int) and hard_week >= 2:
        concerns.append(f"近 7 天已有 {hard_week} 次 hard 跑步，强度密度偏高。")

    if concerns:
        verdict = "有效但恢复风险偏高"
    elif run.intensity == "hard":
        verdict = "高质量强度课"
    elif run.intensity == "moderate":
        verdict = "合格的有氧/节奏课"
    else:
        verdict = "合格的恢复课"

    notes.append(f"训练内容判断：{training_content(run)}。")
    notes.append(f"训练质量判断：{verdict}。")
    return {
        "verdict": verdict,
        "notes": notes,
        "benefits": benefits,
        "concerns": concerns,
    }


def recovery_advice(run: RunActivity | None, body: dict[str, Any], volume: dict[str, Any]) -> list[str]:
    if run is None:
        return []
    advice: list[str] = []
    body_flags = set(body.get("flags") or [])
    hard_week = (volume.get("rolling7d") or {}).get("hard_count")
    if run.intensity == "hard" or body_flags:
        advice.append("未来 24-48 小时避免再次强刺激，优先休息、散步或 Z1/Z2 轻松跑。")
    if body_flags & {"low_training_readiness", "low_body_battery"}:
        advice.append("等 Training Readiness 和 Body Battery 明显回升后，再安排间歇、节奏或 VO2max 课。")
    if isinstance(hard_week, int) and hard_week >= 2:
        advice.append("本周强度课已经不少，下一次质量课前至少插入 1-2 天低强度恢复。")
    if run.intensity == "easy" and not body_flags:
        advice.append("如果主观疲劳低，明天可以维持轻松有氧或短技术跑。")
    if not advice:
        advice.append("明天以低到中等强度有氧为主，观察晨脉、HRV 和腿部疲劳再决定是否加量。")
    return advice


def build_training_assessment(
    latest_main_run: RunActivity | None,
    latest_health: dict[str, Any],
    baselines: dict[str, dict[str, Any]],
    comparisons: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    if latest_main_run is None:
        return {
            "hasRun": False,
            "summary": "latest day has no running activity",
            "requiredReportAngles": [],
        }
    body = body_state(latest_health, baselines)
    heart = heart_performance_state(latest_main_run, comparisons)
    quality = training_quality(latest_main_run, body, heart, volume)
    return {
        "hasRun": True,
        "trainingContent": training_content(latest_main_run),
        "trainingIntensity": {
            "class": latest_main_run.intensity,
            "aerobicTrainingEffect": latest_main_run.aerobic_te,
            "anaerobicTrainingEffect": latest_main_run.anaerobic_te,
            "label": latest_main_run.training_effect_label,
        },
        "bodyState": body,
        "heartAndPerformance": heart,
        "trainingQuality": quality,
        "benefits": quality["benefits"],
        "recoveryAdvice": recovery_advice(latest_main_run, body, volume),
        "requiredReportAngles": [
            "训练内容",
            "训练强度",
            "最近训练课背景",
            "身体状态",
            "心率状态",
            "运动表现",
            "是否是好的训练课",
            "训练收益",
            "恢复建议",
        ],
    }


def compact_run(run: RunActivity | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return asdict(run)


def build_context(health_dir: Path, days: int) -> dict[str, Any]:
    metrics_path = health_dir / "metrics.json"
    metrics = read_json(metrics_path)
    coverage = metrics.get("coverage") if isinstance(metrics.get("coverage"), dict) else {}
    latest_date_raw = coverage.get("latestDate") or metrics.get("latest", {}).get("date")
    if not isinstance(latest_date_raw, str):
        raise SystemExit("metrics.json does not contain coverage.latestDate")
    latest_day = parse_day(latest_date_raw)
    earliest = latest_day - timedelta(days=days - 1)
    runs = load_runs(health_dir, earliest=earliest)
    records = metric_record_by_date(metrics)
    latest_health = records.get(latest_date_raw) or metrics.get("latest") or {}

    latest_day_runs = [run for run in runs if run.date == latest_date_raw]
    latest_main_run = max(latest_day_runs, key=lambda item: item.distance_km or 0, default=None)
    previous_runs = [run for run in runs if sort_key(run) < sort_key(latest_main_run)] if latest_main_run else []
    prior_5 = previous_runs[-5:]
    prior_30 = [run for run in previous_runs if parse_day(run.date) >= latest_day - timedelta(days=30)]
    prior_30_hard = [run for run in prior_30 if run.intensity == "hard"]
    prior_30_normal = [run for run in prior_30 if run.intensity in {"easy", "moderate"}]

    week_start = latest_day - timedelta(days=latest_day.weekday())
    month_start = latest_day.replace(day=1)
    baselines = build_recovery_baselines(metrics)
    volume = {
        "calendarWeek": period_summary(runs, week_start, latest_day),
        "rolling7d": period_summary(runs, latest_day - timedelta(days=6), latest_day),
        "calendarMonth": period_summary(runs, month_start, latest_day),
        "rolling30d": period_summary(runs, latest_day - timedelta(days=29), latest_day),
    }
    comparisons = {
        "latestVsPrior5Runs": comparison(latest_main_run, prior_5),
        "latestVsPrior30dRuns": comparison(latest_main_run, prior_30),
        "latestVsPrior30dHardRuns": comparison(latest_main_run, prior_30_hard),
        "latestVsPrior30dNormalRuns": comparison(latest_main_run, prior_30_normal),
    }
    latest_health_context = {
        key: latest_health.get(key)
        for key in [
            "sleep_minutes",
            "sleep_score",
            "resting_hr",
            "max_hr",
            "hrv",
            "body_battery",
            "training_readiness",
            "intensity_total",
            "activity_count",
            "activities",
        ]
    }
    context = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "metricsPath": str(metrics_path),
            "rawDir": str(health_dir / "raw"),
            "daysScanned": days,
            "coverage": coverage,
        },
        "latestDate": latest_date_raw,
        "latestHealth": latest_health_context,
        "recoveryBaselines": baselines,
        "latestDayRunning": {
            "hasRun": bool(latest_day_runs),
            "runCount": len(latest_day_runs),
            "totalDistanceKm": sum_distance(latest_day_runs),
            "mainRun": compact_run(latest_main_run),
            "allRuns": [compact_run(run) for run in latest_day_runs],
        },
        "volume": volume,
        "recentRuns": [compact_run(run) for run in runs[-10:]],
        "comparisons": comparisons,
        "trainingAssessment": build_training_assessment(
            latest_main_run,
            latest_health_context,
            baselines,
            comparisons,
            volume,
        ),
        "reportGuidance": [
            "If latestDayRunning.hasRun is true, include a dedicated running workout section.",
            "Describe the main run's distance, duration, pace, average/max HR, cadence, power, aerobic/anaerobic effect, and intensity class when present.",
            "Compare the main run with prior 5 runs and 30-day hard/normal baselines; lower HR at similar/faster pace is a positive efficiency signal, while higher HR at slower/similar pace can indicate fatigue, heat, stress, or insufficient recovery.",
            "Always mention calendarWeek.distance_km and calendarMonth.distance_km, and use rolling7d/rolling30d for short-term load context.",
            "Interpret run HR together with latestHealth training_readiness, sleep_score, hrv, resting_hr, and body_battery.",
            "Use trainingAssessment to explicitly cover training content, intensity, recent workout context, body state, heart-rate response, performance, workout quality, benefits, and recovery advice.",
        ],
    }
    return context


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Garmin running context JSON for daily reports.")
    parser.add_argument("--health-dir", type=Path, default=BASE_DIR / "health")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args()

    context = build_context(args.health_dir, args.days)
    indent = 2 if args.pretty else None
    print(json.dumps(context, ensure_ascii=False, indent=indent, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
