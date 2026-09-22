#!/usr/bin/env python3
"""
TheSportsDB -> XMLTV EPG generator.

Pulls upcoming fixtures for a configured list of leagues from TheSportsDB,
builds a standard XMLTV guide, and packages it as a .tar.gz archive.

Usage:
    python3 epg_generator.py [--config channels_config.json] [--out-dir ./output]

Designed to be run on a schedule (cron / systemd timer, see install_cron.sh
and the systemd unit files in this project) every 6 hours.
"""

import argparse
import gzip
import json
import logging
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree
from xml.dom import minidom

LOG = logging.getLogger("sportsdb_epg")


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def fetch_json(url: str, timeout: int, max_retries: int, delay: float) -> dict | None:
    """GET a URL and parse JSON, with basic retry/backoff. Returns None on failure."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sportsdb-epg/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            LOG.warning("Request failed (attempt %d/%d) for %s: %s", attempt, max_retries, url, e)
            time.sleep(delay * attempt)
    LOG.error("Giving up on %s after %d attempts: %s", url, max_retries, last_err)
    return None


# --------------------------------------------------------------------------
# TheSportsDB lookups
# --------------------------------------------------------------------------

def build_league_id_map(base_url: str, api_key: str, sports: set, timeout: int,
                         max_retries: int, delay: float) -> dict:
    """
    For every distinct sport in the config, fetch the full league list for
    that sport and build a {lowercased league name: league id} map.
    """
    name_to_id = {}
    for sport in sports:
        url = f"{base_url}/{api_key}/search_all_leagues.php?s={urllib.parse.quote(sport)}"
        data = fetch_json(url, timeout, max_retries, delay)
        time.sleep(delay)
        if not data or not data.get("countries"):
            LOG.warning("No leagues returned for sport '%s' - check the sport name matches "
                        "TheSportsDB's category exactly.", sport)
            continue
        for league in data["countries"]:
            lname = league.get("strLeague")
            lid = league.get("idLeague")
            if lname and lid:
                name_to_id[lname.strip().lower()] = lid
    return name_to_id


def fetch_league_events(base_url: str, api_key: str, league_id: str, timeout: int,
                         max_retries: int, delay: float) -> list:
    """Fetch the upcoming (next ~15) events for a league id."""
    url = f"{base_url}/{api_key}/eventsnextleague.php?id={league_id}"
    data = fetch_json(url, timeout, max_retries, delay)
    if not data:
        return []
    return data.get("events") or []


# --------------------------------------------------------------------------
# XMLTV building
# --------------------------------------------------------------------------

def parse_event_start_utc(event: dict) -> datetime | None:
    """
    TheSportsDB gives strTimestamp as an ISO8601 UTC time when available.
    Fall back to combining dateEvent + strTime (assumed UTC) if missing.
    """
    ts = event.get("strTimestamp")
    if ts:
        try:
            # e.g. "2024-01-15 20:00:00" or "2024-01-15T20:00:00+00:00"
            ts_norm = ts.replace("T", " ").split("+")[0].strip()
            dt = datetime.strptime(ts_norm, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
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


def xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0000")


def build_xmltv(channels_cfg: list, events_by_channel: dict, durations: dict) -> Element:
    tv = Element("tv", {
        "generator-info-name": "sportsdb-epg",
        "generator-info-url": "https://www.thesportsdb.com",
    })

    for ch in channels_cfg:
        c_el = SubElement(tv, "channel", {"id": ch["channel_id"]})
        dn = SubElement(c_el, "display-name")
        dn.text = ch["name"]

    default_minutes = durations.get("_default", 180)

    for ch in channels_cfg:
        cid = ch["channel_id"]
        sport = ch["sport"]
        minutes = durations.get(sport, default_minutes)
        for event in events_by_channel.get(cid, []):
            start = parse_event_start_utc(event)
            if start is None:
                continue
            stop = start.timestamp() + minutes * 60
            stop_dt = datetime.fromtimestamp(stop, tz=timezone.utc)

            home = event.get("strHomeTeam")
            away = event.get("strAwayTeam")
            if home and away:
                title = f"{home} vs {away}"
            else:
                title = event.get("strEvent") or ch["name"]

            prog = SubElement(tv, "programme", {
                "start": xmltv_time(start),
                "stop": xmltv_time(stop_dt),
                "channel": cid,
            })
            t_el = SubElement(prog, "title")
            t_el.text = title

            desc_bits = []
            if event.get("strLeague"):
                desc_bits.append(event["strLeague"])
            if event.get("strVenue"):
                desc_bits.append(f"Venue: {event['strVenue']}")
            if event.get("strSeason"):
                desc_bits.append(f"Season {event['strSeason']}")
            if desc_bits:
                d_el = SubElement(prog, "desc")
                d_el.text = " | ".join(desc_bits)

            cat_el = SubElement(prog, "category")
            cat_el.text = "Sports"

    return tv


def write_pretty_xml(root: Element, out_path: Path) -> None:
    rough = ElementTree(root)
    from io import BytesIO
    buf = BytesIO()
    rough.write(buf, encoding="utf-8", xml_declaration=True)
    pretty = minidom.parseString(buf.getvalue()).toprettyxml(indent="  ", encoding="utf-8")
    out_path.write_bytes(pretty)


# --------------------------------------------------------------------------
# Packaging
# --------------------------------------------------------------------------

def package_tar_gz(xml_path: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(xml_path, arcname=xml_path.name)


# --------------------------------------------------------------------------
# Reusable entry point (called by the CLI below, and importable by
# web_service.py for the Railway deployment)
# --------------------------------------------------------------------------

def generate(config_path: str = "channels_config.json", out_dir: str = ".") -> Path:
    """
    Run one full generation pass: resolve leagues, fetch events, write
    epg.xml, package epg.tar.gz. Returns the Path to the archive.
    Raises FileNotFoundError if the config is missing.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    cfg = json.loads(config_path.read_text())
    channels_cfg = cfg["channels"]
    settings = cfg["settings"]

    base_url = settings["base_url"]
    api_key = settings["api_key"]
    timeout = settings.get("request_timeout_seconds", 15)
    max_retries = settings.get("max_retries", 3)
    delay = settings.get("request_delay_seconds", 1.2)
    durations = settings.get("default_event_duration_minutes", {})

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = out_dir / settings.get("output_xml_name", "epg.xml")
    archive_path = out_dir / settings.get("output_archive_name", "epg.tar.gz")

    LOG.info("Resolving league IDs for %d configured channels...", len(channels_cfg))
    # Prefer an explicit "league_id" in the config (fast: one lookup call,
    # no ambiguity). Only fall back to name-based search for channels that
    # don't have one set.
    channels_needing_search = [ch for ch in channels_cfg if not ch.get("league_id")]
    name_to_id = {}
    if channels_needing_search:
        sports_needed = {ch["sport"] for ch in channels_needing_search}
        name_to_id = build_league_id_map(base_url, api_key, sports_needed, timeout, max_retries, delay)

    events_by_channel = {}
    total_events = 0
    for ch in channels_cfg:
        league_id = ch.get("league_id") or name_to_id.get(ch["name"].strip().lower())
        if not league_id:
            LOG.warning("Could not resolve league id for '%s' (sport: %s) - skipping. "
                        "Add a \"league_id\" to this entry in the config (find it on "
                        "TheSportsDB's website URL for that league) to skip the name lookup "
                        "entirely.", ch["name"], ch["sport"])
            events_by_channel[ch["channel_id"]] = []
            continue

        LOG.info("Fetching events for %s (league id %s)...", ch["name"], league_id)
        events = fetch_league_events(base_url, api_key, league_id, timeout, max_retries, delay)
        events_by_channel[ch["channel_id"]] = events
        total_events += len(events)
        time.sleep(delay)

    LOG.info("Fetched %d total events across %d channels.", total_events, len(channels_cfg))

    tv_root = build_xmltv(channels_cfg, events_by_channel, durations)
    write_pretty_xml(tv_root, xml_path)
    LOG.info("Wrote XMLTV to %s", xml_path)

    package_tar_gz(xml_path, archive_path)
    LOG.info("Packaged archive at %s", archive_path)
    return archive_path


def main():
    parser = argparse.ArgumentParser(description="Generate an XMLTV sports EPG from TheSportsDB.")
    parser.add_argument("--config", default="channels_config.json", help="Path to channels_config.json")
    parser.add_argument("--out-dir", default=".", help="Directory to write epg.xml / epg.tar.gz into")
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
