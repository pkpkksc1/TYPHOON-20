#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard
Flight Status Fetch v8.4 - 5 representative flights + monthly API counter

Aviationstack targets

WF수입
    KE249  ICN -> PVG  기준 출발 01:20
    KE335  ICN -> PVG  기준 출발 01:20

수출
    PR337  PVG -> MNL  기준 출발 16:00
    KJ948  PVG -> ICN  기준 출발 03:05
    KJ988  PVG -> ICN  기준 출발 19:20

Important:
- Keeps v7.4 Aviationstack handling:
  timestamp date/hour/minute is treated as the airport local wall clock.
- The API's timezone suffix is preserved only in *_raw fields.
- Route + flight ident + expected departure clock are used to avoid
  choosing the wrong record when multiple records are returned.

Required GitHub Secret:
    AVIATIONSTACK_API_KEY

Selection / robustness updates in v8.1:
    - Fix same-day early actual time being misread as +24h delay
    - Prefer today's flight_date
    - Prefer nearest future service over past service
    - Keep NO_DATA flights without failing workflow

Output:
    data/flights.json

API usage counter:
- Tracks requests made by this dashboard from the moment v8.4 is deployed.
- Resets automatically when the Shanghai calendar month changes.
- This is a local dashboard counter, not provider-side billing usage.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "data" / "flights.json"

API_URL = "https://api.aviationstack.com/v1/flights"
PARSER_VERSION = "8.4"
AVIATIONSTACK_MONTHLY_LIMIT = 100
SHANGHAI_TZ = timezone(timedelta(hours=8))


AIRPORTS = {
    "ICN": {
        "timezone": "Asia/Seoul",
        "timezone_label_ko": "한국시간",
        "offset": "+09:00",
    },
    "PVG": {
        "timezone": "Asia/Shanghai",
        "timezone_label_ko": "중국시간",
        "offset": "+08:00",
    },
    "MNL": {
        "timezone": "Asia/Manila",
        "timezone_label_ko": "필리핀시간",
        "offset": "+08:00",
    },
    "CRK": {
        "timezone": "Asia/Manila",
        "timezone_label_ko": "필리핀시간",
        "offset": "+08:00",
    },
}


FLIGHT_CONFIGS = [
    {
        "flight_iata": "KE249",
        "group_ko": "WF수입",
        "dep_iata": "ICN",
        "arr_iata": "PVG",
        "expected_departure_local": "01:20",
    },
    {
        "flight_iata": "KE335",
        "group_ko": "WF수입",
        "dep_iata": "ICN",
        "arr_iata": "PVG",
        "expected_departure_local": "01:20",
    },
    {
        "flight_iata": "PR337",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "MNL",
        "expected_departure_local": "16:00",
    },
    {
        "flight_iata": "KJ948",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "ICN",
        "expected_departure_local": "03:05",
    },
    {
        "flight_iata": "KJ988",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "ICN",
        "expected_departure_local": "19:20",
    },
]


def parse_local_clock(value: Any) -> Optional[datetime]:
    """
    Preserve v7.4 behavior:
    treat YYYY-MM-DDTHH:MM:SS as local airport wall-clock time,
    ignoring the timezone suffix supplied by Aviationstack.
    """
    if not value:
        return None

    text = str(value).strip()

    try:
        if "T" in text:
            return datetime.strptime(
                text[:19],
                "%Y-%m-%dT%H:%M:%S",
            )
    except ValueError:
        return None

    return None


def local_iso(value: Any, offset_text: str) -> Optional[str]:
    dt = parse_local_clock(value)
    if not dt:
        return None

    return dt.strftime("%Y-%m-%dT%H:%M:%S") + offset_text


def local_short(value: Any) -> Optional[str]:
    dt = parse_local_clock(value)
    if not dt:
        return None

    return dt.strftime("%Y-%m-%d %H:%M")


