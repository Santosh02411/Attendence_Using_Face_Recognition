"""Gunicorn config for production. Used by the Dockerfile's CMD and can
also be used directly on a Linux/macOS host:

    gunicorn --config gunicorn.conf.py wsgi:app

(gunicorn doesn't support Windows — see README's "Deploying on Windows"
note for the waitress alternative.)

Every value here is overridable via the environment so the same image
works across dev/staging/prod without a rebuild.
"""
import multiprocessing
import os

# Bound to all interfaces intentionally: container/reverse-proxy deployments
# need to accept traffic from outside gunicorn's own network namespace.
# Override GUNICORN_HOST to restrict it for a bare-metal deployment.
bind = f"{os.environ.get('GUNICORN_HOST', '0.0.0.0')}:{os.environ.get('GUNICORN_PORT', '5000')}"  # nosec B104

# A small, CPU-bound-ish app (face embedding inference happens synchronously
# per request) benefits more from a modest worker count than from a large
# one competing for the same CPU cores. (2 x cores) + 1 is gunicorn's usual
# rule of thumb; overridable directly via GUNICORN_WORKERS for tuning to
# your actual hardware.
workers = int(os.environ.get('GUNICORN_WORKERS', multiprocessing.cpu_count() * 2 + 1))

# Sync worker (gunicorn's default) is the right fit here: request handlers
# do blocking CPU/OpenCV work, not I/O-bound waiting, so async workers
# (gevent/eventlet) wouldn't help and add a dependency for no benefit.
worker_class = 'sync'

# Recycle each worker after this many requests (with jitter, so workers
# don't all recycle at once) as a defense against slow memory growth in
# long-running OpenCV/numpy processes — cheap insurance, not a fix for a
# real leak if one shows up.
max_requests = int(os.environ.get('GUNICORN_MAX_REQUESTS', 500))
max_requests_jitter = int(os.environ.get('GUNICORN_MAX_REQUESTS_JITTER', 50))

# Registration/attendance requests carry base64 image payloads and run
# face detection + embedding inference synchronously — give them more
# room than gunicorn's 30s default before a worker is killed and restarted.
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 60))

accesslog = '-'   # stdout — let the container runtime / orchestrator collect logs
errorlog = '-'    # stderr
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')

# Structured (JSON) access/error logs — see logging_config.py's
# JsonGunicornLogger. Reformats gunicorn's own logs the same way
# logging_config.configure_app_logging() formats the app's own (both
# ultimately use the same JsonFormatter), so a log shipper downstream
# sees one consistent shape whether a line came from gunicorn itself or
# from inside the Flask app. Set GUNICORN_LOG_FORMAT=plain to fall back
# to gunicorn's normal Apache-combined-style access log text (e.g. for a
# quick local `gunicorn --config gunicorn.conf.py wsgi:app` run where
# JSON is harder to eyeball).
if os.environ.get('GUNICORN_LOG_FORMAT', 'json') != 'plain':
    logger_class = 'logging_config.JsonGunicornLogger'

# %({x-request-id}o)s reads the X-Request-ID response header the app sets
# on every response (see app.py's after_request hook) — so a gunicorn
# access-log line and the app's own per-request JSON log line for the
# same request end up tagged with the same id, letting the two be
# cross-referenced even though they're logged from different places.
access_log_format = (
    '%(h)s %(l)s %(u)s "%(r)s" %(s)s %(b)s %(D)sus request_id=%({x-request-id}o)s'
)
