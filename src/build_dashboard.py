#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SBLC Typhoon Dashboard - Step 8.1
Build one dashboard summary JSON

Inputs:
    data/jma_typhoon.json
    data/typhoon_compare.json
    data/typhoon_risk.json
    data/flights.json

Output:
    data/dashboard.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]

JMA_PATH = BASE_DIR / "data" / "jma_typhoon.json"
COMPARE_PATH = BASE_DIR / "data" / "typhoon_compare.json"
RISK_PATH = BASE_DIR / "data" / "typhoon_risk.json"
FLIGHTS_PATH = BASE_DIR / "data" / "flights.json"
OUTPUT_PATH = BASE_DIR / "data" / "dashboard.json"

PARSER_VERSION = "8.71.1-T20-NO-FLIGHT"
TARGET_TYPHOON_NUMBER = "2618"
TARGET_TYPHOON_NAME = "SAUDEL"
LOCATION_ORDER = ["SUZHOU", "PVG", "ICN", "MNL", "HAN", "CRK"]
REPRESENTATIVE_FLIGHTS = ["KE249", "KE335", "PR337", "KJ948", "KJ988"]
LOCATION_NAME_OVERRIDES = {
    "PVG": "푸동 국제공항",
    "ICN": "인천 국제공항",
    "MNL": "마닐라 국제공항",
    "CRK": "클락 국제공항",
}


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def simplify_location(item: Dict[str, Any], code: str = "") -> Dict[str, Any]:
    weather = item.get("weather", {})
    typhoon = item.get("typhoon", {})
    risk = item.get("risk", {})

    return {
        "name_ko": LOCATION_NAME_OVERRIDES.get(code, item.get("name_ko")),
        "score": item.get("score"),
        "risk": {
            "emoji": risk.get("emoji"),
            "label_ko": risk.get("label_ko"),
        },
        "reason_ko": item.get("reason_ko"),
        "current_distance_km": typhoon.get("current_distance_km"),
        "closest_distance_km": typhoon.get("closest_distance_km"),
        "closest_time": typhoon.get("closest_time"),
        "closest_forecast_hour": typhoon.get("closest_forecast_hour"),
        "closest_source": typhoon.get("source"),
        "trend_ko": typhoon.get("trend_ko"),
        "current_weather": {
            "source": weather.get("source") or "WeatherAPI.com",
            "last_updated": weather.get("current_last_updated"),
            "rain_mm": weather.get("current_rain_mm"),
            "wind_mps": weather.get("current_wind_mps"),
        },
        "forecast_72h": {
            "max_rain_mm": weather.get("max_72h_rain_mm"),
            "max_rain_time": weather.get("max_72h_rain_time"),
            "max_wind_mps": weather.get("max_72h_wind_mps"),
            "max_wind_time": weather.get("max_72h_wind_time"),
        },
    }


def simplify_flight(item: Dict[str, Any]) -> Dict[str, Any]:
    dep = item.get("departure", {})
    arr = item.get("arrival", {})
    status = item.get("status", {})

    def display_label(event: Dict[str, Any], kind: str) -> str:
        if event.get("actual_local"):
            return f"실제 {kind}"
        if event.get("estimated_local"):
            return f"예상 {kind}"
        return f"예정 {kind}"

    return {
        "flight_iata": item.get("flight_iata"),
        "route": item.get("route"),
        "status": {
            "level": status.get("level"),
            "emoji": status.get("emoji"),
            "label_ko": status.get("label_ko"),
        },
        "departure": {
            "scheduled_local": dep.get("scheduled_local"),
            "estimated_local": dep.get("estimated_local"),
            "actual_local": dep.get("actual_local"),
            "display_time_local": dep.get("display_time_local"),
            "display_label_ko": display_label(dep, "출발"),
            "delay_minutes": dep.get("calculated_delay_minutes"),
            "timezone_label_ko": dep.get("timezone_label_ko"),
        },
        "arrival": {
            "scheduled_local": arr.get("scheduled_local"),
            "estimated_local": arr.get("estimated_local"),
            "actual_local": arr.get("actual_local"),
            "display_time_local": arr.get("display_time_local"),
            "display_label_ko": display_label(arr, "도착"),
            "delay_minutes": arr.get("calculated_delay_minutes"),
            "timezone_label_ko": arr.get("timezone_label_ko"),
        },
    }


