#!/usr/bin/env python3
"""Fetch conflict-related RSS headlines and normalize into event records."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
EVENTS_PATH = DATA_DIR / "events.json"

MAX_ITEMS_PER_FEED = 40
REQUEST_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    source_weight: float


FEEDS: List[Feed] = [
    Feed(
        name="Google News - US Iran conflict",
        url=(
            "https://news.google.com/rss/search"
            "?q=US+Iran+conflict&hl=en-US&gl=US&ceid=US:en"
        ),
        source_weight=0.55,
    ),
    Feed(
        name="Google News - US Iran strikes",
        url=(
            "https://news.google.com/rss/search"
            "?q=U.S.+Iran+strikes&hl=en-US&gl=US&ceid=US:en"
        ),
        source_weight=0.55,
    ),
    Feed(
        name="Google News - Iran response US",
        url=(
            "https://news.google.com/rss/search"
            "?q=Iran+response+to+US+military&hl=en-US&gl=US&ceid=US:en"
        ),
        source_weight=0.55,
    ),
]


USA_KEYWORDS = (
    "u.s.",
    "united states",
    "american",
    "pentagon",
    "washington",
    "us military",
    "us navy",
    "us air force",
)

IRAN_KEYWORDS = (
    "iran",
    "iranian",
    "tehran",
    "irgc",
    "revolutionary guard",
)

USA_ALLY_KEYWORDS = (
    "israel",
    "israeli",
    "idf",
)

ATTACK_WORDS = ("strike", "strikes", "attack", "attacks", "hits", "bombs", "launches")
INTERCEPT_WORDS = ("intercept", "intercepts", "thwarts", "foils", "shoots down")
LOSS_WORDS = ("destroyed", "killed", "losses", "downed", "sunk")
DIPLO_WORDS = ("sanction", "talks", "ceasefire", "resolution", "warning", "condemn")

ACTION_PATTERN = (
    r"(strike|strikes|struck|attack|attacks|hit|hits|bomb|bombs|launches|launched|"
    r"intercept|intercepts|intercepted|thwart|thwarts|thwarted|foil|foils|foiled|"
    r"shoots down|shot down|sink|sank|sunk|destroyed|downed)"
)
USA_PATTERN = (
    r"(u\.s\.|united states|american|us military|us navy|us air force|"
    r"pentagon|washington|israel|israeli|idf)"
)
IRAN_PATTERN = r"(iran|iranian|tehran|irgc|revolutionary guard)"
TARGET_IRAN_PATTERN = r"(iran|iranian|tehran|irgc)"
TARGET_USA_PATTERN = r"(u\.s\.|united states|american|washington|pentagon)"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_pub_date(raw: Optional[str]) -> str:
    if not raw:
        return now_iso()
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return now_iso()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def canonical_id(title: str, published_at: str) -> str:
    key = f"{title.lower()}|{published_at[:10]}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def side_mentioned(text: str, keywords: tuple[str, ...]) -> bool:
    return any(word in text for word in keywords)


def determine_event_type(text: str) -> str:
    if any(word in text for word in INTERCEPT_WORDS):
        return "intercept"
    if any(word in text for word in ATTACK_WORDS):
        return "attack"
    if any(word in text for word in LOSS_WORDS):
        return "material_loss"
    if any(word in text for word in DIPLO_WORDS):
        return "diplomatic"
    return "other"


def detect_actor_target(text: str) -> tuple[str, str]:
    # Explicit directional phrasing first.
    directional_regexes = [
        (rf"{USA_PATTERN}.{{0,90}}{ACTION_PATTERN}.{{0,90}}{IRAN_PATTERN}", "usa", "iran"),
        (rf"{IRAN_PATTERN}.{{0,90}}{ACTION_PATTERN}.{{0,90}}{USA_PATTERN}", "iran", "usa"),
        (
            rf"{USA_PATTERN}.{{0,70}}(strike|strikes|attack|attacks|bomb|bombs|launches|launched)"
            rf".{{0,40}}(in|on|against)\s+(the\s+)?{TARGET_IRAN_PATTERN}",
            "usa",
            "iran",
        ),
        (
            rf"{IRAN_PATTERN}.{{0,70}}(strike|strikes|attack|attacks|bomb|bombs|launches|launched)"
            rf".{{0,40}}(in|on|against)\s+(the\s+)?{TARGET_USA_PATTERN}",
            "iran",
            "usa",
        ),
    ]
    for regex, actor, target in directional_regexes:
        if re.search(regex, text):
            return actor, target

    # Loss phrasing where only target is explicit.
    if re.search(rf"{TARGET_IRAN_PATTERN}.{{0,30}}(sunk|destroyed|downed|losses|killed)", text):
        return "usa", "iran"
    if re.search(rf"{TARGET_USA_PATTERN}.{{0,30}}(sunk|destroyed|downed|losses|killed)", text):
        return "iran", "usa"

    # Diplomatic headlines can still have a single-sided actor.
    if determine_event_type(text) == "diplomatic":
        if side_mentioned(text, USA_KEYWORDS) or side_mentioned(text, USA_ALLY_KEYWORDS):
            return "usa", "unknown"
        if side_mentioned(text, IRAN_KEYWORDS):
            return "iran", "unknown"

    return "unknown", "unknown"


def score_event(text: str, actor: str, target: str, event_type: str) -> Dict[str, int]:
    points = {"usa": 0, "iran": 0}

    if event_type == "attack" and actor in ("usa", "iran"):
        points[actor] += 3
    elif event_type == "intercept" and actor in ("usa", "iran"):
        points[actor] += 2
    elif event_type == "material_loss" and target in ("usa", "iran"):
        attacker = "usa" if target == "iran" else "iran"
        points[attacker] += 4
    elif event_type == "diplomatic" and actor in ("usa", "iran"):
        points[actor] += 1
    return points


def confidence_for_event(text: str, base_weight: float) -> float:
    confidence = base_weight
    if side_mentioned(text, USA_KEYWORDS) and side_mentioned(text, IRAN_KEYWORDS):
        confidence += 0.15
    if determine_event_type(text) != "other":
        confidence += 0.1
    return round(min(confidence, 0.95), 2)


def fetch_feed(feed: Feed) -> List[Dict[str, str]]:
    req = urllib.request.Request(
        feed.url,
        headers={"User-Agent": "war-scoreboard-bot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        data = response.read()

    root = ET.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        return []

    items = []
    for item in channel.findall("item")[:MAX_ITEMS_PER_FEED]:
        title = normalize_text(item.findtext("title", default=""))
        link = normalize_text(item.findtext("link", default=""))
        pub_date = parse_pub_date(item.findtext("pubDate"))
        source_name = normalize_text(item.findtext("source", default=feed.name))
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "url": link,
                "published_at": pub_date,
                "source_name": source_name,
            }
        )
    return items


def build_events() -> List[Dict[str, object]]:
    events_by_id: Dict[str, Dict[str, object]] = {}

    for feed in FEEDS:
        try:
            feed_items = fetch_feed(feed)
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            print(f"[warn] failed feed: {feed.name} ({exc})", file=sys.stderr)
            continue

        for item in feed_items:
            text = item["title"].lower()
            event_type = determine_event_type(text)
            actor, target = detect_actor_target(text)
            points = score_event(text, actor, target, event_type)
            confidence = confidence_for_event(text, feed.source_weight)
            event_id = canonical_id(item["title"], item["published_at"])

            existing = events_by_id.get(event_id)
            if existing:
                existing["feed_mentions"] = int(existing["feed_mentions"]) + 1
                if feed.name not in existing["feeds"]:
                    existing["feeds"].append(feed.name)
                existing["confidence"] = round(
                    min(float(existing["confidence"]) + 0.05, 0.99), 2
                )
                continue

            events_by_id[event_id] = {
                "id": event_id,
                "title": item["title"],
                "url": item["url"],
                "published_at": item["published_at"],
                "source_name": item["source_name"],
                "feeds": [feed.name],
                "feed_mentions": 1,
                "event_type": event_type,
                "actor": actor,
                "target": target,
                "points": points,
                "confidence": confidence,
                "fetched_at": now_iso(),
            }

    events = list(events_by_id.values())
    events.sort(key=lambda event: event["published_at"], reverse=True)
    return events


def write_events(events: List[Dict[str, object]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_iso(),
        "event_count": len(events),
        "events": events,
    }
    EVENTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    events = build_events()
    write_events(events)
    print(f"[ok] wrote {len(events)} events to {EVENTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
