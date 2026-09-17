FROM python:3.12-slim

WORKDIR /app

# Keep pip/apt lean, don't buffer logs (so Railway logs show up live)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/output

# Railway injects $PORT at runtime; our app reads it via config/settings.py
EXPOSE 8080

CMD ["python", "server.py"]
