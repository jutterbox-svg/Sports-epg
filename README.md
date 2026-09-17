# English Football Sports EPG

Generates a live XMLTV EPG for English football, one "channel" per team,
each showing a **Now** and **Next** fixture programme. Covers:

- Premier League
- Championship
- League One
- League Two
- National League

Team lists are fetched dynamically from [TheSportsDB](https://www.thesportsdb.com)
each run (rather than hardcoded), so promotions/relegations between
seasons are picked up automatically without editing code.

Runs as a small always-on web service (Flask + a background scheduler)
so you get stable URLs to point your IPTV/EPG player at, regenerated
every 6 hours automatically.

## Endpoints (once deployed)

| Endpoint | Description |
|---|---|
| `GET /epg.xml` | Combined XMLTV file, every league/team |
| `GET /epg.tar.gz` | `epg.xml` + one XML file per team, gzipped |
| `GET /teams` | JSON index of leagues → teams → slugs |
| `GET /teams/<slug>.xml` | XMLTV for a single team, e.g. `/teams/premier-league-arsenal.xml` |
| `GET /health` | Health check (used by Railway) |
| `POST /regenerate` | Force an immediate regeneration |

## How "Now / Next" works

For each team, at generation time:

- If a match kicked off within the last ~2h15m and hasn't finished yet,
  it's shown as the **live** "now" programme, and the actual next
  fixture becomes "next".
- Otherwise, "now" is a filler card ("Next up: Team A vs Team B, kick-off
  at ...") running until kickoff, and "next" is that fixture itself
  (kickoff → kickoff + ~2h15m).
- If a team has no fixture scheduled at all, a single 24h filler
  programme is shown so the channel is never empty.

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.server
# visit http://localhost:8080
```

Or generate the files once without starting the web server:

```bash
python -m app.generator
```

## Deploying to Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, select this
   repo. Railway will detect the `Dockerfile` automatically.
3. (Optional) Set environment variables under the service's **Variables** tab:
   - `SPORTSDB_API_KEY` — defaults to the shared free key `123`. Set your
     own key here if you get a paid/Patreon TheSportsDB key later.
   - `UPDATE_INTERVAL_HOURS` — defaults to `6`.
   - `MATCH_DURATION_MINUTES` — defaults to `135`.
4. Deploy. Railway assigns a public URL — your EPG will be at
   `https://<your-app>.up.railway.app/epg.xml` (and `/epg.tar.gz`, etc).
5. Point your IPTV player / EPG aggregator at that URL. Since the
   service regenerates itself every 6 hours in the background, the
   URL never needs to change.

## Notes on the free TheSportsDB key

The shared free key (`123`) is rate-limited and occasionally flaky.
The client (`app/sportsdb_client.py`) retries transient failures and
throttles requests; if a specific team or league fails to fetch during
a run, it's skipped and logged rather than failing the whole build —
you'll just see it missing until the next 6-hourly run picks it up.
A full run across ~5 leagues (~110+ teams, 2 API calls each) takes a
few minutes because of this throttling; that's expected and fine for
a service updating every 6 hours.

If your IPTV setup hits noticeable gaps or errors, upgrading to a paid
TheSportsDB key (set as `SPORTSDB_API_KEY`) removes the rate limiting.

## Project layout

```
config/
  leagues.py     # league display names -> TheSportsDB league name strings
  settings.py    # env-var driven settings
app/
  sportsdb_client.py  # API wrapper, retries/rate-limiting
  xmltv_builder.py    # per-team Now/Next XMLTV construction
  generator.py        # orchestrates the full run, writes output/
  server.py            # Flask app + scheduler, Railway entrypoint
Dockerfile
railway.toml
requirements.txt
```
