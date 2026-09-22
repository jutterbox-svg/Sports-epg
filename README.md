# sportsdb-epg

Generates an XMLTV sports guide from [TheSportsDB](https://www.thesportsdb.com/) and packages it
as `epg.tar.gz`, ready for Plex/Jellyfin/Tvheadend/xTeVe/most IPTV players that accept XMLTV.

## What it does

1. Reads `channels_config.json` — one entry per league you want as a "channel"
   (e.g. English Premier League, NBA, NFL...).
2. Looks up each league's ID on TheSportsDB, then pulls its upcoming fixtures
   (`eventsnextleague.php`, which returns roughly the next 15 events per league).
3. Builds a standard XMLTV file (`<channel>` + `<programme>` elements) with
   match title, start/stop time (UTC), venue, and league as description.
4. Tars and gzips it to `epg.tar.gz`.

## Requirements

- Python 3.10+ (uses the stdlib only — no pip installs needed: `urllib`, `json`, `xml.etree`, `tarfile`).
- Outbound internet access to `www.thesportsdb.com`.

## Quick start

```bash
python3 epg_generator.py --out-dir ./output
```

This writes `./output/epg.xml` and `./output/epg.tar.gz`.

## Configuring channels

Edit `channels_config.json`. Each channel needs:

```json
{ "channel_id": "epl.sportsdb", "name": "English Premier League", "sport": "Soccer", "league_id": "4328" }
```

- `channel_id` — any unique string; this is what you'll reference in your player/M3U as the channel ID.
- `league_id` — **preferred.** The fastest and most reliable way to point at a league: browse to
  it on thesportsdb.com and read the ID out of the URL (e.g. `.../league/4328-English-Premier-League`
  → `4328`). When set, the script skips name lookup entirely and goes straight to fetching events.
- `name` / `sport` — only used as a *fallback* when `league_id` is omitted: the script searches
  TheSportsDB for a league whose name matches exactly (case-insensitive). Slower (extra API calls)
  and more fragile than just supplying the ID, so `league_id` is worth grabbing from the site
  even if you keep `name`/`sport` around as a human-readable label.

The default config ships with EPL, La Liga, Serie A, Bundesliga, Ligue 1, UEFA Champions League,
NBA, NFL, NHL, MLB and Formula 1 (all with `league_id` set) — trim or extend the list as you like.
To add a new league, find its ID from its URL on thesportsdb.com rather than guessing the name.

### API key

The free public test key `"123"` (already set in `settings.api_key`) is what TheSportsDB's own
docs currently point to for testing — it's shared by everyone using the free tier, so it's rate
limited (around 30 requests/minute) and gives reduced event coverage per league compared to a paid
key. If you have a paid TheSportsDB key, drop it into `settings.api_key` in
`channels_config.json` (or set the `SPORTSDB_API_KEY` env var if you're running the Railway
deployment) for higher limits and fuller fixture coverage.

### Event duration

TheSportsDB gives a *start* time but not a duration, so the script assigns a plausible runtime
per sport (`settings.default_event_duration_minutes` in the config) to compute each
`<programme stop=...>`. Adjust these if your matches typically run long/short.

## Scheduling it every 6 hours

Two options are included — pick one.

### Option A: cron

```bash
chmod +x install_cron.sh
./install_cron.sh
```

This adds a crontab entry (`0 */6 * * *`) that regenerates `output/epg.xml` and
`output/epg.tar.gz` in place, and logs to `epg_cron.log`. Re-running the installer is safe —
it replaces its own previous entry instead of duplicating it.

### Option B: systemd timer

```bash
sudo cp sportsdb-epg.service sportsdb-epg.timer /etc/systemd/system/
# edit the WorkingDirectory / ExecStart paths in the .service file first if not using /opt/sportsdb-epg
sudo systemctl daemon-reload
sudo systemctl enable --now sportsdb-epg.timer
sudo systemctl start sportsdb-epg.service   # optional: run once immediately
```

Check it with `systemctl list-timers | grep sportsdb-epg`.

## Pointing your player at it

Serve `output/epg.xml` or `output/epg.tar.gz` over HTTP (most XMLTV consumers accept either raw
`.xml`, `.xml.gz`, or expect you to point at the file path directly) — e.g. drop `output/` behind
nginx, or just point your player at the local file path if it runs on the same machine. Match each
`channel_id` from `channels_config.json` to the corresponding channel number in your M3U playlist
via `tvg-id`.

## Notes / limitations

- TheSportsDB's free tier returns only *upcoming* fixtures per league (not a full season), so the
  guide's depth is whatever they expose (commonly the next ~15 events) — that's inherent to the
  API, not something this script can extend, aside from paying for a key with deeper endpoints.
- All times are emitted in UTC (`+0000`); your player should handle the timezone conversion.
- The script was tested for syntax/logic in this environment but not against a live network call
  (this container has outbound network disabled) — the endpoints and field names above match
  TheSportsDB's documented v1 API, so run it against a real network before relying on it, and
  check the log output for any "Could not resolve league id" warnings, which mean a `name`/`sport`
  in the config didn't match TheSportsDB's naming.
