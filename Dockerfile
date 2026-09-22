FROM python:3.12-slim

WORKDIR /app

# No third-party packages needed - the whole project uses only the
# Python standard library (urllib, xml.etree, tarfile, http.server).
COPY epg_generator.py web_service.py list_teams.py team_epg_generator.py apifootball_team_epg.py \
     channels_config.json apifootball_config.json ./

RUN mkdir -p /app/output

# Railway injects $PORT at runtime; web_service.py reads it.
CMD ["python", "web_service.py"]