def get_typhoon_track(jma: Dict[str, Any]) -> Dict[str, Any]:
    typhoons = jma.get("typhoons", [])

    if not isinstance(typhoons, list) or not typhoons:
        return {}

    # Dashboard is locked to 2618 SAUDEL.
    # Never display another named typhoon or tropical depression.
    item = next(
        (
            t for t in typhoons
            if isinstance(t, dict)
            and isinstance(t.get("typhoon"), dict)
            and str(t.get("typhoon", {}).get("number") or "").strip()
                == TARGET_TYPHOON_NUMBER
            and str(t.get("typhoon", {}).get("name") or "").strip().upper()
                == TARGET_TYPHOON_NAME
        ),
        None,
    )

    if item is None:
        return {}

    meta = item.get("typhoon", {})
    analysis = item.get("analysis", {}) or {}

    forecast_points: List[Dict[str, Any]] = []

    for p in item.get("forecast", []):
        if not isinstance(p, dict):
            continue

        forecast_points.append({
            "forecast_hour": p.get("forecast_hour"),
            "time": p.get("time"),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
            "pressure_hpa": p.get("pressure_hpa"),
            "max_wind_mps": p.get("max_wind_mps"),
            "movement_direction": p.get("movement_direction"),
        })

    return {
        "number": meta.get("number"),
        "name": meta.get("name"),
        "current": {
            "time": analysis.get("time"),
            "lat": analysis.get("lat"),
            "lon": analysis.get("lon"),
            "pressure_hpa": analysis.get("pressure_hpa"),
            "max_wind_mps": analysis.get("max_wind_mps"),
            "movement_direction": analysis.get("movement_direction"),
            "movement_speed_kmh": analysis.get("movement_speed_kmh"),
        },
        "forecast_track": forecast_points,
    }


def main() -> int:
    jma = load_json(JMA_PATH)
    compare = load_json(COMPARE_PATH)
    risk = load_json(RISK_PATH)
    flights = {}

    # v8.71.1 TYPHOON-20: flight module disabled (operation dashboard only)
    flight_summaries = []

    typhoon_summary = get_typhoon_track(jma)

    output = {
        "source": "SBLC Typhoon Dashboard",
        "product": "Dashboard Summary",
        "parser_version": PARSER_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),

        # Source-specific refresh times.
        # These remain tied to the source JSON itself, so rebuilding the
        # dashboard for flights does not falsely make JMA look newer.
        "source_updated_at_utc": {
            "jma": jma.get("generated_at_utc"),
            "weather": (
                risk.get("source_updated_at_utc", {}).get("weather")
                if isinstance(risk.get("source_updated_at_utc"), dict)
                else None
            ),
            "risk": risk.get("generated_at_utc"),
            "flights": flights.get("generated_at_utc"),
        },

        "risk_meta": {
            "typhoon_reference_time": (
                typhoon_summary.get("current", {}).get("time")
                if isinstance(typhoon_summary.get("current"), dict)
                else None
            ),
            "jma_updated_at_utc": jma.get("generated_at_utc"),
            "weather_updated_at_utc": (
                risk.get("source_updated_at_utc", {}).get("weather")
                if isinstance(risk.get("source_updated_at_utc"), dict)
                else None
            ),
            "impact_updated_at_utc": (
                risk.get("source_updated_at_utc", {}).get("impact")
                if isinstance(risk.get("source_updated_at_utc"), dict)
                else None
            ),
            "risk_updated_at_utc": risk.get("generated_at_utc"),
            "risk_version": risk.get("parser_version"),
        },

        "typhoon": typhoon_summary,
        "forecast_comparison": {
            "emoji": compare_overall.get("emoji", "⚪"),
            "label_ko": compare_overall.get("label_ko", "비교자료 없음"),
            "average_difference_km": compare_summary.get("average_difference_km"),
            "max_difference_km": compare_summary.get("max_difference_km"),
        },
        "locations": locations,
        "routes": routes,
        "flights": flight_summaries,
        "aviationstack_usage": flights.get("api_usage", {}),
        "attribution": [
            "Japan Meteorological Agency (JMA)",
            "Korea Meteorological Administration (KMA)",
            "Powered by WeatherAPI.com",
            "Aviationstack",
        ],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT_PATH.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Dashboard summary version: {PARSER_VERSION}")
    print(f"Updated: {OUTPUT_PATH}")
    print(f"Locations: {len(locations)}")
    print(f"Routes: {len(routes)}")
    print(f"Flights: {len(flight_summaries)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
