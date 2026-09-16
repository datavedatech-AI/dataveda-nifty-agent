FROM python:3.11-slim

WORKDIR /app

# Server-side only. The bridge agent (bridge_agent/agent.py) runs on the
# customer's own Windows machine, next to their MT5 terminal, and is
# installed separately via requirements-bridge-agent.txt - it is never
# part of this image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
