import os

# TheSportsDB API key. "3" is the free tier's shared test key used in
# their public docs (some older examples use "1" or "123" — all three
# are the same shared free-tier key). Override with a paid key via env
# var if you upgrade later.
SPORTSDB_API_KEY = os.environ.get("SPORTSDB_API_KEY", "123")
SPORTSDB_BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}"

# How often to regenerate the EPG, in hours.
UPDATE_INTERVAL_HOURS = float(os.environ.get("UPDATE_INTERVAL_HOURS", "6"))

# Assumed total on-air duration of a football match for XMLTV purposes
# (kick-off to final whistle + stoppage/build-up buffer).
MATCH_DURATION_MINUTES = int(os.environ.get("MATCH_DURATION_MINUTES", "135"))  # 2h15m

# Free-tier TheSportsDB is rate limited. This is the delay between
# consecutive API calls while building the EPG.
API_REQUEST_DELAY_SECONDS = float(os.environ.get("API_REQUEST_DELAY_SECONDS", "1.2"))

# Where generated files live.
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"))
TEAMS_SUBDIR = "teams"

# Web server port (Railway sets $PORT automatically).
PORT = int(os.environ.get("PORT", "8080"))

# XMLTV source metadata.
XMLTV_SOURCE_INFO_NAME = "English Football EPG (TheSportsDB)"
XMLTV_GENERATOR_NAME = "sports-epg-generator"
