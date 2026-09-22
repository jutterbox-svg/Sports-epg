#!/usr/bin/env python3
"""
TheSportsDB -> per-TEAM XMLTV EPG generator.

Unlike epg_generator.py (one XMLTV channel per LEAGUE, showing every match in
that league), this builds one channel per TEAM, showing only that team's own
upcoming fixture(s) - e.g. a channel called "Arsenal" that only ever shows
Arsenal's games, wherever they're playing.

Steps:
  1. Read the leagues already configured in channels_config.json.
  2. For each league, fetch its full team list (lookup_all_teams.php).
  3. For each team, fetch its next fixture(s) (eventsnext.php) AND its
     recently completed fixture(s) (eventslast.php, for final scores).
  4. Build one XMLTV <channel> + <programme> per team - upcoming matches
     show as "Upcoming: Home to X, Sat 10 Oct"; finished matches show as
     "FT: Arsenal 3-1 Leeds United".
  5. Package as epg_teams.xml / epg_teams.tar.gz.

Usage:
    python3 team_epg_generator.py [--config channels_config.json] [--out-dir ./output]

IMPORTANT - free API key limitations:
  - eventsnext.php is restricted to a team's next HOME fixture only on the
    free key (away fixtures may not appear on that team's own channel -
    though they'll still show up on the opposing team's channel, and on the
    league-wide channel from epg_generator.py). A paid key removes this.
  - No live in-progress scores are available on the free key at all - that's
    a premium-only ($9/mo Patreon) feature via TheSportsDB's V2 API. This
    script only shows final ("FT") scores after a match has fully ended,
    never a live/in-progress score.
"""

import argparse
import logging
import sys
import time
import urllib.parse
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

from epg_generator import (
    fetch_json,
    parse_event_start_utc,
    xmltv_time,
    write_pretty_xml,
    package_tar_gz,
)

import json

LOG = logging.getLogger("sportsdb_team_epg")


def format_match_date(dt) -> str:
    """e.g. 'Sat 10 Oct' - matches the style shown in EPG viewers like EPGenius."""
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"


def build_description(home, away, league, round_, venue, is_finished, home_score, away_score,
                       description_en=None) -> str:
    """A readable sentence about the match, plus round/venue where available.
    If TheSportsDB has a proper prose description for this event
    (strDescriptionEN - not always populated, more common for higher-profile
    leagues), that's used as the lead sentence instead of the generated one."""
    if not (home and away):
        return description_en or ""

    if description_en:
        sentence = description_en.strip()
    elif is_finished:
        sentence = f"{home} {home_score}-{away_score} {away}."
    elif league:
        sentence = f"{home} host {away} in the {league}."
    else:
        sentence = f"{home} host {away}."

    extra = []
    if round_:
        extra.append(f"Round {round_}")
    if venue:
        extra.append(f"Venue: {venue}")

    if extra:
        return f"{sentence} " + " | ".join(extra)
    return sentence


# --------------------------------------------------------------------------
# TheSportsDB lookups
# --------------------------------------------------------------------------

def fetch_teams_for_league(base_url: str, api_key: str, league_id: str, timeout: int,
                            max_retries: int, delay: float) -> list:
    url = f"{base_url}/{api_key}/lookup_all_teams.php?id={league_id}"
    data = fetch_json(url, timeout, max_retries, delay)
    time.sleep(delay)
    if not data:
        return []
    return data.get("teams") or []


def fetch_team_next_events(base_url: str, api_key: str, team_id: str, timeout: int,
                            max_retries: int, delay: float) -> list:
    url = f"{base_url}/{api_key}/eventsnext.php?id={team_id}"
    data = fetch_json(url, timeout, max_retries, delay)
    if not data:
        return []
    return data.get("events") or []


def fetch_team_last_events(base_url: str, api_key: str, team_id: str, timeout: int,
                            max_retries: int, delay: float) -> list:
    """Recently completed matches (for showing final scores), via eventslast.php.
    TheSportsDB's v1 API has been inconsistent historically about whether this
    wraps results under "results" or "events" - check both defensively."""
    url = f"{base_url}/{api_key}/eventslast.php?id={team_id}"
    data = fetch_json(url, timeout, max_retries, delay)
    if not data:
        return []
    return data.get("results") or data.get("events") or []


# --------------------------------------------------------------------------
# XMLTV building
# --------------------------------------------------------------------------

