#!/usr/bin/env python3
"""
API-Football -> per-TEAM XMLTV EPG generator.

Team schedules ONLY (no league-wide channels) - built for API-Football's
free tier (100 requests/day, 10/min). Designed to run once every 24h:
  - ~96 teams across Premier League/Championship/League One/League Two
    = ~96 fixture requests/day, comfortably under the 100/day cap.
  - Team lists (which don't change often) are cached and only re-fetched
    every `team_cache_days` (default 30), not every run.
  - League IDs not already known are resolved by name search once and
    cached, not looked up every run.
  - If the daily quota runs out mid-run (checked via the
    x-ratelimit-requests-remaining response header, with a safety margin),
    the run stops early gracefully. Teams not reached this run keep their
    last successfully-fetched fixtures (from a local cache) rather than
    going blank, so a partial run still produces a usable, if slightly
    stale, guide.

Usage:
    python3 apifootball_team_epg.py [--config apifootball_config.json] [--out-dir ./output]
"""

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement
from xml.dom import minidom

LOG = logging.getLogger("apifootball_team_epg")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class QuotaExhausted(Exception):
    pass


def build_headers(api_key: str, auth_style: str) -> dict:
    if auth_style == "rapidapi":
        return {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        }
    return {"x-apisports-key": api_key}


def fetch_json(url: str, headers: dict, timeout: int, max_retries: int, delay: float,
                safety_margin: int) -> dict | None:
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={**headers, "User-Agent": "apifootball-epg/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                remaining = resp.headers.get("x-ratelimit-requests-remaining")
                raw = resp.read()
            if remaining is not None:
                try:
                    if int(remaining) <= safety_margin:
                        LOG.warning("Only %s requests remaining today (safety margin %d) - "
                                    "stopping this run early.", remaining, safety_margin)
                        raise QuotaExhausted()
                except ValueError:
                    pass
            data = json.loads(raw.decode("utf-8"))
            errors = data.get("errors")
            if errors:
                LOG.warning("API returned errors for %s: %s", url, errors)
            return data
        except QuotaExhausted:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            LOG.warning("Request failed (attempt %d/%d) for %s: %s", attempt, max_retries, url, e)
            time.sleep(delay * attempt)
    LOG.error("Giving up on %s after %d attempts: %s", url, max_retries, last_err)
    return None


# --------------------------------------------------------------------------
# Season helper
# --------------------------------------------------------------------------

def current_season_year() -> int:
    """API-Football labels a season by its start year (e.g. Aug 2026-May 2027 = 2026)."""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


# --------------------------------------------------------------------------
# League ID resolution (cached)
# --------------------------------------------------------------------------

def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            LOG.warning("Cache file %s is corrupt - starting fresh.", path)
    return {}


def save_cache(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def resolve_league_id(name: str, base_url: str, headers: dict, cache: dict, timeout: int,
                       max_retries: int, delay: float, safety_margin: int) -> int | None:
    if name in cache.get("resolved_league_ids", {}):
        return cache["resolved_league_ids"][name]

    url = f"{base_url}/leagues?search={urllib.parse.quote(name)}&country=England"
    data = fetch_json(url, headers, timeout, max_retries, delay, safety_margin)
    time.sleep(delay)
    if not data or not data.get("response"):
        LOG.warning("Could not resolve league id for '%s' via search.", name)
        return None

    league_id = data["response"][0]["league"]["id"]
    cache.setdefault("resolved_league_ids", {})[name] = league_id
    LOG.info("Resolved '%s' -> league id %s (cached for future runs)", name, league_id)
    return league_id


# --------------------------------------------------------------------------
# Team discovery (cached)
# --------------------------------------------------------------------------

def teams_cache_is_fresh(cache: dict, league_id: int, max_age_days: int) -> bool:
    entry = cache.get("teams_by_league", {}).get(str(league_id))
    if not entry:
        return False
    cached_at = datetime.fromisoformat(entry["cached_at"])
    return datetime.now(timezone.utc) - cached_at < timedelta(days=max_age_days)


def fetch_teams_for_league(league_id: int, season: int, base_url: str, headers: dict,
                            timeout: int, max_retries: int, delay: float, safety_margin: int) -> list:
    url = f"{base_url}/teams?league={league_id}&season={season}"
    data = fetch_json(url, headers, timeout, max_retries, delay, safety_margin)
    time.sleep(delay)
    if not data:
        return []
    return [
        {"team_id": t["team"]["id"], "team_name": t["team"]["name"]}
        for t in data.get("response", [])
    ]


# --------------------------------------------------------------------------
# Fixtures per team
# --------------------------------------------------------------------------

def fetch_team_fixtures(team_id: int, next_n: int, base_url: str, headers: dict,
                         timeout: int, max_retries: int, delay: float, safety_margin: int) -> list:
    url = f"{base_url}/fixtures?team={team_id}&next={next_n}"
    data = fetch_json(url, headers, timeout, max_retries, delay, safety_margin)
    if not data:
        return []
    return data.get("response", [])


# --------------------------------------------------------------------------
# XMLTV building
# --------------------------------------------------------------------------

def slugify(team_id, team_name: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in team_name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"af-team-{team_id}-{slug}"


def xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0000")


def format_match_date(dt: datetime) -> str:
    """e.g. 'Sat 10 Oct' - matches the style shown in EPG viewers like EPGenius."""
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"


# Status codes per API-Football's docs: https://www.api-football.com/documentation-v3#operation/get-fixtures
FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}
LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}


def build_description(home_name, away_name, league_name, round_name, venue_name,
                       state, home_score, away_score) -> str:
    """A readable sentence about the match, plus round/venue where available."""
    if not (home_name and away_name):
        return ""

    if state == "finished":
        sentence = f"{home_name} {home_score}-{away_score} {away_name}."
    elif state == "live":
        sentence = f"{home_name} {home_score if home_score is not None else 0}-" \
                    f"{away_score if away_score is not None else 0} {away_name}, in progress."
    elif league_name:
        sentence = f"{home_name} host {away_name} in the {league_name}."
    else:
        sentence = f"{home_name} host {away_name}."

    extra = []
    if round_name:
        extra.append(round_name)
    if venue_name:
        extra.append(f"Venue: {venue_name}")

    if extra:
        return f"{sentence} " + " | ".join(extra)
    return sentence


def parse_fixture_start(fixture: dict) -> datetime | None:
    date_str = fixture.get("fixture", {}).get("date")
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).astimezone(timezone.utc)
    except ValueError:
        return None


