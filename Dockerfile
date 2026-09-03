# syntax=docker/dockerfile:1
#
# Production image: gunicorn serving wsgi:app behind whatever reverse
# proxy/load balancer you put in front of the container (see README
# "Deploying behind a reverse proxy" — set BEHIND_REVERSE_PROXY=1 when you
# do). Face detection/embedding data (Datasets/, database/) is written to
# volumes, not baked into the image — see docker-compose.yml.

FROM python:3.12-slim

# --- System dependencies -------------------------------------------------
# opencv-contrib-python's Linux wheel is self-contained EXCEPT for a
# handful of shared libraries a normal desktop Linux install has and a
# minimal container image doesn't (there is no X server / OpenGL in this
# image, matching the "headless" reality of a server deployment):
#   - libgl1, libglib2.0-0: required by cv2's imgcodecs/highgui bindings
#     even when only the still-image (no GUI window) path is used.
#   - libgomp1: OpenMP runtime cv2's DNN module (face embedding inference)
#     links against.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user rather than the container default root — limits
# blast radius if the app or one of its dependencies is ever compromised.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

WORKDIR /app

# --- Python dependencies --------------------------------------------------
# Copied and installed before the rest of the source so this (slow) layer
# is cached across rebuilds that only change application code.
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

# --- Application source ----------------------------------------------------
COPY . .
# .dockerignore already excludes tests/, .git/, __pycache__, local .env,
# and the runtime database/Datasets directories (see docker-compose.yml
# for how those are provided instead, via volumes).

# database/, Datasets/, and models/ are written to (or, for models/, may be
# a fresh empty dir the app fills from an env override) at runtime — make
# sure they exist and are owned by the user the process actually runs as,
# so a bind-mounted host directory with different ownership doesn't cause
# a permission error on first write.
RUN mkdir -p database Datasets models backups \
    && chown -R appuser:appuser /app

USER appuser

# Gunicorn's own PID isn't reachable from HEALTHCHECK's shell, so hit the
# app over HTTP instead, via a small standalone script (see
# healthcheck.py) — proves the WSGI app is actually serving, not just
# that the process is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "healthcheck.py"]

EXPOSE 5000

# init_databases() runs inside wsgi.py at import time, so it happens
# exactly once per worker boot — no separate entrypoint script needed.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