def slugify_channel_id(team_id: str, team_name: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in team_name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"team-{team_id}-{slug}"


def build_team_xmltv(team_entries: list, events_by_team_id: dict, durations: dict, now=None) -> Element:
    """
    team_entries: list of dicts {team_id, team_name, channel_id, sport}
    events_by_team_id: {team_id: [event dicts]}
    now: current UTC time (injectable for testing); defaults to datetime.now(UTC).

    Three title states per fixture, since the free API gives no live score:
      - not yet started (now < kickoff)  -> "Upcoming: Home to X, Sat 10 Oct"
      - started, no final score yet      -> just the plain fixture, e.g. "Home to X"
        (covers actually-live matches, and the brief window right after a
        match ends before it shows up with a score via eventslast.php)
      - finished (has a final score)     -> "FT: Team A 3-1 Team B"
    """
    from datetime import datetime, timezone
    if now is None:
        now = datetime.now(timezone.utc)

    tv = Element("tv", {
        "generator-info-name": "sportsdb-team-epg",
        "generator-info-url": "https://www.thesportsdb.com",
    })

    for entry in team_entries:
        c_el = SubElement(tv, "channel", {"id": entry["channel_id"]})
        dn = SubElement(c_el, "display-name")
        dn.text = entry["team_name"]

    default_minutes = durations.get("_default", 180)

    for entry in team_entries:
        team_id = entry["team_id"]
        team_name = entry["team_name"]
        cid = entry["channel_id"]
        minutes = durations.get(entry["sport"], default_minutes)

        for event in events_by_team_id.get(team_id, []):
            start = parse_event_start_utc(event)
            if start is None:
                continue
            stop_dt = datetime.fromtimestamp(start.timestamp() + minutes * 60, tz=timezone.utc)

            home = event.get("strHomeTeam")
            away = event.get("strAwayTeam")
            home_id = event.get("idHomeTeam")
            home_score = event.get("intHomeScore")
            away_score = event.get("intAwayScore")
            is_finished = home_score is not None and away_score is not None

            if home and away:
                is_home = (home_id == team_id)
                opponent = away if is_home else home
                relation = f"Home to {opponent}" if is_home else f"Away at {opponent}"
                if is_finished:
                    title = f"FT: {home} {home_score}-{away_score} {away}"
                elif start <= now:
                    # Kicked off but no final score yet (in progress, or just
                    # finished and not reflected via eventslast.php yet) - no
                    # live score available on the free API, so just the fixture.
                    title = relation
                else:
                    date_str = format_match_date(start)
                    title = f"Upcoming: {relation}, {date_str}"
            else:
                title = event.get("strEvent") or team_name

            prog = SubElement(tv, "programme", {
                "start": xmltv_time(start),
                "stop": xmltv_time(stop_dt),
                "channel": cid,
            })
            t_el = SubElement(prog, "title")
            t_el.text = title

            desc_text = build_description(home, away, event.get("strLeague"),
                                           event.get("intRound"), event.get("strVenue"),
                                           is_finished, home_score, away_score,
                                           description_en=event.get("strDescriptionEN"))
            if desc_text:
                d_el = SubElement(prog, "desc")
                d_el.text = desc_text

            cat_el = SubElement(prog, "category")
            cat_el.text = "Sports"

    return tv


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def generate(config_path: str = "channels_config.json", out_dir: str = ".") -> Path:
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    cfg = json.loads(config_path.read_text())
    leagues_cfg = cfg["channels"]  # reuse the same league list already configured
    settings = cfg["settings"]

    base_url = settings["base_url"]
    api_key = settings["api_key"]
    timeout = settings.get("request_timeout_seconds", 15)
    max_retries = settings.get("max_retries", 3)
    # Team-level runs make far more requests than the league-level run, so
    # use a slightly longer delay by default to stay comfortably under
    # TheSportsDB's free-tier rate limit (~30 req/min).
    delay = settings.get("team_request_delay_seconds", 2.2)
    durations = settings.get("default_event_duration_minutes", {})

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = out_dir / settings.get("output_teams_xml_name", "epg_teams.xml")
    archive_path = out_dir / settings.get("output_teams_archive_name", "epg_teams.tar.gz")
    cache_path = out_dir / "team_events_cache.json"

    LOG.info("Fetching team lists for %d configured leagues...", len(leagues_cfg))
    team_entries = []
    seen_team_ids = set()
    for league in leagues_cfg:
        league_id = league.get("league_id")
        if not league_id:
            LOG.warning("Skipping '%s' - no league_id set, can't list its teams.", league["name"])
            continue
        teams = fetch_teams_for_league(base_url, api_key, league_id, timeout, max_retries, delay)
        LOG.info("  %s: %d teams", league["name"], len(teams))
        for t in teams:
            team_id = t.get("idTeam")
            team_name = t.get("strTeam")
            if not team_id or not team_name or team_id in seen_team_ids:
                continue
            seen_team_ids.add(team_id)
            team_entries.append({
                "team_id": team_id,
                "team_name": team_name,
                "channel_id": slugify_channel_id(team_id, team_name),
                "sport": league["sport"],
            })

    LOG.info("Resolved %d unique teams. Fetching each team's fixtures "
              "(next + recently completed for scores)...", len(team_entries))

    events_by_team_id = {}
    total_events = 0
    for i, entry in enumerate(team_entries, start=1):
        next_events = fetch_team_next_events(base_url, api_key, entry["team_id"], timeout, max_retries, delay)
        time.sleep(delay)
        last_events = fetch_team_last_events(base_url, api_key, entry["team_id"], timeout, max_retries, delay)
        combined = next_events + last_events
        events_by_team_id[entry["team_id"]] = combined
        total_events += len(combined)
        if i % 25 == 0:
            LOG.info("  ...%d/%d teams processed", i, len(team_entries))
        time.sleep(delay)

    LOG.info("Fetched %d total fixtures across %d teams.", total_events, len(team_entries))

    # Save a cache of everything fetched this run, so matchday_refresh() can
    # do cheap partial updates later without needing to re-fetch every team.
    cache_path.write_text(json.dumps({
        "team_entries": team_entries,
        "events_by_team_id": events_by_team_id,
        "settings_snapshot": {
            "base_url": base_url, "api_key": api_key, "timeout": timeout,
            "max_retries": max_retries, "delay": delay, "durations": durations,
            "xml_name": xml_path.name, "archive_name": archive_path.name,
        },
    }, indent=2))

    tv_root = build_team_xmltv(team_entries, events_by_team_id, durations)
    write_pretty_xml(tv_root, xml_path)
    LOG.info("Wrote team XMLTV to %s", xml_path)

    package_tar_gz(xml_path, archive_path)
    LOG.info("Packaged team archive at %s", archive_path)
    return archive_path


def matchday_refresh(out_dir: str = ".", window_hours: float = 4.0, grace_hours: float = 3.0) -> Path | None:
    """
    Cheap partial refresh for use on a short interval (e.g. every 15 min)
    between full generate() runs. Only re-fetches teams whose cached fixture
    is currently 'interesting' - kicking off soon, already live, or recently
    finished - rather than re-polling all ~96 teams every time.

    window_hours: how far into the future counts as "starting soon".
    grace_hours: how far into the past still counts as "recently finished"
      (covers the time between a match ending and eventslast.php picking up
      the final score).

    Requires generate() to have run at least once already (needs its cache).
    Returns None (and logs a warning) if no cache exists yet, or if nothing
    is currently in the matchday window (skips the write entirely - the
    existing files are left untouched).
    """
    from datetime import datetime, timezone, timedelta

    out_dir = Path(out_dir)
    cache_path = out_dir / "team_events_cache.json"
    if not cache_path.exists():
        LOG.warning("No team_events_cache.json yet - run generate() at least once first.")
        return None

    cache = json.loads(cache_path.read_text())
    team_entries = cache["team_entries"]
    events_by_team_id = cache["events_by_team_id"]
    s = cache["settings_snapshot"]

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)
    window_start = now - timedelta(hours=grace_hours)

    teams_to_refresh = []
    for entry in team_entries:
        for event in events_by_team_id.get(entry["team_id"], []):
            start = parse_event_start_utc(event)
            if start and window_start <= start <= window_end:
                teams_to_refresh.append(entry)
                break

    if not teams_to_refresh:
        LOG.info("Matchday refresh: nothing in the +%.1fh/-%.1fh window right now - skipping.",
                  window_hours, grace_hours)
        return None

    LOG.info("Matchday refresh: %d team(s) have a fixture in the current window, re-fetching...",
              len(teams_to_refresh))

    for entry in teams_to_refresh:
        next_events = fetch_team_next_events(s["base_url"], s["api_key"], entry["team_id"],
                                              s["timeout"], s["max_retries"], s["delay"])
        time.sleep(s["delay"])
        last_events = fetch_team_last_events(s["base_url"], s["api_key"], entry["team_id"],
                                              s["timeout"], s["max_retries"], s["delay"])
        events_by_team_id[entry["team_id"]] = next_events + last_events
        time.sleep(s["delay"])

    cache["events_by_team_id"] = events_by_team_id
    cache_path.write_text(json.dumps(cache, indent=2))

    xml_path = out_dir / s.get("xml_name", "epg_teams.xml")
    archive_path = out_dir / s.get("archive_name", "epg_teams.tar.gz")
    # Try to honor the real output filenames from settings if a full config
    # is reachable; fall back to the defaults above if not (cache-only mode).
    tv_root = build_team_xmltv(team_entries, events_by_team_id, s["durations"], now=now)
    write_pretty_xml(tv_root, xml_path)
    package_tar_gz(xml_path, archive_path)
    LOG.info("Matchday refresh complete - rewrote %s", archive_path)
    return archive_path


def main():
    parser = argparse.ArgumentParser(description="Generate a per-team XMLTV EPG from TheSportsDB.")
    parser.add_argument("--config", default="channels_config.json")
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        generate(args.config, args.out_dir)
    except FileNotFoundError as e:
        LOG.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
