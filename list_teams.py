#!/usr/bin/env python3
"""
List every team in each of a set of TheSportsDB leagues.

Uses lookup_all_teams.php?id=<league_id> - the correct endpoint for listing
*all* teams in a league. (lookupteam.php?id=<team_id> only returns a single
team you already know the ID for - useful once you have a team ID from here,
not for discovering them.)

Usage:
    python3 list_teams.py [--api-key 123] [--out teams.csv]
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://www.thesportsdb.com/api/v1/json"

# League name -> TheSportsDB league ID
LEAGUES = {
    "Premier League": "4328",
    "Championship": "4329",
    "EFL League One": "4396",
    "EFL League Two": "4397",
}


def fetch_json(url: str, timeout: int = 15, retries: int = 3, delay: float = 1.2):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sportsdb-teams/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            print(f"  request failed (attempt {attempt}/{retries}): {e}", file=sys.stderr)
            time.sleep(delay * attempt)
    print(f"  giving up on {url}: {last_err}", file=sys.stderr)
    return None


def list_teams_for_league(api_key: str, league_id: str, delay: float) -> list:
    url = f"{BASE_URL}/{api_key}/lookup_all_teams.php?id={league_id}"
    data = fetch_json(url)
    time.sleep(delay)
    if not data:
        return []
    return data.get("teams") or []


def generate_teams_csv(api_key: str = "123", out_path: str = "teams.csv", delay: float = 1.2) -> Path:
    """
    Fetch all teams for every league in LEAGUES and write them to a CSV.
    Returns the Path to the CSV. Importable by web_service.py.
    """
    rows = []
    for league_name, league_id in LEAGUES.items():
        print(f"Fetching teams for {league_name} (league id {league_id})...")
        teams = list_teams_for_league(api_key, league_id, delay)
        print(f"  -> {len(teams)} teams")
        for t in teams:
            rows.append({
                "league": league_name,
                "league_id": league_id,
                "team_id": t.get("idTeam"),
                "team_name": t.get("strTeam"),
                "stadium": t.get("strStadium"),
                "stadium_capacity": t.get("intStadiumCapacity"),
                "founded": t.get("intFormedYear"),
                "badge_url": t.get("strTeamBadge"),
            })

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "league", "league_id", "team_id", "team_name",
            "stadium", "stadium_capacity", "founded", "badge_url",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} teams across {len(LEAGUES)} leagues to {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="List all teams for a set of TheSportsDB leagues.")
    parser.add_argument("--api-key", default="123", help="TheSportsDB API key (default: free test key 123)")
    parser.add_argument("--out", default="teams.csv", help="Output CSV path")
    parser.add_argument("--delay", type=float, default=1.2, help="Delay between requests (seconds)")
    args = parser.parse_args()

    generate_teams_csv(args.api_key, args.out, args.delay)


if __name__ == "__main__":
    main()
