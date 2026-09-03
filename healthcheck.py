"""Docker HEALTHCHECK probe.

Kept as a plain script (rather than an inline `python -c "..."` in the
Dockerfile) so the quoting/escaping is simple and easy to verify, and so
it can be run directly (`python healthcheck.py`) to sanity-check outside
of Docker too.

Hits GET /login — the cheapest route that proves gunicorn is actually
accepting and serving requests, not just that the process exists.
"""
import os
import sys
import urllib.request

port = os.environ.get('GUNICORN_PORT', '5000')
url = f'http://127.0.0.1:{port}/login'  # fixed loopback host + operator-controlled port, never user input

try:
    with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310
        sys.exit(0 if response.status < 500 else 1)
except Exception:
    sys.exit(1)
