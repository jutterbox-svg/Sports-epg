"""
Minimal client for the football-data.org v4 API.

Free-tier notes:
  - 10 requests/minute, shared across your whole API key (not just this
    app) - we throttle every call to stay safely under that.
  - Coverage on the free tier is a fixed set of ~12 competitions. For
    English football that's Premier League and Championship only -
    League One, League Two and National League are NOT available here,
    which is why providers.py falls back to TheSportsDB for those three.
  - On a 429, the API returns a Retry-After header telling us how long
    to back off; we honour it.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from settings import FOOTBALL_DATA_API_KEY, FOOTBALL_DATA_BASE_URL, FOOTBALL_DATA_REQUEST_DELAY_SECONDS

log = logging.getLogger("football_data_client")

_session = requests.Session()
_session.headers.update({"X-Auth-Token": FOOTBALL_DATA_API_KEY})


def _get(path: str, params: dict | None = None, retries: int = 3, timeout: int = 15) -> dict | None:
    if not FOOTBALL_DATA_API_KEY:
        log.error("FOOTBALL_DATA_API_KEY is not set - skipping football-data.org call to %s", path)
        return None

    url = f"{FOOTBALL_DATA_BASE_URL}{path}"
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(url, params=params or {}, timeout=timeout)
            time.sleep(FOOTBALL_DATA_REQUEST_DELAY_SECONDS)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                log.warning("football-data.org rate limited on %s, waiting %ss", path, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Request to %s failed (attempt %d/%d): %s", path, attempt, retries, exc)
            time.sleep(2 * attempt)
    log.error("Giving up on %s after %d attempts", path, retries)
    return None


def get_teams_in_competition(competition_code: str) -> list[dict]:
    """Return teams for a competition, normalized to the same shape
    TheSportsDB teams use (idTeam, strTeam, strTeamBadge) so the rest
    of the pipeline doesn't need to know which API a team came from."""
    data = _get(f"/competitions/{competition_code}/teams")
    if not data:
        return []
    teams = data.get("teams") or []
    return [
        {
            "idTeam": str(t["id"]),
            "strTeam": t.get("name") or t.get("shortName") or "Unknown",
            "strTeamBadge": t.get("crest"),
        }
        for t in teams if t.get("id")
    ]


def get_team_matches(team_id: str, days_back: int = 3, days_forward: int = 21) -> list[dict]:
    """Return this team's matches across a date window, normalized to the
    same event shape TheSportsDB uses (strHomeTeam/strAwayTeam/
    strTimestamp/strLeague/strVenue/intRound), plus an internal
    "_status" field providers.py uses to split into next/last."""
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_forward)).strftime("%Y-%m-%d")

    data = _get(f"/teams/{team_id}/matches", {"dateFrom": date_from, "dateTo": date_to})
    if not data:
        return []

    normalized = []
    for m in data.get("matches") or []:
        home = (m.get("homeTeam") or {}).get("name")
        away = (m.get("awayTeam") or {}).get("name")
        if not home or not away or not m.get("utcDate"):
            continue
        normalized.append({
            "strHomeTeam": home,
            "strAwayTeam": away,
            "strTimestamp": m.get("utcDate"),
            "strLeague": (m.get("competition") or {}).get("name"),
            "strVenue": m.get("venue") or "",
            "intRound": m.get("matchday"),
            "_status": m.get("status"),
        })
    return normalized