def clock_hhmm(value: Any) -> Optional[str]:
    dt = parse_local_clock(value)
    if not dt:
        return None
    return dt.strftime("%H:%M")


def diff_minutes(
    scheduled: Any,
    actual_or_estimated: Any,
) -> Optional[int]:
    """
    Delay in minutes using the local wall-clock timestamps supplied by Aviationstack.

    Important:
    - If actual/estimated is earlier than scheduled on the SAME calendar date,
      this is treated as an early departure/arrival (negative delay), not +24h.
    - Cross-midnight is only applied when the calendar date genuinely advances.
    """
    start = parse_local_clock(scheduled)
    end = parse_local_clock(actual_or_estimated)

    if not start or not end:
        return None

    return round((end - start).total_seconds() / 60)


def circular_clock_difference_minutes(
    actual_hhmm: Optional[str],
    expected_hhmm: Optional[str],
) -> int:
    """
    Distance between two clock times without caring about calendar date.
    Used only to select the best Aviationstack record.
    """
    if not actual_hhmm or not expected_hhmm:
        return 10_000

    try:
        ah, am = map(int, actual_hhmm.split(":"))
        eh, em = map(int, expected_hhmm.split(":"))
    except (ValueError, AttributeError):
        return 10_000

    a = ah * 60 + am
    e = eh * 60 + em
    d = abs(a - e)

    return min(d, 1440 - d)


def select_display_time(
    actual: Any,
    estimated: Any,
    scheduled: Any,
) -> Any:
    if actual:
        return actual
    if estimated:
        return estimated
    return scheduled


def normalize_airport_event(
    raw: Dict[str, Any],
    airport_iata: str,
) -> Dict[str, Any]:

    airport_meta = AIRPORTS.get(
        airport_iata,
        {
            "timezone": None,
            "timezone_label_ko": "현지시간",
            "offset": "",
        },
    )

    scheduled = raw.get("scheduled")
    estimated = raw.get("estimated")
    actual = raw.get("actual")

    operational_time = select_display_time(
        actual,
        estimated,
        scheduled,
    )

    delay_target = actual or estimated

    calculated_delay = None
    if delay_target:
        calculated_delay = diff_minutes(
            scheduled,
            delay_target,
        )

    offset = airport_meta["offset"]

    return {
        "airport": raw.get("airport"),
        "iata": raw.get("iata") or airport_iata,
        "timezone": airport_meta["timezone"],
        "timezone_label_ko": airport_meta["timezone_label_ko"],

        # Aviationstack original values
        "scheduled_raw": scheduled,
        "estimated_raw": estimated,
        "actual_raw": actual,

        # Dashboard local-clock values
        "scheduled_local": local_iso(scheduled, offset),
        "estimated_local": local_iso(estimated, offset),
        "actual_local": local_iso(actual, offset),
        "display_time_local": local_short(operational_time),

        "calculated_delay_minutes": calculated_delay,
        "api_delay_minutes": raw.get("delay"),
        "terminal": raw.get("terminal"),
        "gate": raw.get("gate"),
    }



def minutes_past_arrival_estimate(
    arrival: Dict[str, Any],
) -> Optional[int]:
    """
    Minutes past ETA (or scheduled arrival when ETA is absent)
    in the arrival airport's local timezone.
    """
    target_text = (
        arrival.get("estimated_local")
        or arrival.get("scheduled_local")
    )

    if not target_text:
        return None

    try:
        target = datetime.fromisoformat(str(target_text))
    except (TypeError, ValueError):
        return None

    if target.tzinfo is None:
        return None

    now = datetime.now(timezone.utc).astimezone(target.tzinfo)

    return round(
        (now - target).total_seconds() / 60
    )