def build_xmltv(teams: list, fixtures_by_team: dict, duration_minutes: int, now=None) -> Element:
    if now is None:
        now = datetime.now(timezone.utc)

    tv = Element("tv", {
        "generator-info-name": "apifootball-team-epg",
        "generator-info-url": "https://www.api-football.com",
    })

    for team in teams:
        c_el = SubElement(tv, "channel", {"id": team["channel_id"]})
        dn = SubElement(c_el, "display-name")
        dn.text = team["team_name"]

    for team in teams:
        team_id = team["team_id"]
        cid = team["channel_id"]

        # First pass: parse every fixture into a normalized record with its
        # TRUE kickoff time (used for sorting and the human-readable date -
        # never altered for display purposes).
        parsed = []
        for fixture in fixtures_by_team.get(str(team_id), []):
            actual_start = parse_fixture_start(fixture)
            if actual_start is None:
                continue
            actual_stop = actual_start + timedelta(minutes=duration_minutes)

            teams_info = fixture.get("teams", {})
            home = teams_info.get("home", {})
            away = teams_info.get("away", {})
            home_name = home.get("name")
            away_name = away.get("name")
            goals = fixture.get("goals", {})
            home_score = goals.get("home")
            away_score = goals.get("away")
            status_short = fixture.get("fixture", {}).get("status", {}).get("short")

            if status_short in FINISHED_STATUSES:
                state = "finished"
            elif status_short in LIVE_STATUSES:
                state = "live"
            else:
                state = "upcoming"

            if home_name and away_name:
                is_home = (home.get("id") == team_id)
                opponent = away_name if is_home else home_name
                relation = f"Home to {opponent}" if is_home else f"Away at {opponent}"

                if state == "finished":
                    title = f"FT: {home_name} {home_score}-{away_score} {away_name}"
                elif state == "live":
                    hs = home_score if home_score is not None else 0
                    as_ = away_score if away_score is not None else 0
                    title = f"LIVE: {home_name} {hs}-{as_} {away_name}"
                else:
                    date_str = format_match_date(actual_start)
                    title = f"Upcoming: {relation}, {date_str}"
            else:
                title = team["team_name"]

            round_name = fixture.get("league", {}).get("round")
            venue_name = fixture.get("fixture", {}).get("venue", {}).get("name")
            desc_text = build_description(home_name, away_name, fixture.get("league", {}).get("name"),
                                           round_name, venue_name, state, home_score, away_score)

            parsed.append({
                "actual_start": actual_start, "actual_stop": actual_stop,
                "title": title, "desc": desc_text,
            })

        if not parsed:
            continue
        parsed.sort(key=lambda p: p["actual_start"])

        # Second pass: lay out display start/stop times so the channel's
        # timeline has no gaps - the earliest entry always covers "now"
        # even if its real kickoff is days away, consecutive entries chain
        # directly onto each other, and the last entry stretches well into
        # the future so the grid never goes empty before the next refresh.
        far_future = now + timedelta(days=45)
        n = len(parsed)
        for i, item in enumerate(parsed):
            if i == 0:
                display_start = min(now, item["actual_start"])
            else:
                display_start = parsed[i - 1]["_display_stop"]
            if i == n - 1:
                display_stop = max(item["actual_stop"], far_future)
            else:
                display_stop = max(item["actual_stop"], parsed[i + 1]["actual_start"])
            item["_display_start"] = display_start
            item["_display_stop"] = display_stop

        for item in parsed:
            prog = SubElement(tv, "programme", {
                "start": xmltv_time(item["_display_start"]),
                "stop": xmltv_time(item["_display_stop"]),
                "channel": cid,
            })
            t_el = SubElement(prog, "title")
            t_el.text = item["title"]

            if item["desc"]:
                d_el = SubElement(prog, "desc")
                d_el.text = item["desc"]

            cat_el = SubElement(prog, "category")
            cat_el.text = "Sports"

    return tv


