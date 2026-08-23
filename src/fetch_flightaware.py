#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard
FlightAware AeroAPI Fetch v1.0

Purpose
-------
Fetch the same 7 representative flights from FlightAware AeroAPI
for cross-checking against Aviationstack.

FlightAware's recommended lookup is GET /flights/{ident}.
The response can contain multiple flight records, so this script
selects the record by:
    1) expected route
    2) today's departure-airport local date
    3) nearest future date
    4) expected departure clock proximity

Required GitHub Secret
----------------------
FLIGHTAWARE_API_KEY

Output
------
data/flightaware_flights.json
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
OUTPUT_PATH = BASE_DIR / "data" / "flightaware_flights.json"

API_BASE = "https://aeroapi.flightaware.com/aeroapi"
PARSER_VERSION = "1.0"


AIRPORTS = {
    "ICN": {"icao": "RKSI", "offset": "+09:00", "timezone_label_ko": "한국시간"},
    "PVG": {"icao": "ZSPD", "offset": "+08:00", "timezone_label_ko": "중국시간"},
    "MNL": {"icao": "RPLL", "offset": "+08:00", "timezone_label_ko": "필리핀시간"},
    "CRK": {"icao": "RPLC", "offset": "+08:00", "timezone_label_ko": "필리핀시간"},
}


FLIGHT_CONFIGS = [
    {
        "flight_iata": "KE315",
        "fa_ident": "KAL315",
        "group_ko": "WF수입",
        "dep_iata": "ICN",
        "arr_iata": "PVG",
        "expected_departure_local": "23:10",
    },
    {
        "flight_iata": "KE249",
        "fa_ident": "KAL249",
        "group_ko": "WF수입",
        "dep_iata": "ICN",
        "arr_iata": "PVG",
        "expected_departure_local": "01:20",
    },
    {
        "flight_iata": "KE335",
        "fa_ident": "KAL335",
        "group_ko": "WF수입",
        "dep_iata": "ICN",
        "arr_iata": "PVG",
        "expected_departure_local": "01:20",
    },
    {
        "flight_iata": "PR337",
        "fa_ident": "PAL337",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "MNL",
        "expected_departure_local": "16:00",
    },
    {
        "flight_iata": "RW609",
        "fa_ident": "RYL609",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "CRK",
        "expected_departure_local": "16:30",
    },
    {
        "flight_iata": "KJ948",
        "fa_ident": "AIH948",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "ICN",
        "expected_departure_local": "03:05",
    },
    {
        "flight_iata": "KJ988",
        "fa_ident": "AIH988",
        "group_ko": "수출",
        "dep_iata": "PVG",
        "arr_iata": "ICN",
        "expected_departure_local": "19:20",
    },
]


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def offset_timezone(offset_text: str) -> timezone:
    sign = 1 if offset_text.startswith("+") else -1
    hh, mm = map(int, offset_text[1:].split(":"))
    return timezone(sign * timedelta(hours=hh, minutes=mm))


def to_local_iso(value: Any, airport_iata: str) -> Optional[str]:
    dt = parse_iso(value)
    if not dt:
        return None
    meta = AIRPORTS[airport_iata]
    local = dt.astimezone(offset_timezone(meta["offset"]))
    return local.isoformat()


def local_clock(value: Any, airport_iata: str) -> Optional[str]:
    dt = parse_iso(value)
    if not dt:
        return None
    meta = AIRPORTS[airport_iata]
    local = dt.astimezone(offset_timezone(meta["offset"]))
    return local.strftime("%H:%M")


def local_date(value: Any, airport_iata: str) -> Optional[str]:
    dt = parse_iso(value)
    if not dt:
        return None
    meta = AIRPORTS[airport_iata]
    local = dt.astimezone(offset_timezone(meta["offset"]))
    return local.strftime("%Y-%m-%d")


def today_local(airport_iata: str) -> str:
    meta = AIRPORTS[airport_iata]
    return datetime.now(timezone.utc).astimezone(
        offset_timezone(meta["offset"])
    ).strftime("%Y-%m-%d")


def circular_clock_difference_minutes(
    actual_hhmm: Optional[str],
    expected_hhmm: Optional[str],
) -> int:
    if not actual_hhmm or not expected_hhmm:
        return 10000
    try:
        ah, am = map(int, actual_hhmm.split(":"))
        eh, em = map(int, expected_hhmm.split(":"))
    except Exception:
        return 10000
    a = ah * 60 + am
    e = eh * 60 + em
    d = abs(a - e)
    return min(d, 1440 - d)


def date_distance_days(date_text: Optional[str], target_date: str) -> int:
    try:
        d = datetime.strptime(str(date_text), "%Y-%m-%d").date()
        t = datetime.strptime(target_date, "%Y-%m-%d").date()
        return (d - t).days
    except Exception:
        return 9999