def make_status(
    flight_status_disabled_v8_71_0: str,
    departure: Dict[str, Any],
    arrival: Dict[str, Any],
) -> Dict[str, Any]:

    raw_status = (flight_status_disabled_v8_71_0 or "").lower()

    if raw_status == "cancelled":
        return {
            "level": "RED",
            "emoji": "🔴",
            "label_ko": "결항",
        }

    if raw_status in ("incident", "diverted"):
        return {
            "level": "RED",
            "emoji": "🔴",
            "label_ko": "운항 문제",
        }

    dep_delay = departure.get("calculated_delay_minutes")

    # Active flight should be shown as operating, even if estimated data exists.
    if raw_status == "active":
        return {
            "level": "YELLOW",
            "emoji": "🟡",
            "label_ko": "운항 중",
        }

    # Actual arrival timestamp = arrival confirmed.
    if arrival.get("actual_raw"):
        return {
            "level": "GREEN",
            "emoji": "🟢",
            "label_ko": "도착 완료",
        }

    # After actual departure, stop showing departure delay as the main state.
    if departure.get("actual_raw"):

        overdue_minutes = minutes_past_arrival_estimate(
            arrival
        )

        if (
            isinstance(overdue_minutes, int)
            and overdue_minutes >= 30
        ):
            return {
                "level": "YELLOW",
                "emoji": "🟡",
                "label_ko": "도착 확인 대기",
            }

        return {
            "level": "BLUE",
            "emoji": "🔵",
            "label_ko": "운항 중",
        }

    if isinstance(dep_delay, int) and dep_delay >= 10:
        return {
            "level": "YELLOW",
            "emoji": "🟡",
            "label_ko": f"출발 예정 {dep_delay}분 지연",
        }

    return {
        "level": "BLUE",
        "emoji": "🔵",
        "label_ko": "출발 예정",
    }


def current_local_date_for_airport(airport_iata: str) -> str:
    """
    Return today's date for the departure airport timezone.
    Fixed-offset zones are enough for our tracked airports.
    """
    offset_text = AIRPORTS.get(airport_iata, {}).get("offset", "+00:00")
    sign = 1 if offset_text.startswith("+") else -1

    try:
        hours, minutes = map(int, offset_text[1:].split(":"))
    except Exception:
        hours, minutes = 0, 0

    offset = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return datetime.now(timezone.utc).astimezone(offset).strftime("%Y-%m-%d")


def date_distance_days(date_text: Optional[str], target_date: str) -> int:
    try:
        d = datetime.strptime(str(date_text), "%Y-%m-%d").date()
        t = datetime.strptime(target_date, "%Y-%m-%d").date()
        return (d - t).days
    except Exception:
        return 9999


def candidate_rank(
    row: Dict[str, Any],
    config: Dict[str, str],
) -> tuple:
    """
    Ranking priority:
    1) today
    2) future date closest to today
    3) past dates last
    4) expected departure clock proximity
    """
    dep_iata = config["dep_iata"]
    today_local = current_local_date_for_airport(dep_iata)

    flight_date = row.get("flight_date")
    delta_days = date_distance_days(flight_date, today_local)

    if delta_days == 0:
        date_bucket = 0
        date_distance = 0
    elif delta_days > 0:
        date_bucket = 1
        date_distance = delta_days
    else:
        date_bucket = 2
        date_distance = abs(delta_days)

    sched_clock = clock_hhmm(
        row.get("departure", {}).get("scheduled")
    )
    clock_distance = circular_clock_difference_minutes(
        sched_clock,
        config.get("expected_departure_local"),
    )

    return (
        date_bucket,
        date_distance,
        clock_distance,
    )


def candidate_matches_route(
    candidate: Dict[str, Any],
    config: Dict[str, str],
) -> bool:

    dep = candidate.get("departure", {}).get("iata")
    arr = candidate.get("arrival", {}).get("iata")
    ident = candidate.get("flight", {}).get("iata")

    return (
        dep == config["dep_iata"]
        and arr == config["arr_iata"]
        and ident == config["flight_iata"]
    )


