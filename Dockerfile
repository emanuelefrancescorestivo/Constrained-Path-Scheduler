# The hosted app as one image (DECISIONS.md D75; the steps are in docs/HETZNER.md).
# deploy/hetzner/compose.yaml runs it behind Caddy, which holds the HTTPS
# certificate; this image only speaks plain HTTP on the private network between the
# two. Built on the server itself, so the same file serves an Intel and an Arm
# machine (python:3.13-slim exists for both).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# The web app and the optional AI (without ANTHROPIC_API_KEY the rules answer, D12).
# The app runs as a user of its own, not as root; /data is its one writable place.
RUN pip install ".[web,ai]" \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin cps \
    && mkdir /data \
    && chown cps /data
USER cps

EXPOSE 8000
# The same check the old host used (/health); Docker marks the container unhealthy
# if it fails three times in a row, and `docker compose ps` shows it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# --behind-proxy trusts X-Forwarded-For and -Proto: only Caddy can reach this port.
CMD ["cps", "web", "--host", "0.0.0.0", "--port", "8000", "--behind-proxy", "--db", "/data/cps.sqlite"]
