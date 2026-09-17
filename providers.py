"""
Dispatch layer so generator.py can fetch teams/fixtures for any league
the same way, regardless of which upstream API actually serves it.

Premier League through League Two go through ESPN's free public API
(generous rate limit, no key). National League isn't reliably covered
by ESPN, so it falls back to TheSportsDB.

football_data_client.py is kept in the repo but no longer referenced
by any league in leagues.py - ESPN covers everything it used to plus
League One/Two, which football-data.org's free tier never covered.
Left in place in case you want a provider to fall back to later.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sportsdb_client
import espn_client


def fetch_teams(league_cfg: dict) -> list[dict]:
    """Returns normalized team dicts: idTeam, strTeam, strTeamBadge."""
    if league_cfg["provider"] == "espn":
        return espn_client.get_teams_in_league(league_cfg["league_code"])
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


def _split_by_status(events: list[dict], live_states: tuple, finished_states: tuple):
    """Shared helper: bucket normalized events (each with a "_status" key)
    into (next_events, last_events), each 0 or 1 items, using the
    provider's own status vocabulary passed in by the caller."""
    now = datetime.now(timezone.utc)
    upcoming, played = [], []
    for e in events:
        dt = _parse_dt(e)
        if dt is None:
            continue
        status = e.get("_status")
        if status in live_states or status in finished_states or dt <= now:
            played.append((dt, e))
        else:
            upcoming.append((dt, e))

    upcoming.sort(key=lambda pair: pair[0])
    played.sort(key=lambda pair: pair[0], reverse=True)
    return [e for _, e in upcoming[:1]], [e for _, e in played[:1]]


def fetch_fixtures(league_cfg: dict, team_id: str) -> tuple[list[dict], list[dict]]:
    """Returns (next_events, last_events), each a list of 0 or 1 event
    dicts in the same shape xmltv_builder.TeamChannel.build_programmes
    already expects - so xmltv_builder.py needs no changes at all."""
    if league_cfg["provider"] == "sportsdb":
        return sportsdb_client.get_next_events(team_id), sportsdb_client.get_last_events(team_id)

    # espn: one call returns the whole schedule; split it ourselves
    # using ESPN's status.type.state vocabulary ("pre"/"in"/"post").
    events = espn_client.get_team_schedule(team_id, league_cfg["league_code"])
    return _split_by_status(events, live_states=("in",), finished_states=("post",))
