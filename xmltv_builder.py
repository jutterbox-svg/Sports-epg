"""
Turns raw TheSportsDB event dicts into XMLTV <channel>/<programme> elements.

Each team becomes one XMLTV "channel". It always has one or two
"programmes" covering the guide window with no gaps:

  - If a match is currently live (kicked off within the last
    MATCH_DURATION_MINUTES): programme 1 = the live match,
    programme 2 = the next scheduled fixture (if known).
  - Otherwise: programme 1 = a filler "Now" card that runs from the
    generation time up to the next kick-off (titled with the next
    fixture so EPG viewers see it as "up next" info), programme 2 =
    the next fixture itself, spanning kickoff -> kickoff+duration.
  - If there is no known next fixture at all: a single filler
    programme is emitted so the channel is never empty.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element, SubElement

from config.settings import MATCH_DURATION_MINUTES

XMLTV_FMT = "%Y%m%d%H%M%S %z"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "team"


def _parse_event_datetime(event: dict) -> datetime | None:
    """TheSportsDB gives strTimestamp (ISO8601 UTC) on most events;
    fall back to combining dateEvent + strTime (also UTC) if missing."""
    ts = event.get("strTimestamp")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    date_part = event.get("dateEvent")
    time_part = event.get("strTime") or "00:00:00"
    if not date_part:
        return None
    try:
        dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fmt(dt: datetime) -> str:
    return dt.strftime(XMLTV_FMT)


def _fixture_title(event: dict) -> str:
    home = event.get("strHomeTeam", "?")
    away = event.get("strAwayTeam", "?")
    return f"{home} vs {away}"


def _fixture_desc(event: dict) -> str:
    bits = []
    comp = event.get("strLeague")
    venue = event.get("strVenue")
    if comp:
        bits.append(comp)
    if venue:
        bits.append(f"Venue: {venue}")
    round_no = event.get("intRound")
    if round_no:
        bits.append(f"Round {round_no}")
    return " | ".join(bits) if bits else "Football fixture"


class TeamChannel:
    """Holds everything needed to render one team's <channel> + programmes."""

    def __init__(self, team: dict, league_display_name: str):
        self.team_id = team.get("idTeam")
        self.team_name = team.get("strTeam") or "Unknown Team"
        self.league_display_name = league_display_name
        self.badge = team.get("strTeamBadge") or team.get("strBadge")
        self.slug = _slugify(f"{league_display_name}-{self.team_name}")
        self.channel_id = f"{self.slug}.sports-epg"

    def build_programmes(self, next_events: list[dict], last_events: list[dict], now: datetime):
        """Returns a list of programme dicts: [{start, stop, title, desc, category}, ...]"""
        duration = timedelta(minutes=MATCH_DURATION_MINUTES)
        next_event = next_events[0] if next_events else None
        last_event = last_events[0] if last_events else None

        next_dt = _parse_event_datetime(next_event) if next_event else None
        last_dt = _parse_event_datetime(last_event) if last_event else None

        is_live = bool(last_dt and last_dt <= now <= last_dt + duration)

        programmes = []

        if is_live:
            programmes.append({
                "start": last_dt,
                "stop": last_dt + duration,
                "title": f"LIVE: {_fixture_title(last_event)}",
                "desc": _fixture_desc(last_event),
                "category": "Football",
            })
            if next_dt:
                programmes.append({
                    "start": last_dt + duration,
                    "stop": next_dt + duration,
                    "title": _fixture_title(next_event),
                    "desc": _fixture_desc(next_event),
                    "category": "Football",
                })
            return programmes

        if next_dt:
            now_stop = max(next_dt, now + timedelta(minutes=1))
            programmes.append({
                "start": now,
                "stop": now_stop,
                "title": f"Next up: {_fixture_title(next_event)}",
                "desc": f"Kick-off {next_dt.strftime('%a %d %b, %H:%M UTC')}. {_fixture_desc(next_event)}",
                "category": "Football",
            })
            programmes.append({
                "start": now_stop,
                "stop": now_stop + duration,
                "title": _fixture_title(next_event),
                "desc": _fixture_desc(next_event),
                "category": "Football",
            })
        else:
            programmes.append({
                "start": now,
                "stop": now + timedelta(hours=24),
                "title": f"{self.team_name}: No fixture currently scheduled",
                "desc": "Check back later for upcoming fixtures.",
                "category": "Football",
            })

        return programmes

    def to_channel_element(self) -> Element:
        chan = Element("channel", {"id": self.channel_id})
        display_name = SubElement(chan, "display-name")
        display_name.text = f"{self.team_name} ({self.league_display_name})"
        if self.badge:
            icon = SubElement(chan, "icon")
            icon.set("src", self.badge)
        return chan

    def to_programme_elements(self, programmes: list[dict]) -> list[Element]:
        elements = []
        for p in programmes:
            if p["stop"] <= p["start"]:
                continue
            prog = Element("programme", {
                "start": _fmt(p["start"]),
                "stop": _fmt(p["stop"]),
                "channel": self.channel_id,
            })
            title_el = SubElement(prog, "title")
            title_el.text = p["title"]
            desc_el = SubElement(prog, "desc")
            desc_el.text = p["desc"]
            category_el = SubElement(prog, "category")
            category_el.text = p["category"]
            elements.append(prog)
        return elements