def choose_best_candidate(
    rows: List[Dict[str, Any]],
    config: Dict[str, str],
) -> Optional[Dict[str, Any]]:

    matches = [
        row
        for row in rows
        if candidate_matches_route(row, config)
    ]

    if not matches:
        return None

    # Prefer today's service, then the nearest future service,
    # and only use a past service as a last resort.
    matches.sort(
        key=lambda row: candidate_rank(row, config)
    )

    selected = matches[0]

    # Explicit past-flight protection:
    # if a future/today record exists, never return a past one.
    dep_iata = config["dep_iata"]
    today_local = current_local_date_for_airport(dep_iata)

    selected_delta = date_distance_days(
        selected.get("flight_date"),
        today_local,
    )

    if selected_delta < 0:
        non_past = [
            row for row in matches
            if date_distance_days(
                row.get("flight_date"),
                today_local,
            ) >= 0
        ]
        if non_past:
            non_past.sort(
                key=lambda row: candidate_rank(row, config)
            )
            selected = non_past[0]

    return selected


def load_flight(
    api_key: str,
    config: Dict[str, str],
) -> Dict[str, Any]:

    flight_iata = config["flight_iata"]
    dep_iata = config["dep_iata"]
    arr_iata = config["arr_iata"]
    route = f"{dep_iata} → {arr_iata}"

    params = urllib.parse.urlencode({
        "access_key": api_key,
        "flight_iata": flight_iata,
        "dep_iata": dep_iata,
        "arr_iata": arr_iata,
        "limit": 20,
    })

    url = f"{API_URL}?{params}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "sblc-typhoon-dashboard/8.1"
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )

    if payload.get("error"):
        raise RuntimeError(
            f"Aviationstack error for {flight_iata}: "
            f"{payload['error']}"
        )

    rows = payload.get("data", [])

    if not rows:
        return {
            "flight_iata": flight_iata,
            "group_ko": config["group_ko"],
            "route": route,
            "expected_departure_local": config["expected_departure_local"],
            "found": False,
            "status": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "조회 결과 없음",
                "note_ko": "API에 운항편 데이터가 없으며 오류로 처리하지 않음",
            },
        }

    row = choose_best_candidate(rows, config)

    if row is None:
        return {
            "flight_iata": flight_iata,
            "group_ko": config["group_ko"],
            "route": route,
            "expected_departure_local": config["expected_departure_local"],
            "found": False,
            "status": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "노선 일치 결과 없음",
            },
            "candidate_count": len(rows),
        }

    raw_departure = row.get("departure", {})
    raw_arrival = row.get("arrival", {})

    departure = normalize_airport_event(
        raw_departure,
        dep_iata,
    )

    arrival = normalize_airport_event(
        raw_arrival,
        arr_iata,
    )

    status = make_status(
        row.get("flight_status_disabled_v8_71_0"),
        departure,
        arrival,
    )

    flight = row.get("flight", {})

    selected_clock = clock_hhmm(
        raw_departure.get("scheduled")
    )

    schedule_difference = circular_clock_difference_minutes(
        selected_clock,
        config["expected_departure_local"],
    )

    return {
        "flight_iata": flight.get("iata") or flight_iata,
        "flight_number": flight.get("number"),
        "group_ko": config["group_ko"],
        "route": route,
        "dep_iata": dep_iata,
        "arr_iata": arr_iata,
        "expected_departure_local": config["expected_departure_local"],
        "selected_scheduled_clock": selected_clock,
        "schedule_match_difference_minutes": schedule_difference,
        "selection_date_local_today": current_local_date_for_airport(dep_iata),
        "selected_flight_date": row.get("flight_date"),
        "selected_date_distance_days": date_distance_days(
            row.get("flight_date"),
            current_local_date_for_airport(dep_iata),
        ),
        "selection_rule_ko": "오늘 운항편 우선 → 다음 운항일 → 출발시간 근접도",
        "found": True,
        "status_raw": row.get("flight_status_disabled_v8_71_0"),
        "status": status,
        "departure": departure,
        "arrival": arrival,
        "airline": row.get("airline", {}).get("name"),
        "flight_date": row.get("flight_date"),
    }



