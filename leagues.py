"""
English football league definitions.

The keys are the human-readable names used in the EPG (channel display
names, filenames, logs). The values are the EXACT league name strings
TheSportsDB's `search_all_teams.php?l=` endpoint expects. These were
verified against thesportsdb.com league pages.

Tiers, top to bottom:
  1. Premier League
  2. Championship
  3. League One
  4. League Two
  5. National League
"""

LEAGUES = {
    "Premier League": "English Premier League",
    "Championship": "English League Championship",
    "League One": "English League 1",
    "League Two": "English League 2",
    "National League": "English National League",
}

# Order in which leagues appear in the combined EPG / logs.
LEAGUE_ORDER = list(LEAGUES.keys())
