FROM ubuntu:26.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libreoffice-calc python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home builder \
    && install -d -o builder -g builder /app/private/web-runs

COPY equity_research/ equity_research/
COPY web/dist/index.html web/dist/app.js web/dist/styles.css web/dist/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER builder
CMD ["/opt/venv/bin/python", "-m", "equity_research", "serve-research", "--live-only", "--port", "8053"]
