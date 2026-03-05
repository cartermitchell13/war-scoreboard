#!/usr/bin/env python3
"""Compute scoreboard JSON from normalized events."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
EVENTS_PATH = DATA_DIR / "events.json"
SCORE_PATH = DATA_DIR / "score.json"

WINDOW_DAYS = 30
MOMENTUM_DAYS = 7
HISTORY_DAYS = 14
LATEST_EVENTS = 20


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def load_events() -> List[Dict[str, object]]:
    if not EVENTS_PATH.exists():
        return []
    payload = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    return payload.get("events", [])


def build_daily_history(events: List[Dict[str, object]], days: int) -> List[Dict[str, object]]:
    today = now_utc().date()
    history: List[Dict[str, object]] = []

    for idx in range(days - 1, -1, -1):
        day = today - timedelta(days=idx)
        day_usa = 0
        day_iran = 0
        for event in events:
            event_day = parse_iso(str(event["published_at"])).date()
            if event_day != day:
                continue
            points = event.get("points", {})
            day_usa += int(points.get("usa", 0))
            day_iran += int(points.get("iran", 0))
        history.append({"date": day.isoformat(), "usa": day_usa, "iran": day_iran})
    return history


def summarize(events: List[Dict[str, object]]) -> Dict[str, object]:
    now = now_utc()
    window_cutoff = now - timedelta(days=WINDOW_DAYS)
    momentum_cutoff = now - timedelta(days=MOMENTUM_DAYS)

    in_window = [e for e in events if parse_iso(str(e["published_at"])) >= window_cutoff]
    in_momentum = [e for e in in_window if parse_iso(str(e["published_at"])) >= momentum_cutoff]

    usa_total = sum(int(e.get("points", {}).get("usa", 0)) for e in in_window)
    iran_total = sum(int(e.get("points", {}).get("iran", 0)) for e in in_window)
    usa_momentum = sum(int(e.get("points", {}).get("usa", 0)) for e in in_momentum)
    iran_momentum = sum(int(e.get("points", {}).get("iran", 0)) for e in in_momentum)

    confidence_values = [float(e.get("confidence", 0.5)) for e in in_window]
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0

    event_types = {}
    for e in in_window:
        event_type = str(e.get("event_type", "other"))
        event_types[event_type] = event_types.get(event_type, 0) + 1

    history = build_daily_history(in_window, HISTORY_DAYS)
    lead = usa_total - iran_total
    lead_side = "USA" if lead > 0 else "Iran" if lead < 0 else "Tied"

    latest_events = [
        {
            "id": e.get("id"),
            "published_at": e.get("published_at"),
            "title": e.get("title"),
            "url": e.get("url"),
            "source_name": e.get("source_name"),
            "event_type": e.get("event_type"),
            "actor": e.get("actor"),
            "target": e.get("target"),
            "points": e.get("points"),
            "confidence": e.get("confidence"),
        }
        for e in in_window[:LATEST_EVENTS]
    ]

    return {
        "generated_at": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "momentum_days": MOMENTUM_DAYS,
        "event_count_window": len(in_window),
        "totals": {
            "usa": usa_total,
            "iran": iran_total,
            "lead": lead,
            "lead_side": lead_side,
        },
        "momentum": {
            "usa": usa_momentum,
            "iran": iran_momentum,
            "delta": usa_momentum - iran_momentum,
        },
        "confidence": {
            "score": round(clamp(avg_confidence * 100.0, 0, 100), 1),
            "label": "High" if avg_confidence >= 0.75 else "Medium" if avg_confidence >= 0.5 else "Low",
        },
        "event_types": event_types,
        "history": history,
        "latest_events": latest_events,
    }


def write_score(payload: Dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCORE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    events = load_events()
    payload = summarize(events)
    write_score(payload)
    print(f"[ok] wrote score snapshot to {SCORE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
