"""
Web service entrypoint for Railway.

Endpoints:
  GET /                    -> simple status page (JSON)
  GET /epg.xml             -> combined XMLTV for every team
  GET /epg.tar.gz          -> combined + per-team XMLTV as a gzipped tarball
  GET /teams               -> JSON index of leagues/teams/slugs
  GET /teams/<slug>.xml    -> single team's XMLTV
  GET /health              -> health check for Railway
  POST /regenerate         -> force an immediate regeneration (blocking)

A background scheduler regenerates all files every UPDATE_INTERVAL_HOURS.
Generation also runs once immediately at startup so the service is
never serving empty output.
"""
import json
import logging
import os
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, send_from_directory, abort

from config.settings import OUTPUT_DIR, TEAMS_SUBDIR, UPDATE_INTERVAL_HOURS, PORT
from app.generator import generate_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")

app = Flask(__name__)
_generation_lock = threading.Lock()


def run_generation_job():
    if not _generation_lock.acquire(blocking=False):
        log.info("Generation already in progress, skipping this trigger")
        return
    try:
        generate_all()
    finally:
        _generation_lock.release()


@app.route("/")
def index():
    last_run_path = os.path.join(OUTPUT_DIR, "last_run.json")
    last_run = None
    if os.path.exists(last_run_path):
        with open(last_run_path, encoding="utf-8") as f:
            last_run = json.load(f)
    return jsonify({
        "service": "English Football Sports EPG",
        "update_interval_hours": UPDATE_INTERVAL_HOURS,
        "last_run": last_run,
        "endpoints": ["/epg.xml", "/epg.tar.gz", "/teams", "/teams/<slug>.xml", "/health"],
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/epg.xml")
def epg_xml():
    path = os.path.join(OUTPUT_DIR, "epg.xml")
    if not os.path.exists(path):
        abort(503, description="EPG not generated yet, try again shortly")
    return send_from_directory(OUTPUT_DIR, "epg.xml", mimetype="application/xml")


@app.route("/epg.tar.gz")
def epg_tar():
    path = os.path.join(OUTPUT_DIR, "epg.tar.gz")
    if not os.path.exists(path):
        abort(503, description="EPG not generated yet, try again shortly")
    return send_from_directory(OUTPUT_DIR, "epg.tar.gz", mimetype="application/gzip", as_attachment=True)


@app.route("/teams")
def teams_index():
    path = os.path.join(OUTPUT_DIR, "teams_index.json")
    if not os.path.exists(path):
        abort(503, description="EPG not generated yet, try again shortly")
    return send_from_directory(OUTPUT_DIR, "teams_index.json", mimetype="application/json")


@app.route("/teams/<slug>.xml")
def team_xml(slug):
    teams_dir = os.path.join(OUTPUT_DIR, TEAMS_SUBDIR)
    filename = f"{slug}.xml"
    if not os.path.exists(os.path.join(teams_dir, filename)):
        abort(404, description="Unknown team slug - see /teams for valid slugs")
    return send_from_directory(teams_dir, filename, mimetype="application/xml")


@app.route("/regenerate", methods=["POST"])
def regenerate():
    run_generation_job()
    return jsonify({"status": "regenerated"})


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_generation_job,
        "interval",
        hours=UPDATE_INTERVAL_HOURS,
        id="epg_regeneration",
        next_run_time=None,  # first run handled explicitly below
    )
    scheduler.start()
    return scheduler


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Run once immediately in a background thread so the web server
    # can start accepting health checks right away on Railway.
    threading.Thread(target=run_generation_job, daemon=True).start()
    start_scheduler()

    from waitress import serve
    log.info("Starting web server on port %d", PORT)
    serve(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
