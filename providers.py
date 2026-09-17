"""
Dispatch layer so generator.py can fetch teams/fixtures for any league
the same way, regardless of which upstream API actually serves it.

Premier League and Championship go through football-data.org (a
dedicated 10 req/min quota, not shared with other users, cleaner
data). League One, League Two, and National League aren't available
on football-data.org's free tier at all, so they fall back to
TheSportsDB, same as before.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sportsdb_client
import football_data_client


def fetch_teams(league_cfg: dict) -> list[dict]:
    """Returns normalized team dicts: idTeam, strTeam, strTeamBadge."""
    if league_cfg["provider"] == "football_data":
        return football_data_client.get_teams_in_competition(league_cfg["code"])
    return sportsdb_client.get_teams_in_league(league_cfg["name"])


def _parse_dt(event: dict) -> datetime | None:
    ts = event.get("strTimestamp")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_fixtures(league_cfg: dict, team_id: str) -> tuple[list[dict], list[dict]]:
    """Returns (next_events, last_events), each a list of 0 or 1 event
    dicts in the same shape xmltv_builder.TeamChannel.build_programmes
    already expects - so xmltv_builder.py needs no changes at all."""
    if league_cfg["provider"] == "sportsdb":
        return sportsdb_client.get_next_events(team_id), sportsdb_client.get_last_events(team_id)

    # football_data: one call returns a window of matches; split it into
    # "next scheduled" and "most recent played/live" ourselves.
    matches = football_data_client.get_team_matches(team_id)
    now = datetime.now(timezone.utc)

    upcoming, played = [], []
    for m in matches:
        dt = _parse_dt(m)
        if dt is None:
            continue
        status = m.get("_status")
        if status in ("FINISHED", "IN_PLAY", "PAUSED") or (status not in ("SCHEDULED", "TIMED") and dt <= now):
            played.append((dt, m))
        else:
            upcoming.append((dt, m))

    upcoming.sort(key=lambda pair: pair[0])
    played.sort(key=lambda pair: pair[0], reverse=True)

    next_events = [m for _, m in upcoming[:1]]
    last_events = [m for _, m in played[:1]]
    return next_events, last_events