def write_pretty_xml(root: Element, out_path: Path) -> None:
    from io import BytesIO
    from xml.etree.ElementTree import ElementTree
    buf = BytesIO()
    ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    pretty = minidom.parseString(buf.getvalue()).toprettyxml(indent="  ", encoding="utf-8")
    out_path.write_bytes(pretty)


def package_tar_gz(xml_path: Path, archive_path: Path) -> None:
    import tarfile
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(xml_path, arcname=xml_path.name)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def generate(config_path: str = "apifootball_config.json", out_dir: str = ".") -> Path:
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    cfg = json.loads(config_path.read_text())

    api_key = cfg["api_key"]
    if not api_key or api_key == "YOUR_API_FOOTBALL_KEY_HERE":
        raise ValueError("Set a real api_key in apifootball_config.json first "
                          "(free key: https://dashboard.api-football.com).")

    base_url = cfg["base_url"]
    headers = build_headers(api_key, cfg.get("auth_style", "direct"))
    timeout = cfg.get("request_timeout_seconds", 15)
    max_retries = cfg.get("max_retries", 3)
    delay = cfg.get("request_delay_seconds", 6.5)
    safety_margin = cfg.get("rate_limit_safety_margin", 3)
    team_cache_days = cfg.get("team_cache_days", 30)
    fixtures_per_team = cfg.get("fixtures_per_team", 5)
    duration_minutes = cfg.get("event_duration_minutes", 120)
    season = current_season_year()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = out_dir / cfg.get("output_xml_name", "epg_teams_apifootball.xml")
    archive_path = out_dir / cfg.get("output_archive_name", "epg_teams_apifootball.tar.gz")
    cache_path = out_dir / "apifootball_cache.json"
    cache = load_cache(cache_path)

    quota_hit = False

    # --- Resolve any missing league IDs (cached) ---
    resolved_leagues = []
    for league in cfg["leagues"]:
        league_id = league.get("league_id")
        if league_id is None and not quota_hit:
            try:
                league_id = resolve_league_id(league["name"], base_url, headers, cache,
                                               timeout, max_retries, delay, safety_margin)
            except QuotaExhausted:
                quota_hit = True
                league_id = cache.get("resolved_league_ids", {}).get(league["name"])
        if league_id:
            resolved_leagues.append({"name": league["name"], "league_id": league_id})
        else:
            LOG.warning("Skipping '%s' - no league id available yet.", league["name"])
    save_cache(cache_path, cache)

    # --- Team discovery per league (cached, refreshed every team_cache_days) ---
    cache.setdefault("teams_by_league", {})
    for league in resolved_leagues:
        lid = league["league_id"]
        if quota_hit or teams_cache_is_fresh(cache, lid, team_cache_days):
            continue
        teams = fetch_teams_for_league(lid, season, base_url, headers, timeout, max_retries,
                                        delay, safety_margin) if not quota_hit else []
        if teams:
            cache["teams_by_league"][str(lid)] = {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "teams": teams,
            }
            LOG.info("  %s: %d teams (refreshed)", league["name"], len(teams))
        save_cache(cache_path, cache)

    # --- Flatten team list across all leagues, de-duplicated ---
    all_teams = []
    seen_ids = set()
    for lid, entry in cache.get("teams_by_league", {}).items():
        for t in entry["teams"]:
            if t["team_id"] in seen_ids:
                continue
            seen_ids.add(t["team_id"])
            all_teams.append({
                "team_id": t["team_id"],
                "team_name": t["team_name"],
                "channel_id": slugify(t["team_id"], t["team_name"]),
            })

    LOG.info("Tracking %d unique teams. Fetching fixtures (quota already hit: %s)...",
              len(all_teams), quota_hit)

    cache.setdefault("fixtures_by_team", {})
    fetched = 0
    for i, team in enumerate(all_teams, start=1):
        tid_key = str(team["team_id"])
        if quota_hit:
            break
        try:
            fixtures = fetch_team_fixtures(team["team_id"], fixtures_per_team, base_url, headers,
                                            timeout, max_retries, delay, safety_margin)
            cache["fixtures_by_team"][tid_key] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "events": fixtures,
            }
            fetched += 1
        except QuotaExhausted:
            quota_hit = True
        if i % 20 == 0:
            LOG.info("  ...%d/%d teams processed this run", i, len(all_teams))
        time.sleep(delay)
    save_cache(cache_path, cache)

    LOG.info("Freshly fetched fixtures for %d/%d teams this run (rest served from cache).",
              fetched, len(all_teams))

    fixtures_by_team = {
        tid: entry["events"] for tid, entry in cache.get("fixtures_by_team", {}).items()
    }

    tv_root = build_xmltv(all_teams, fixtures_by_team, duration_minutes)
    write_pretty_xml(tv_root, xml_path)
    LOG.info("Wrote XMLTV to %s", xml_path)

    package_tar_gz(xml_path, archive_path)
    LOG.info("Packaged archive at %s", archive_path)
    return archive_path


