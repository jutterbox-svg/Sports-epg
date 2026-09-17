"""
Generates the full English football EPG:

  output/
    epg.xml                 <- combined XMLTV, every league/team
    epg.tar.gz               <- epg.xml + teams/*.xml, gzipped tarball
    teams/
      premier-league-arsenal.xml
      premier-league-brighton-and-hove-albion.xml
      ...
    teams_index.json         <- {league: [{name, slug, channel_id}, ...]}

Safe to call repeatedly (e.g. every 6 hours) - each run fully
regenerates the output directory. Any single team/league that fails
to fetch is logged and skipped rather than aborting the whole run.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
from datetime import datetime, timezone
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, tostring

from leagues import LEAGUES, LEAGUE_ORDER
from settings import OUTPUT_DIR, TEAMS_SUBDIR, XMLTV_SOURCE_INFO_NAME, XMLTV_GENERATOR_NAME
from sportsdb_client import get_teams_in_league, get_next_events, get_last_events
from xmltv_builder import TeamChannel

log = logging.getLogger("generator")


def _pretty_xml(root: Element) -> str:
    rough = tostring(root, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def _new_tv_root() -> Element:
    root = Element("tv", {
        "source-info-name": XMLTV_SOURCE_INFO_NAME,
        "generator-info-name": XMLTV_GENERATOR_NAME,
    })
    return root


def generate_all() -> dict:
    """Runs a full generation pass. Returns a small summary dict."""
    started = datetime.now(timezone.utc)
    log.info("Starting EPG generation at %s", started.isoformat())

    teams_dir = os.path.join(OUTPUT_DIR, TEAMS_SUBDIR)
    os.makedirs(teams_dir, exist_ok=True)

    combined_root = _new_tv_root()
    channel_elements = []
    programme_elements = []
    teams_index: dict[str, list[dict]] = {}
    team_count = 0
    error_count = 0

    for league_display_name in LEAGUE_ORDER:
        api_league_name = LEAGUES[league_display_name]
        log.info("Fetching teams for %s (%s)", league_display_name, api_league_name)
        teams = get_teams_in_league(api_league_name)
        if not teams:
            log.warning("No teams returned for %s - skipping league this run", league_display_name)
            continue

        teams_index[league_display_name] = []

        for team in teams:
            team_name = team.get("strTeam", "Unknown")
            team_id = team.get("idTeam")
            if not team_id:
                continue
            try:
                channel = TeamChannel(team, league_display_name)
                next_events = get_next_events(team_id)
                last_events = get_last_events(team_id)
                now = datetime.now(timezone.utc)
                programmes = channel.build_programmes(next_events, last_events, now)

                # Combined document.
                channel_elements.append(channel.to_channel_element())
                programme_elements.extend(channel.to_programme_elements(programmes))

                # Per-team document.
                team_root = _new_tv_root()
                team_root.append(channel.to_channel_element())
                for prog_el in channel.to_programme_elements(programmes):
                    team_root.append(prog_el)
                team_xml_path = os.path.join(teams_dir, f"{channel.slug}.xml")
                with open(team_xml_path, "w", encoding="utf-8") as f:
                    f.write(_pretty_xml(team_root))

                teams_index[league_display_name].append({
                    "name": team_name,
                    "slug": channel.slug,
                    "channel_id": channel.channel_id,
                })
                team_count += 1
            except Exception:
                error_count += 1
                log.exception("Failed to build EPG for team %s (%s)", team_name, team_id)

    # Assemble combined epg.xml (channels first, then all programmes -
    # required ordering per the XMLTV spec).
    for el in channel_elements:
        combined_root.append(el)
    for el in programme_elements:
        combined_root.append(el)

    combined_path = os.path.join(OUTPUT_DIR, "epg.xml")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write(_pretty_xml(combined_root))

    with open(os.path.join(OUTPUT_DIR, "teams_index.json"), "w", encoding="utf-8") as f:
        json.dump(teams_index, f, indent=2)

    tar_path = os.path.join(OUTPUT_DIR, "epg.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(combined_path, arcname="epg.xml")
        tar.add(teams_dir, arcname="teams")

    finished = datetime.now(timezone.utc)
    summary = {
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "teams_processed": team_count,
        "errors": error_count,
        "leagues": {k: len(v) for k, v in teams_index.items()},
    }
    log.info("EPG generation complete: %s", summary)

    with open(os.path.join(OUTPUT_DIR, "last_run.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    generate_all()
