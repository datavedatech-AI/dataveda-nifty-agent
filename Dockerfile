FROM python:3.11-slim

WORKDIR /app

# Note: requirements.txt intentionally does NOT include the MetaTrader5
# package (it's Windows-only). If MT5_MODE=direct, this container cannot
# talk to MT5 - use MT5_MODE=bridge and run app/mt5_bridge/server.py on a
# separate Windows host instead. See README.md.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY symbol_map.example.json ./symbol_map.json

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
