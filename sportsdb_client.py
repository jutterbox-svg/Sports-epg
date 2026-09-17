"""
Minimal client for the TheSportsDB v1 JSON API.

Endpoints used:
  - search_all_teams.php?l=<league name>   -> all teams in a league
  - eventsnext.php?id=<team id>            -> team's next 5 scheduled events
  - eventslast.php?id=<team id>            -> team's last 5 played events

Free-tier notes:
  - Shared free key is heavily rate-limited and occasionally flaky.
  - We add a small delay between calls and retry transient failures.
  - Any single team failing should never take down the whole run, so
    every public method here returns [] on unrecoverable error rather
    than raising.
"""
import logging
import time

import requests

from config.settings import SPORTSDB_BASE_URL, API_REQUEST_DELAY_SECONDS

log = logging.getLogger("sportsdb_client")

_session = requests.Session()
_session.headers.update({"User-Agent": "sports-epg-generator/1.0"})


def _get(path: str, params: dict, retries: int = 3, timeout: int = 15) -> dict | None:
    url = f"{SPORTSDB_BASE_URL}/{path}"
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
            time.sleep(API_REQUEST_DELAY_SECONDS)
            if resp.status_code == 429:
                wait = 3 * attempt
                log.warning("Rate limited on %s, waiting %ss", path, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Request to %s failed (attempt %d/%d): %s", path, attempt, retries, exc)
            time.sleep(1.5 * attempt)
    log.error("Giving up on %s after %d attempts", path, retries)
    return None


def get_teams_in_league(league_name: str) -> list[dict]:
    """Return all teams TheSportsDB has for the given league name."""
    data = _get("search_all_teams.php", {"l": league_name})
    if not data:
        return []
    teams = data.get("teams") or []
    # Keep only actual soccer teams with a usable ID.
    return [t for t in teams if t.get("idTeam") and t.get("strSport") == "Soccer"]


def get_next_events(team_id: str) -> list[dict]:
    """Return the team's upcoming scheduled fixtures, soonest first."""
    data = _get("eventsnext.php", {"id": team_id})
    if not data:
        return []
    events = data.get("events") or []
    return sorted(events, key=lambda e: (e.get("dateEvent") or "9999", e.get("strTime") or "99:99:99"))


def get_last_events(team_id: str) -> list[dict]:
    """Return the team's most recently played fixtures, most recent first."""
    data = _get("eventslast.php", {"id": team_id})
    if not data:
        return []
    events = data.get("results") or data.get("events") or []
    return sorted(events, key=lambda e: (e.get("dateEvent") or "", e.get("strTime") or ""), reverse=True)