def load_api_usage(now_utc: datetime) -> Dict[str, Any]:
    """Load the locally persisted Aviationstack request counter."""
    period = now_utc.astimezone(SHANGHAI_TZ).strftime("%Y-%m")
    previous_count = 0
    tracking_started_at_utc = now_utc.isoformat()

    if OUTPUT_PATH.exists():
        try:
            previous = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            usage = previous.get("api_usage") or {}

            if str(usage.get("period") or "") == period:
                previous_count = int(usage.get("monthly_requests") or 0)
                tracking_started_at_utc = (
                    usage.get("tracking_started_at_utc")
                    or tracking_started_at_utc
                )
        except Exception:
            # A damaged/old flights.json must never block flight fetching.
            previous_count = 0

    return {
        "period": period,
        "previous_count": max(previous_count, 0),
        "tracking_started_at_utc": tracking_started_at_utc,
    }

def main() -> int:
    print(f"Flight fetch version: {PARSER_VERSION}")

    api_key = os.environ.get(
        "AVIATIONSTACK_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "AVIATIONSTACK_API_KEY secret is missing."
        )

    usage_base = load_api_usage(datetime.now(timezone.utc))

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    run_request_count = 0

    for config in FLIGHT_CONFIGS:
        ident = config["flight_iata"]
        route = f"{config['dep_iata']} -> {config['arr_iata']}"

        print(
            f"Fetching {config['group_ko']} / "
            f"{ident} / {route}"
        )

        try:
            result = load_flight(
                api_key,
                config,
            )
            results.append(result)

            # Aviationstack states that errored requests do not count toward
            # monthly quota, so only a completed API response is counted here.
            run_request_count += 1
        except Exception as exc:
            errors.append({
                "flight_iata": ident,
                "group_ko": config["group_ko"],
                "route": route,
                "error": str(exc),
            })
            print(f"ERROR {ident}: {exc}")

    generated_at_utc = datetime.now(timezone.utc)
    monthly_requests = usage_base["previous_count"] + run_request_count

    api_usage = {
        "provider": "Aviationstack",
        "counter_type": "dashboard_local",
        "period": usage_base["period"],
        "monthly_requests": monthly_requests,
        "monthly_limit": AVIATIONSTACK_MONTHLY_LIMIT,
        "last_run_requests": run_request_count,
        "last_fetch_at_utc": generated_at_utc.isoformat(),
        "tracking_started_at_utc": usage_base["tracking_started_at_utc"],
    }

    output = {
        "source": "Aviationstack",
        "product": "Manual Flight Status - 5 Representative Flights",
        "parser_version": PARSER_VERSION,
        "api_usage": api_usage,

        "groups": {
            "WF수입": [
                c["flight_iata"]
                for c in FLIGHT_CONFIGS
                if c["group_ko"] == "WF수입"
            ],
            "수출": [
                c["flight_iata"]
                for c in FLIGHT_CONFIGS
                if c["group_ko"] == "수출"
            ],
        },

        "tracked_flights": [
            c["flight_iata"]
            for c in FLIGHT_CONFIGS
        ],

        "flight_config": FLIGHT_CONFIGS,
        "flights": results,
        "errors": errors,

        "generated_at_utc": generated_at_utc.isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Updated: {OUTPUT_PATH}")
    print(f"Flights returned: {len(results)}")
    print(f"Errors: {len(errors)}")
    print(f"API requests this run: {run_request_count}")
    print(
        f"API requests this month: {monthly_requests} / "
        f"{AVIATIONSTACK_MONTHLY_LIMIT}"
    )

    # Do not fail the whole workflow when only one airline has no result.
    # Fail only if all configured API calls errored.
    return 1 if len(errors) == len(FLIGHT_CONFIGS) else 0


if __name__ == "__main__":
    sys.exit(main())
