"""
English football league definitions.

Each league is tagged with which upstream API actually serves it:

  - "football_data": football-data.org (dedicated 10 req/min free quota,
    cleaner data). Its free tier only covers 12 competitions worldwide -
    for England that's Premier League and Championship, nothing lower.
  - "sportsdb": TheSportsDB (shared free key, more generous coverage
    including lower English tiers, but heavily rate-limited since the
    key is shared by everyone using the public "123" key).

Tiers, top to bottom:
  1. Premier League      -> football-data.org (code "PL")
  2. Championship         -> football-data.org (code "ELC")
  3. League One           -> TheSportsDB ("English League 1")
  4. League Two            -> TheSportsDB ("English League 2")
  5. National League      -> TheSportsDB ("English National League")
"""

LEAGUES = {
    "Premier League": {"provider": "football_data", "code": "PL"},
    "Championship": {"provider": "football_data", "code": "ELC"},
    "League One": {"provider": "sportsdb", "name": "English League 1"},
    "League Two": {"provider": "sportsdb", "name": "English League 2"},
    "National League": {"provider": "sportsdb", "name": "English National League"},
}

# Order in which leagues appear in the combined EPG / logs.
LEAGUE_ORDER = list(LEAGUES.keys())
