"""
English football league definitions.

Each league is tagged with which upstream API actually serves it:

  - "espn": ESPN's free, public, keyless site API. Generous rate limit,
    covers Premier League through League Two via league codes eng.1
    through eng.4.
  - "sportsdb": TheSportsDB (shared free key). Used only where ESPN
    doesn't have reliable coverage - National League, in our case.
    Heavily rate-limited since the free key is shared by everyone
    using the public "123" key, but with only one division's worth
    of teams going through it, that's no longer a real bottleneck.

Tiers, top to bottom:
  1. Premier League      -> ESPN (eng.1)
  2. Championship         -> ESPN (eng.2)
  3. League One           -> ESPN (eng.3)
  4. League Two            -> ESPN (eng.4)
  5. National League      -> TheSportsDB ("English National League")
"""

LEAGUES = {
    "Premier League": {"provider": "espn", "league_code": "eng.1"},
    "Championship": {"provider": "espn", "league_code": "eng.2"},
    "League One": {"provider": "espn", "league_code": "eng.3"},
    "League Two": {"provider": "espn", "league_code": "eng.4"},
    "National League": {"provider": "sportsdb", "name": "English National League"},
}

# Order in which leagues appear in the combined EPG / logs.
LEAGUE_ORDER = list(LEAGUES.keys())
