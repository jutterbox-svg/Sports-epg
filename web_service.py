#!/usr/bin/env python3
"""
Railway entry point.

Runs a tiny HTTP server that:
  - serves the generated epg.xml / epg.tar.gz / epg_teams.xml / epg_teams.tar.gz /
    teams.csv / epg_teams_apifootball.xml / epg_teams_apifootball.tar.gz from OUTPUT_DIR
  - regenerates the TheSportsDB-based files once on startup, then every
    REFRESH_HOURS (default 6), in a background thread
  - regenerates the API-Football team schedule separately, every
    APIFOOTBALL_REFRESH_HOURS (default 24), in its own background thread -
    kept on a longer cycle since it's rate-limited to 100 requests/day on
    the free plan

Railway keeps this process running continuously (unlike a serverless
function), so simple sleep-loops are enough - no external scheduler needed.

Env vars (all optional, set in Railway's "Variables" tab):
  PORT                    - provided automatically by Railway
  REFRESH_HOURS           - TheSportsDB full regeneration interval in hours (default: 6)
  MATCHDAY_REFRESH_MINUTES - how often to check for live/soon matches (default: 15)
  MATCHDAY_WINDOW_HOURS   - "starting soon" window for the matchday check (default: 4)
  MATCHDAY_GRACE_HOURS    - "recently finished" window for the matchday check (default: 3)
  APIFOOTBALL_REFRESH_HOURS - API-Football team-schedule interval in hours (default: 24)
  CONFIG_PATH             - path to channels_config.json (default: ./channels_config.json)
  APIFOOTBALL_CONFIG_PATH - path to apifootball_config.json (default: ./apifootball_config.json)
  OUTPUT_DIR              - where output files are written (default: ./output)
  SPORTSDB_API_KEY        - overrides "api_key" in channels_config.json if set
  API_FOOTBALL_KEY        - overrides "api_key" in apifootball_config.json if set
"""

import http.server
import json
import logging
import os
import socketserver
import threading
import time
from pathlib import Path

import epg_generator
import list_teams
import team_epg_generator
import apifootball_team_epg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("web_service")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "channels_config.json")
APIFOOTBALL_CONFIG_PATH = os.environ.get("APIFOOTBALL_CONFIG_PATH", "apifootball_config.json")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output"))
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "6"))
MATCHDAY_REFRESH_MINUTES = float(os.environ.get("MATCHDAY_REFRESH_MINUTES", "15"))
APIFOOTBALL_MATCHDAY_REFRESH_MINUTES = float(os.environ.get("APIFOOTBALL_MATCHDAY_REFRESH_MINUTES", "45"))
MATCHDAY_WINDOW_HOURS = float(os.environ.get("MATCHDAY_WINDOW_HOURS", "4"))
MATCHDAY_GRACE_HOURS = float(os.environ.get("MATCHDAY_GRACE_HOURS", "3"))
APIFOOTBALL_REFRESH_HOURS = float(os.environ.get("APIFOOTBALL_REFRESH_HOURS", "24"))
PORT = int(os.environ.get("PORT", "8080"))

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Optional: let Railway env vars override the API keys baked into the
# configs, so you don't have to commit real keys to the repo.
if os.environ.get("SPORTSDB_API_KEY"):
    cfg_path = Path(CONFIG_PATH)
    cfg = json.loads(cfg_path.read_text())
    cfg["settings"]["api_key"] = os.environ["SPORTSDB_API_KEY"]
    cfg_path.write_text(json.dumps(cfg, indent=2))
    LOG.info("Applied SPORTSDB_API_KEY override from environment.")

if os.environ.get("API_FOOTBALL_KEY"):
    af_cfg_path = Path(APIFOOTBALL_CONFIG_PATH)
    if af_cfg_path.exists():
        af_cfg = json.loads(af_cfg_path.read_text())
        af_cfg["api_key"] = os.environ["API_FOOTBALL_KEY"]
        af_cfg_path.write_text(json.dumps(af_cfg, indent=2))
        LOG.info("Applied API_FOOTBALL_KEY override from environment.")

# Read the (possibly just-overridden) TheSportsDB API key once, to hand to
# list_teams.py too - it doesn't read channels_config.json itself.
_API_KEY = json.loads(Path(CONFIG_PATH).read_text())["settings"]["api_key"]


def regeneration_loop():
    while True:
        try:
            LOG.info("Starting EPG generation run...")
            epg_generator.generate(config_path=CONFIG_PATH, out_dir=str(OUTPUT_DIR))
            LOG.info("EPG generation complete.")
        except Exception:
            LOG.exception("EPG generation run failed - will retry on the next cycle.")

        try:
            LOG.info("Starting team list generation run...")
            list_teams.generate_teams_csv(
                api_key=_API_KEY,
                out_path=str(OUTPUT_DIR / "teams.csv"),
            )
            LOG.info("Team list generation complete.")
        except Exception:
            LOG.exception("Team list generation run failed - will retry on the next cycle.")

        try:
            LOG.info("Starting per-team EPG generation run (TheSportsDB, this one takes "
                      "longer - one request per team across all configured leagues)...")
            team_epg_generator.generate(config_path=CONFIG_PATH, out_dir=str(OUTPUT_DIR))
            LOG.info("Per-team EPG generation complete.")
        except Exception:
            LOG.exception("Per-team EPG generation run failed - will retry on the next cycle.")

        LOG.info("Next run in %.1f hours.", REFRESH_HOURS)
        time.sleep(REFRESH_HOURS * 3600)


