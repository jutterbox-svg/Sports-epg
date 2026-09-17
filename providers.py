"""
Abstracted provider interface.

Routes team-fetching and event-fetching to either:
  - espn_client: Free, unauthenticated ESPN site API for eng.1 - eng.4.
  - sportsdb_client: Free key "123" for National League (eng.5).
"""
from __future__ import annotations

import logging
from typing import Any

import espn_client
import sportsdb_client
from leagues import LEAGUES

log = logging.getLogger("providers")


def get_teams_for_league(league_name: str) -> list[dict]:
    """
    Fetch all teams for a given league.
    Returns normalized team dicts:
      {"idTeam": str, "strTeam": str, "strTeamBadge": str | None}
    """
    league_cfg = LEAGUES.get(league_name)
    if not league_cfg:
        log.error("Unknown league requested: %s", league_name)
        return []

    provider = league_cfg.get("provider")

    if provider == "espn":
        code = league_cfg["league_code"]
        log.info("Fetching teams for %s via ESPN (%s)", league_name, code)
        return espn_client.get_teams_in_league(code)

    elif provider == "sportsdb":
        sdb_name = league_cfg["name"]
        log.info("Fetching teams for %s via TheSportsDB (%s)", league_name, sdb_name)
        return sportsdb_client.get_teams_in_league(sdb_name)

    else:
        log.error("Unsupported provider '%s' for league %s", provider, league_name)
        return []


def get_events_for_team(team_id: str, league_name: str) -> dict[str, list[dict]]:
    """
    Fetch upcoming (next) and past (last) events for a team.
    Returns:
      {
        "next": [ ... normalized event dicts ... ],
        "last": [ ... normalized event dicts ... ]
      }
    """
    league_cfg = LEAGUES.get(league_name)
    if not league_cfg:
        log.error("Unknown league requested: %s", league_name)
        return {"next": [], "last": []}

    provider = league_cfg.get("provider")

    if provider == "espn":
        code = league_cfg["league_code"]
        log.info("Fetching schedule for team %s via ESPN (%s)", team_id, code)
        raw_events = espn_client.get_team_schedule(team_id, code)

        # Separate ESPN events into "next" and "last" based on the internal _status flag
        # _status from ESPN: "pre" (upcoming), "in" (live), "post" (completed)
        next_events = []
        last_events = []

        for evt in raw_events:
            status = evt.get("_status")
            if status in ("pre", "in"):
                next_events.append(evt)
            elif status == "post":
                last_events.append(evt)
            else:
                # Default fallback if status isn't clear
                next_events.append(evt)

        return {"next": next_events, "last": last_events}

    elif provider == "sportsdb":
        log.info("Fetching schedule for team %s via TheSportsDB", team_id)
        next_evts = sportsdb_client.get_next_events_for_team(team_id)
        last_evts = sportsdb_client.get_last_events_for_team(team_id)
        return {"next": next_evts, "last": last_evts}

    else:
        log.error("Unsupported provider '%s' for league %s", provider, league_name)
        return {"next": [], "last": []}
        
