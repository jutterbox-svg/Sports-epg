"""
Client for ESPN's free, public, unauthenticated site API.

This is the same undocumented API that powers espn.com itself. No key,
no signup, and per widely-reported experience its rate limit is
generous enough that DNS/network throttling is the usual bottleneck
long before ESPN itself pushes back. It is NOT an official/supported
API, so endpoints could change without notice - if it ever breaks,
the fix is updating the parsing in this file, not switching providers
again (though providers.py's abstraction makes that easy too).

Endpoints used (base: https://site.api.espn.com/apis/site/v2/sports/soccer):
  - /{league_code}/teams                   -> all teams in a league
  - /{league_code}/teams/{id}/schedule     -> that team's full schedule

English league codes: eng.1 (Premier League), eng.2 (Championship),
eng.3 (League One), eng.4 (League Two).
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("espn_client")

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# ESPN's rate limit isn't published, but is widely reported as generous.
# A small politeness delay is still worthwhile so we don't hammer it.
REQUEST_DELAY_SECONDS = 0.5

_session = requests.Session()
_session.headers.update({"User-Agent": "sports-epg-generator/1.0"})


def _get(path: str, params: dict | None = None, retries: int = 3, timeout: int = 15) -> dict | None:
    url = f"{BASE_URL}{path}"
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(url, params=params or {}, timeout=timeout)
            time.sleep(REQUEST_DELAY_SECONDS)
            if resp.status_code == 429:
                wait = 5 * attempt
                log.warning("ESPN rate limited on %s, waiting %ss", path, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Request to %s failed (attempt %d/%d): %s", path, attempt, retries, exc)
            time.sleep(1.5 * attempt)
    log.error("Giving up on %s after %d attempts", path, retries)
    return None


def get_teams_in_league(league_code: str) -> list[dict]:
    """Return teams for a league, normalized to idTeam/strTeam/strTeamBadge
    so the rest of the pipeline doesn't need to know which API served it."""
    data = _get(f"/{league_code}/teams", {"limit": 1000})
    if not data:
        return []

    teams_out = []
    for sport in data.get("sports") or []:
        for league in sport.get("leagues") or []:
            for entry in league.get("teams") or []:
                t = entry.get("team") or {}
                if not t.get("id"):
                    continue
                logos = t.get("logos") or []
                badge = logos[0]["href"] if logos else None
                teams_out.append({
                    "idTeam": str(t["id"]),
                    "strTeam": t.get("displayName") or t.get("name") or "Unknown",
                    "strTeamBadge": badge,
                })
    return teams_out


def get_team_schedule(team_id: str, league_code: str) -> list[dict]:
    """Return this team's full available schedule, normalized to the same
    event shape the other providers use (strHomeTeam/strAwayTeam/
    strTimestamp/strLeague/strVenue/intRound), plus an internal
    "_status" field ("pre"/"in"/"post") providers.py uses to split
    into next/last."""
    data = _get(f"/{league_code}/teams/{team_id}/schedule")
    if not data:
        return []

    league_name = ((data.get("team") or {}).get("league") or {}).get("name")

    normalized = []
    for event in data.get("events") or []:
        competitions = event.get("competitions") or []
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors") or []

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        home_name = (home.get("team") or {}).get("displayName")
        away_name = (away.get("team") or {}).get("displayName")
        if not home_name or not away_name:
            continue

        status_state = (((comp.get("status") or event.get("status") or {}).get("type") or {}).get("state"))
        venue = (comp.get("venue") or {}).get("fullName", "")

        normalized.append({
            "strHomeTeam": home_name,
            "strAwayTeam": away_name,
            "strTimestamp": event.get("date") or comp.get("date"),
            "strLeague": league_name,
            "strVenue": venue,
            "intRound": None,
            "_status": status_state,  # "pre" / "in" / "post"
        })
    return normalized