def apifootball_loop():
    if not Path(APIFOOTBALL_CONFIG_PATH).exists():
        LOG.info("No apifootball_config.json found - skipping API-Football team schedule.")
        return
    while True:
        try:
            LOG.info("Starting API-Football team schedule run...")
            apifootball_team_epg.generate(config_path=APIFOOTBALL_CONFIG_PATH, out_dir=str(OUTPUT_DIR))
            LOG.info("API-Football team schedule complete.")
        except ValueError as e:
            LOG.error("API-Football not configured: %s", e)
            return  # no working API key set - don't keep retrying every cycle
        except Exception:
            LOG.exception("API-Football team schedule run failed - will retry on the next cycle.")
        LOG.info("Next API-Football run in %.1f hours.", APIFOOTBALL_REFRESH_HOURS)
        time.sleep(APIFOOTBALL_REFRESH_HOURS * 3600)


def apifootball_matchday_loop():
    """Same idea as matchday_loop() below, for the API-Football source. Note:
    the full daily run already uses ~96 of the 100 daily requests, so this
    has much less headroom than TheSportsDB's version - it'll hit the quota
    (and gracefully skip/reuse cached data) more often on busy matchdays.
    A paid API-Football plan removes this ceiling if it becomes a problem."""
    if not Path(APIFOOTBALL_CONFIG_PATH).exists():
        return
    cache_path = OUTPUT_DIR / "apifootball_cache.json"
    max_wait_seconds = 30 * 60
    waited = 0
    while not cache_path.exists() and waited < max_wait_seconds:
        time.sleep(15)
        waited += 15
    while True:
        try:
            apifootball_team_epg.matchday_refresh(
                config_path=APIFOOTBALL_CONFIG_PATH,
                out_dir=str(OUTPUT_DIR),
                window_hours=MATCHDAY_WINDOW_HOURS,
                grace_hours=MATCHDAY_GRACE_HOURS,
            )
        except (ValueError, FileNotFoundError):
            return  # not configured - don't keep retrying
        except Exception:
            LOG.exception("API-Football matchday refresh failed - will retry next cycle.")
        time.sleep(APIFOOTBALL_MATCHDAY_REFRESH_MINUTES * 60)


def matchday_loop():
    """Cheap, frequent check for live/soon-to-start matches - only re-fetches
    the handful of teams actually in the matchday window, not all ~96."""
    # Wait for the first full regeneration_loop pass to build the cache
    # matchday_refresh() depends on, rather than guessing a fixed delay -
    # the full team run can take 7-10+ minutes, so a short fixed sleep here
    # just produces a harmless but confusing warning on every fresh deploy.
    cache_path = OUTPUT_DIR / "team_events_cache.json"
    max_wait_seconds = 30 * 60  # give up waiting after 30 min and just start trying anyway
    waited = 0
    while not cache_path.exists() and waited < max_wait_seconds:
        time.sleep(15)
        waited += 15
    if not cache_path.exists():
        LOG.warning("team_events_cache.json still missing after %d min - starting matchday "
                     "checks anyway; they'll keep skipping until the first full run finishes.",
                     max_wait_seconds // 60)

    while True:
        try:
            team_epg_generator.matchday_refresh(
                out_dir=str(OUTPUT_DIR),
                window_hours=MATCHDAY_WINDOW_HOURS,
                grace_hours=MATCHDAY_GRACE_HOURS,
            )
        except Exception:
            LOG.exception("Matchday refresh failed - will retry next cycle.")
        time.sleep(MATCHDAY_REFRESH_MINUTES * 60)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"sportsdb-epg is running. Files: /epg.xml  /epg.tar.gz  "
                              b"/epg_teams.xml  /epg_teams.tar.gz  /teams.csv  "
                              b"/epg_teams_apifootball.xml  /epg_teams_apifootball.tar.gz\n")
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)


def main():
    # Kick off generation in the background so the HTTP server can bind
    # immediately (Railway's health check expects the port to open fast).
    threading.Thread(target=regeneration_loop, daemon=True).start()
    threading.Thread(target=matchday_loop, daemon=True).start()
    threading.Thread(target=apifootball_loop, daemon=True).start()
    threading.Thread(target=apifootball_matchday_loop, daemon=True).start()

    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        LOG.info("Serving %s on port %d (TheSportsDB full run every %.1fh, matchday "
                  "check every %.0fmin, API-Football every %.1fh)",
                  OUTPUT_DIR, PORT, REFRESH_HOURS, MATCHDAY_REFRESH_MINUTES, APIFOOTBALL_REFRESH_HOURS)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
