"""Production WSGI entrypoint.

`app.py`'s `if __name__ == '__main__':` block runs Flask's built-in dev
server (`app.run(...)`) — it prints its own "WARNING: this is a
development server" banner for a reason: no concurrency to speak of, no
process management, not hardened for the open internet.

This module is what a real WSGI server imports instead. It does the one
thing app.py's __main__ block does that a WSGI server won't do for you —
call init_databases() so the SQLite files/tables and Datasets/ folder
exist before the first request — and then exposes the Flask `app` object
under the conventional `wsgi:app` target.

Usage:
    gunicorn --config gunicorn.conf.py wsgi:app
    # or, without the config file:
    gunicorn --bind 0.0.0.0:5000 --workers 3 wsgi:app

    # Windows (gunicorn isn't available there — see README):
    waitress-serve --host 0.0.0.0 --port 5000 wsgi:app
"""
from app import app, init_databases

init_databases()

__all__ = ['app']