def airport_code_matches(raw: Dict[str, Any], iata: str) -> bool:
    if not isinstance(raw, dict):
        return False
    expected_icao = AIRPORTS[iata]["icao"]

    candidates = {
        str(raw.get("code_iata") or "").upper(),
        str(raw.get("code_icao") or "").upper(),
        str(raw.get("code") or "").upper(),
    }

    return iata.upper() in candidates or expected_icao.upper() in candidates


def candidate_route_matches(
    row: Dict[str, Any],
    config: Dict[str, str],
) -> bool:
    return (
        airport_code_matches(row.get("origin", {}), config["dep_iata"])
        and airport_code_matches(row.get("destination", {}), config["arr_iata"])
    )


def preferred_departure_time(row: Dict[str, Any]) -> Any:
    return (
        row.get("scheduled_out")
        or row.get("estimated_out")
        or row.get("actual_out")
        or row.get("scheduled_off")
        or row.get("estimated_off")
        or row.get("actual_off")
    )


def preferred_arrival_time(row: Dict[str, Any]) -> Any:
    return (
        row.get("scheduled_in")
        or row.get("estimated_in")
        or row.get("actual_in")
        or row.get("scheduled_on")
        or row.get("estimated_on")
        or row.get("actual_on")
    )


def candidate_rank(
    row: Dict[str, Any],
    config: Dict[str, str],
) -> tuple:

    dep_iata = config["dep_iata"]
    target_date = today_local(dep_iata)

    dep_time = preferred_departure_time(row)
    flight_date = local_date(dep_time, dep_iata)
    delta = date_distance_days(flight_date, target_date)

    if delta == 0:
        bucket = 0
        day_distance = 0
    elif delta > 0:
        bucket = 1
        day_distance = delta
    else:
        bucket = 2
        day_distance = abs(delta)

    clock = local_clock(dep_time, dep_iata)
    clock_diff = circular_clock_difference_minutes(
        clock,
        config["expected_departure_local"],
    )

    return bucket, day_distance, clock_diff


def choose_best_candidate(
    rows: List[Dict[str, Any]],
    config: Dict[str, str],
) -> Optional[Dict[str, Any]]:

    route_matches = [
        row for row in rows
        if candidate_route_matches(row, config)
    ]

    if not route_matches:
        return None

    route_matches.sort(
        key=lambda row: candidate_rank(row, config)
    )

    # today -> future -> past
    return route_matches[0]


def minutes_between(a: Any, b: Any) -> Optional[int]:
    da = parse_iso(a)
    db = parse_iso(b)
    if not da or not db:
        return None
    return round((db - da).total_seconds() / 60)


def status_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    cancelled = bool(row.get("cancelled"))
    diverted = bool(row.get("diverted"))

    if cancelled:
        return {"level": "RED", "emoji": "🔴", "label_ko": "결항"}

    if diverted:
        return {"level": "RED", "emoji": "🔴", "label_ko": "회항"}

    actual_on = row.get("actual_on")
    actual_in = row.get("actual_in")
    actual_off = row.get("actual_off")
    actual_out = row.get("actual_out")

    if actual_on or actual_in:
        return {"level": "GREEN", "emoji": "🟢", "label_ko": "도착 완료"}

    if actual_off or actual_out:
        return {"level": "GREEN", "emoji": "🟢", "label_ko": "운항 중"}

    return {"level": "GREEN", "emoji": "🟢", "label_ko": "출발 예정"}


def normalize_row(
    row: Dict[str, Any],
    config: Dict[str, str],
) -> Dict[str, Any]:

    dep_iata = config["dep_iata"]
    arr_iata = config["arr_iata"]

    scheduled_dep = row.get("scheduled_out") or row.get("scheduled_off")
    estimated_dep = row.get("estimated_out") or row.get("estimated_off")
    actual_dep = row.get("actual_out") or row.get("actual_off")

    scheduled_arr = row.get("scheduled_in") or row.get("scheduled_on")
    estimated_arr = row.get("estimated_in") or row.get("estimated_on")
    actual_arr = row.get("actual_in") or row.get("actual_on")

    dep_delay = minutes_between(scheduled_dep, actual_dep or estimated_dep)
    arr_delay = minutes_between(scheduled_arr, actual_arr or estimated_arr)

    dep_date = local_date(
        scheduled_dep or estimated_dep or actual_dep,
        dep_iata,
    )

    return {
        "flight_iata": config["flight_iata"],
        "flightaware_ident": row.get("ident") or config["fa_ident"],
        "fa_flight_id": row.get("fa_flight_id"),
        "group_ko": config["group_ko"],
        "route": f"{dep_iata} → {arr_iata}",
        "dep_iata": dep_iata,
        "arr_iata": arr_iata,
        "expected_departure_local": config["expected_departure_local"],
        "selected_flight_date": dep_date,
        "selected_date_distance_days": date_distance_days(
            dep_date,
            today_local(dep_iata),
        ),
        "selected_scheduled_clock": local_clock(scheduled_dep, dep_iata),
        "schedule_match_difference_minutes": circular_clock_difference_minutes(
            local_clock(scheduled_dep, dep_iata),
            config["expected_departure_local"],
        ),
        "found": True,
        "status": status_from_row(row),
        "cancelled": bool(row.get("cancelled")),
        "diverted": bool(row.get("diverted")),
        "departure": {
            "timezone_label_ko": AIRPORTS[dep_iata]["timezone_label_ko"],
            "scheduled_utc": scheduled_dep,
            "estimated_utc": estimated_dep,
            "actual_utc": actual_dep,
            "scheduled_local": to_local_iso(scheduled_dep, dep_iata),
            "estimated_local": to_local_iso(estimated_dep, dep_iata),
            "actual_local": to_local_iso(actual_dep, dep_iata),
            "calculated_delay_minutes": dep_delay,
            "gate": row.get("gate_origin"),
            "terminal": row.get("terminal_origin"),
        },
        "arrival": {
            "timezone_label_ko": AIRPORTS[arr_iata]["timezone_label_ko"],
            "scheduled_utc": scheduled_arr,
            "estimated_utc": estimated_arr,
            "actual_utc": actual_arr,
            "scheduled_local": to_local_iso(scheduled_arr, arr_iata),
            "estimated_local": to_local_iso(estimated_arr, arr_iata),
            "actual_local": to_local_iso(actual_arr, arr_iata),
            "calculated_delay_minutes": arr_delay,
            "gate": row.get("gate_destination"),
            "terminal": row.get("terminal_destination"),
        },
        "aircraft_type": row.get("aircraft_type"),
        "registration": row.get("registration"),
    }


