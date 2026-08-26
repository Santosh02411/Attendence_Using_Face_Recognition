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

bind = f"{os.environ.get('GUNICORN_HOST', '0.0.0.0')}:{os.environ.get('GUNICORN_PORT', '5000')}"

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