def matchday_refresh(config_path: str = "apifootball_config.json", out_dir: str = ".",
                      window_hours: float = 4.0, grace_hours: float = 3.0) -> Path | None:
    """
    Cheap partial refresh for use on a short interval between full generate()
    runs - mirrors team_epg_generator.py's matchday_refresh(). Only re-fetches
    teams whose cached fixture is currently 'interesting' (starting soon,
    live, or recently finished), so frequent polling doesn't burn through
    the 100/day quota the way re-running generate() in full would.

    Requires generate() to have run at least once already (needs its cache).
    Returns None (and logs, without touching existing files) if there's no
    cache yet, or nothing is currently in the matchday window.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    cfg = json.loads(config_path.read_text())

    api_key = cfg["api_key"]
    if not api_key or api_key == "YOUR_API_FOOTBALL_KEY_HERE":
        raise ValueError("Set a real api_key in apifootball_config.json first.")

    base_url = cfg["base_url"]
    headers = build_headers(api_key, cfg.get("auth_style", "direct"))
    timeout = cfg.get("request_timeout_seconds", 15)
    max_retries = cfg.get("max_retries", 3)
    delay = cfg.get("request_delay_seconds", 6.5)
    safety_margin = cfg.get("rate_limit_safety_margin", 3)
    fixtures_per_team = cfg.get("fixtures_per_team", 5)
    duration_minutes = cfg.get("event_duration_minutes", 120)

    out_dir = Path(out_dir)
    cache_path = out_dir / "apifootball_cache.json"
    if not cache_path.exists():
        LOG.warning("No apifootball_cache.json yet - run generate() at least once first.")
        return None
    cache = load_cache(cache_path)

    all_teams = []
    seen_ids = set()
    for lid, entry in cache.get("teams_by_league", {}).items():
        for t in entry["teams"]:
            if t["team_id"] in seen_ids:
                continue
            seen_ids.add(t["team_id"])
            all_teams.append({
                "team_id": t["team_id"], "team_name": t["team_name"],
                "channel_id": slugify(t["team_id"], t["team_name"]),
            })

    fixtures_by_team = {tid: entry["events"] for tid, entry in cache.get("fixtures_by_team", {}).items()}

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)
    window_start = now - timedelta(hours=grace_hours)

    teams_to_refresh = []
    for team in all_teams:
        for fixture in fixtures_by_team.get(str(team["team_id"]), []):
            start = parse_fixture_start(fixture)
            if start and window_start <= start <= window_end:
                teams_to_refresh.append(team)
                break

    if not teams_to_refresh:
        LOG.info("Matchday refresh: nothing in the +%.1fh/-%.1fh window right now - skipping.",
                  window_hours, grace_hours)
        return None

    LOG.info("Matchday refresh: %d team(s) in the current window, re-fetching...",
              len(teams_to_refresh))

    for team in teams_to_refresh:
        try:
            fixtures = fetch_team_fixtures(team["team_id"], fixtures_per_team, base_url, headers,
                                            timeout, max_retries, delay, safety_margin)
            fixtures_by_team[str(team["team_id"])] = fixtures
            cache.setdefault("fixtures_by_team", {})[str(team["team_id"])] = {
                "updated_at": now.isoformat(), "events": fixtures,
            }
        except QuotaExhausted:
            LOG.warning("Quota exhausted during matchday refresh - stopping early.")
            break
        time.sleep(delay)

    save_cache(cache_path, cache)

    xml_path = out_dir / cfg.get("output_xml_name", "epg_teams_apifootball.xml")
    archive_path = out_dir / cfg.get("output_archive_name", "epg_teams_apifootball.tar.gz")
    tv_root = build_xmltv(all_teams, fixtures_by_team, duration_minutes, now=now)
    write_pretty_xml(tv_root, xml_path)
    package_tar_gz(xml_path, archive_path)
    LOG.info("Matchday refresh complete - rewrote %s", archive_path)
    return archive_path


def main():
    parser = argparse.ArgumentParser(description="Generate a per-team XMLTV EPG from API-Football.")
    parser.add_argument("--config", default="apifootball_config.json")
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        generate(args.config, args.out_dir)
    except (FileNotFoundError, ValueError) as e:
        LOG.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