def fetch_ident(
    api_key: str,
    ident: str,
) -> Dict[str, Any]:

    encoded = urllib.parse.quote(ident, safe="")
    url = f"{API_BASE}/flights/{encoded}"

    req = urllib.request.Request(
        url,
        headers={
            "x-apikey": api_key,
            "Accept": "application/json",
            "User-Agent": "sblc-typhoon-dashboard-flightaware/1.0",
        },
    )

    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_one(
    api_key: str,
    config: Dict[str, str],
) -> Dict[str, Any]:

    payload = fetch_ident(api_key, config["fa_ident"])
    rows = payload.get("flights", [])

    if not isinstance(rows, list) or not rows:
        return {
            "flight_iata": config["flight_iata"],
            "flightaware_ident": config["fa_ident"],
            "group_ko": config["group_ko"],
            "route": f"{config['dep_iata']} → {config['arr_iata']}",
            "expected_departure_local": config["expected_departure_local"],
            "found": False,
            "status": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "조회 결과 없음",
            },
        }

    selected = choose_best_candidate(rows, config)

    if not selected:
        return {
            "flight_iata": config["flight_iata"],
            "flightaware_ident": config["fa_ident"],
            "group_ko": config["group_ko"],
            "route": f"{config['dep_iata']} → {config['arr_iata']}",
            "expected_departure_local": config["expected_departure_local"],
            "found": False,
            "status": {
                "level": "NO_DATA",
                "emoji": "⚪",
                "label_ko": "노선 일치 결과 없음",
            },
            "candidate_count": len(rows),
        }

    return normalize_row(selected, config)


def main() -> int:
    print(f"FlightAware fetch version: {PARSER_VERSION}")

    api_key = os.environ.get("FLIGHTAWARE_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("FLIGHTAWARE_API_KEY secret is missing.")

    flights: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for config in FLIGHT_CONFIGS:
        print(
            f"Fetching {config['flight_iata']} "
            f"as {config['fa_ident']} "
            f"({config['dep_iata']}->{config['arr_iata']})"
        )

        try:
            flights.append(fetch_one(api_key, config))
        except Exception as exc:
            errors.append({
                "flight_iata": config["flight_iata"],
                "flightaware_ident": config["fa_ident"],
                "error": str(exc),
            })
            flights.append({
                "flight_iata": config["flight_iata"],
                "flightaware_ident": config["fa_ident"],
                "group_ko": config["group_ko"],
                "route": f"{config['dep_iata']} → {config['arr_iata']}",
                "expected_departure_local": config["expected_departure_local"],
                "found": False,
                "status": {
                    "level": "ERROR",
                    "emoji": "⚠️",
                    "label_ko": "조회 오류",
                },
                "error": str(exc),
            })

    output = {
        "source": "FlightAware AeroAPI",
        "product": "7 Representative Flights",
        "parser_version": PARSER_VERSION,
        "tracked_flights": [
            c["flight_iata"] for c in FLIGHT_CONFIGS
        ],
        "flightaware_idents": {
            c["flight_iata"]: c["fa_ident"]
            for c in FLIGHT_CONFIGS
        },
        "flights": flights,
        "errors": errors,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Updated: {OUTPUT_PATH}")
    print(f"Flights: {len(flights)}")
    print(f"Errors: {len(errors)}")

    # Keep output available for diagnosis even when some individual lookups fail.
    return 1 if len(errors) == len(FLIGHT_CONFIGS) else 0


if __name__ == "__main__":
    sys.exit(main())
