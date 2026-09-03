import base64
import contextlib
import csv
import io
import ipaddress
import logging
import os
import random
import secrets
import sqlite3
import string
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Union

import cv2
import numpy as np
from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from PIL import Image, ImageDraw, ImageFont
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import config as cfg
import db_migrations
import error_reporting
import face_security
import logging_config

logging_config.configure_app_logging(level=cfg.LOG_LEVEL, fmt=cfg.LOG_FORMAT)
logger = logging.getLogger('attendance_app')

BASE_DIR = cfg.BASE_DIR
DATA_DIR = cfg.DATA_DIR
DATABASE_PATH = cfg.DATABASE_PATH
FACE_DATABASE_PATH = cfg.FACE_DATABASE_PATH
CASCADE_PATH = cfg.CASCADE_PATH

SECRET_KEY_ENV_VAR = cfg.SECRET_KEY_ENV_VAR
_secret_key = os.environ.get(SECRET_KEY_ENV_VAR)
if not _secret_key:
    # No key was provided via the environment: generate one for this run only.
    # This keeps the app usable for local/dev testing, but sessions will NOT
    # survive a restart, and this path must never be relied on in production —
    # always set FLASK_SECRET_KEY explicitly when deploying.
    _secret_key = secrets.token_hex(32)
    logger.warning(
        f'{SECRET_KEY_ENV_VAR} not set — using a temporary, random '
        'secret key for this process only. Set the environment variable for '
        'persistent sessions and before deploying.'
    )

app = Flask(__name__)
app.secret_key = _secret_key

# Opt-in (see error_reporting.py) — a no-op unless SENTRY_DSN is set.
# Called before the app starts handling requests so the Flask integration
# is wired up in time to catch anything, including startup-time errors
# raised by later module-level code below.
error_reporting.init_sentry(app)


def configure_proxy_fix(flask_app):
    """Wraps flask_app.wsgi_app in werkzeug's ProxyFix when the app is
    configured to run behind a reverse proxy (nginx, a cloud load balancer,
    etc.), so request.remote_addr reflects the real client IP instead of
    the proxy's own address.

    This matters beyond logging: per-IP rate limiting (Flask-Limiter),
    account-lockout-by-IP, the attendance IP allowlist
    (ATTENDANCE_ALLOWED_NETWORKS), and the audit log all read
    request.remote_addr directly. Left unconfigured behind a proxy, every
    request appears to come from the proxy, which silently defeats all
    four. Off by default so a direct (no-proxy) deployment isn't tricked
    into trusting a spoofable X-Forwarded-For header from an untrusted
    client. See README's "Deploying behind a reverse proxy" section.

    Returns flask_app for convenience/chaining; also easily unit-testable
    on its own Flask instance without needing to reimport this module.
    """
    if not cfg.BEHIND_REVERSE_PROXY:
        return flask_app
    flask_app.wsgi_app = ProxyFix(
        flask_app.wsgi_app,
        x_for=cfg.PROXY_FIX_NUM_PROXIES,
        x_proto=cfg.PROXY_FIX_NUM_PROXIES,
        x_host=cfg.PROXY_FIX_NUM_PROXIES,
        x_port=cfg.PROXY_FIX_NUM_PROXIES,
    )
    return flask_app


configure_proxy_fix(app)

# Login sessions expire after a period of inactivity instead of lasting
# indefinitely. session.permanent is set to True at each successful login
# (see login() and student_login()) so this actually applies.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=cfg.SESSION_LIFETIME_MINUTES)

# Session cookie hardening. HTTPONLY (blocks JS access, so an XSS bug
# can't steal the session cookie directly) and SAMESITE='Lax' (cookie
# isn't sent on cross-site requests, blunting CSRF further alongside
# CSRFProtect below) are Flask defaults already, but set explicitly so
# they can't silently change on a Flask upgrade. SECURE is config-driven
# — see cfg.SESSION_COOKIE_SECURE's docstring in config.py for why it
# isn't just always-on.
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = cfg.SESSION_COOKIE_SECURE

# Reject any request body larger than this outright (protects against a
# basic oversized-payload resource-exhaustion attempt). Registration and
# attendance-marking add their own tighter, more specific checks on top.
app.config['MAX_CONTENT_LENGTH'] = cfg.MAX_CONTENT_LENGTH_MB * 1024 * 1024
# Exposed so templates can do {% if config.CAPTCHA_ENABLED %} without a
# separate context processor.
app.config['CAPTCHA_ENABLED'] = cfg.CAPTCHA_ENABLED

# CSRF protection for every state-changing (non-GET) request. Forms get a
# hidden csrf_token field; JS fetch() calls send it via the X-CSRFToken
# header (see the csrfHeaders() helper in static/app.js).
csrf = CSRFProtect(app)

# Per-IP rate limiting on top of (not instead of) the per-account lockout
# below: rate limiting throttles a single IP regardless of which account
# it's hitting; lockout protects one account even from a distributed
# attempt. Defaults to in-memory storage — fine for a single-process
# deployment; set RATE_LIMIT_STORAGE_URI for a shared backend (e.g. Redis)
# if running multiple processes.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=cfg.RATE_LIMIT_STORAGE_URI,
    enabled=cfg.RATE_LIMIT_ENABLED,
    default_limits=[],
)


# --- Structured request logging -------------------------------------------
# Registered before any other before_request function (in particular,
# before restrict_access() below) so it always runs first and sets
# g.request_id even on requests restrict_access redirects away — Flask
# runs before_request hooks in registration order and stops at the first
# one that returns a value, but ALL registered before_request hooks up to
# that point still run, and after_request always runs regardless of which
# hook produced the response. See logging_config.py for how request_id
# then gets attached to every log line emitted during this request.
_REQUEST_ID_HEADER = 'X-Request-ID'
_MAX_INBOUND_REQUEST_ID_LEN = 128


@app.before_request
def _assign_request_id():
    inbound = request.headers.get(_REQUEST_ID_HEADER)
    if inbound and 0 < len(inbound) <= _MAX_INBOUND_REQUEST_ID_LEN:
        # Honor an id a trusted upstream (reverse proxy, load balancer)
        # already generated, so one request has one id end-to-end across
        # services — but only within a sane length, so a malicious/broken
        # client can't stuff an oversized value into every log line for
        # its requests.
        g.request_id = inbound
    else:
        g.request_id = uuid.uuid4().hex
    g.request_start_time = time.monotonic()


@app.after_request
def _log_request_and_tag_response(response):
    response.headers[_REQUEST_ID_HEADER] = getattr(g, 'request_id', '') or uuid.uuid4().hex

    # Baseline security headers on every response. Deliberately not a
    # Content-Security-Policy: the app relies on inline event handlers
    # and inline <script> in several templates, so a real CSP would need
    # a broader template pass (nonces or a JS refactor) to avoid breaking
    # them -- tracked as follow-up, not bundled into this header pass.
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'

    duration_ms = None
    start = getattr(g, 'request_start_time', None)
    if start is not None:
        duration_ms = round((time.monotonic() - start) * 1000, 2)

    # Identity for correlation, never credentials: whichever of these is
    # set by the session (see login()/student_login()), or None for an
    # anonymous request. Never logs request/response bodies (see
    # logging_config.py's module docstring) — those can carry face-image
    # payloads or, on some legacy paths, plaintext passwords.
    actor = session.get('admin_user') or session.get('student_name')

    log_level = logging.INFO
    if response.status_code >= 500:
        log_level = logging.ERROR
    elif response.status_code >= 400:
        log_level = logging.WARNING

    logger.log(
        log_level,
        f'{request.method} {request.path} {response.status_code}',
        extra={
            'event': 'http_request',
            'http_method': request.method,
            'http_path': request.path,
            'http_status': response.status_code,
            'duration_ms': duration_ms,
            'remote_addr': request.remote_addr,
            'actor': actor,
        },
    )
    _record_request_metric(request.endpoint, request.method, response.status_code, duration_ms)
    return response


@app.errorhandler(Exception)
def _handle_unexpected_error(e):
    """Catches anything that wasn't already turned into a response by a
    more specific error handler (like the 413/429 ones below) or by a
    route's own try/except — logs it with a full traceback (tagged with
    this request's id, so it's easy to find in the JSON logs) and, if
    Sentry is configured (see error_reporting.py), reports it there too,
    without ever leaking the traceback itself to the client."""
    # HTTPException subclasses (404, 405, the 413/429 handled below, etc.)
    # already carry their own correct status code and a safe, generic
    # message — re-raise them as-is rather than flattening every one of
    # them into a generic 500.
    if isinstance(e, HTTPException):
        return e

    logger.error(f'Unhandled exception on {request.method} {request.path}: {e}', exc_info=True)
    error_reporting.capture_exception(e)
    return jsonify({
        'status': 'error',
        'message': 'An unexpected error occurred. Please try again, or contact an administrator if it persists.',
        'request_id': logging_config.get_request_id(),
    }), 500


@app.errorhandler(413)
def handle_request_too_large(e):
    return jsonify({'status': 'error', 'message': 'Request too large.'}), 413


@app.errorhandler(429)
def handle_rate_limited(e):
    return jsonify({'status': 'error', 'message': 'Too many requests. Please wait and try again.'}), 429


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_databases():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(cfg.MODELS_DIR, exist_ok=True)
    os.makedirs(cfg.DATABASE_DIR, exist_ok=True)

    if not os.path.exists(CASCADE_PATH) and hasattr(cv2, 'data'):
        src = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
        if os.path.exists(src):
            with open(src, 'rb') as src_file, open(CASCADE_PATH, 'wb') as dst_file:
                dst_file.write(src_file.read())

    # Schema creation and evolution for database/app.db (admins, students,
    # subjects, sessions, attendance, audit_log, face_embeddings) is
    # handled by Alembic migrations under migrations/versions/ — see
    # db_migrations.py for how they're applied and how an existing
    # pre-Alembic database is detected and stamped instead of re-run.
    db_migrations.run_migrations(DATABASE_PATH)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM admins LIMIT 1')
    if cursor.fetchone() is None:
        default_admin_password = cfg.DEFAULT_ADMIN_PASSWORD
        cursor.execute('INSERT INTO admins(username, password) VALUES(?, ?)',
                        ('admin', generate_password_hash(default_admin_password)))
    conn.commit()
    conn.close()

    if not os.path.exists(FACE_DATABASE_PATH):
        face_conn = sqlite3.connect(FACE_DATABASE_PATH)
        face_cursor = face_conn.cursor()
        face_cursor.execute('''CREATE TABLE IF NOT EXISTS people(
            id INTEGER PRIMARY KEY,
            name TEXT,
            gender TEXT,
            section TEXT
        )''')
        face_conn.commit()
        face_conn.close()


def query_db(query, args=(), one=False):
    conn = get_db_connection()
    cursor = conn.execute(query, args)
    rows = cursor.fetchall()
    conn.close()
    return rows[0] if one and rows else rows


def execute_db(query, args=()):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, args)
    conn.commit()
    lastrowid = cursor.lastrowid
    conn.close()
    return lastrowid


def get_pagination(total_count, per_page, page_param='page'):
    """
    Reads the current page number from the query string, clamps it to a
    valid range given total_count/per_page, and returns a dict with
    everything a template needs to render page controls:
    {page, per_page, total_pages, total_count, offset, has_prev, has_next}.
    Page numbers are 1-indexed.
    """
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    try:
        page = int(request.args.get(page_param, 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, total_pages))
    return {
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'total_count': total_count,
        'offset': (page - 1) * per_page,
        'has_prev': page > 1,
        'has_next': page < total_pages,
    }


# Audit-log actions that represent a security-relevant event (as opposed
# to routine admin activity like a session being created, or a normal
# successful login). Used to power the audit log's "Security events"
# filter and the admin dashboard's "Security Alerts" widget — see
# audit_log_view() and admin_dashboard().
SECURITY_AUDIT_ACTIONS = (
    'admin_login_failed',
    'admin_login_blocked',
    'student_login_failed',
    'student_login_blocked',
    'attendance_blocked_network',
    'attendance_liveness_challenge_failed',
    'attendance_spoof_suspected',
    'attendance_security_lockout',
    'attendance_marking_blocked_lockout',
    'attendance_lockout_cleared',
    'unknown_face_detected',
    'suspicious_device_detected',
    'concurrent_session_detected',
    'network_change_detected',
    'impossible_location_suspected',
    'security_risk_escalated',
    'security_escalation_cleared',
)

# The subset of SECURITY_AUDIT_ACTIONS that count toward one student's
# attendance-marking lockout (see _register_attendance_security_failure).
# Narrower than SECURITY_AUDIT_ACTIONS as a whole: login failures are
# already covered by the separate login lockout, and a blocked-network
# attempt isn't evidence of a spoofed face, so neither should also count
# here.
ATTENDANCE_SECURITY_FAILURE_ACTIONS = (
    'attendance_liveness_challenge_failed',
    'attendance_spoof_suspected',
)

# Severity levels shown throughout the security dashboard/audit log —
# Low/Medium/High/Critical, ordered so a caller can compare severities
# (e.g. sort, or pick "at least High"). Deliberately a fixed lookup by
# action name (computed on read) rather than a stored column: it keeps
# every event's severity consistent with the current understanding of
# how serious that action is, including for events logged before this
# feature existed, without a backfill migration. An action not listed
# here is treated as 'low'.
SEVERITY_LEVELS = ('low', 'medium', 'high', 'critical')
EVENT_SEVERITY = {
    'admin_login_failed': 'low',
    'admin_login_blocked': 'medium',
    'student_login_failed': 'low',
    'student_login_blocked': 'medium',
    'attendance_blocked_network': 'low',
    'attendance_blocked_late': 'low',
    'attendance_blocked_group': 'low',
    'attendance_liveness_challenge_failed': 'medium',
    'attendance_spoof_suspected': 'high',
    'attendance_security_lockout': 'high',
    'attendance_marking_blocked_lockout': 'medium',
    'attendance_lockout_cleared': 'low',
    'unknown_face_detected': 'medium',
    'suspicious_device_detected': 'high',
    'concurrent_session_detected': 'high',
    'network_change_detected': 'medium',
    'impossible_location_suspected': 'critical',
    'security_risk_escalated': 'critical',
    'security_escalation_cleared': 'low',
}


def event_severity(action):
    return EVENT_SEVERITY.get(action, 'low')


# Weighted contribution of each security event type toward a student's
# risk score (see _compute_risk_score()) — a rough, documented heuristic
# rather than a statistically calibrated model. The final score is
# capped at 100 for a consistent 0-100 scale regardless of how many
# events accumulate.
RISK_EVENT_WEIGHTS = {
    'student_login_failed': 3,
    'student_login_blocked': 12,
    'attendance_liveness_challenge_failed': 8,
    'attendance_spoof_suspected': 15,
    'attendance_security_lockout': 25,
    'attendance_marking_blocked_lockout': 5,
    'suspicious_device_detected': 20,
    'concurrent_session_detected': 20,
    'network_change_detected': 10,
    'impossible_location_suspected': 35,
}


# --- In-process metrics (/metrics, Prometheus text exposition format) -----
# Deliberately hand-rolled rather than pulling in the prometheus_client
# package, matching this project's preference for no third-party
# dependency where a straightforward one suffices (see also
# face_security.py's own anti-spoof heuristic). In-memory only: counters
# reset on restart, and under gunicorn with more than one worker process
# each worker exposes only its own share of traffic — there's no
# cross-worker aggregation. Fine for the small/single-department scale
# this project targets (see README's documented trade-offs); a
# multi-worker production deployment wanting accurate aggregate metrics
# should scrape every worker or move to a shared registry.
_METRICS_LOCK = threading.Lock()
_METRICS_START_TIME = time.time()
_metrics_http_requests_total: defaultdict = defaultdict(int)          # (endpoint, method, status_class) -> count
_metrics_http_request_duration_sum: defaultdict = defaultdict(float)  # endpoint -> sum of seconds
_metrics_http_request_duration_count: defaultdict = defaultdict(int)  # endpoint -> count
_metrics_audit_events_total: defaultdict = defaultdict(int)           # action -> count
_metrics_attendance_marks_total: defaultdict = defaultdict(int)       # outcome -> count ('present', 'late', 'already_marked', 'error')


def _record_request_metric(endpoint, method, status_code, duration_ms):
    status_class = f'{status_code // 100}xx'
    with _METRICS_LOCK:
        _metrics_http_requests_total[(endpoint or 'unknown', method, status_class)] += 1
        if duration_ms is not None:
            _metrics_http_request_duration_sum[endpoint or 'unknown'] += duration_ms / 1000.0
            _metrics_http_request_duration_count[endpoint or 'unknown'] += 1


def _record_attendance_mark_metric(outcome):
    with _METRICS_LOCK:
        _metrics_attendance_marks_total[outcome] += 1


def log_audit(actor_type, actor_name, action, target=None, details=None):
    """Records one audit-log entry. actor_type is 'admin', 'student', or
    'anonymous'; action is a short machine-readable label like
    'admin_login_failed' or 'student_deleted'; target and details are
    free-form human-readable strings."""
    try:
        ip_address = request.remote_addr
    except RuntimeError:
        ip_address = None  # called outside a request context (shouldn't happen, but don't crash)
    execute_db(
        'INSERT INTO audit_log(timestamp, actor_type, actor_name, action, target, details, ip_address) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (datetime.now().isoformat(), actor_type, actor_name, action, target, details, ip_address)
    )
    with _METRICS_LOCK:
        _metrics_audit_events_total[action] += 1


def _is_locked_out(row):
    """row is an admins/students sqlite3.Row. Returns True if this account
    is currently within its lockout window."""
    locked_until = row['locked_until']
    if not locked_until:
        return False
    try:
        return datetime.fromisoformat(locked_until) > datetime.now()
    except ValueError:
        return False


def _register_failed_login(table, row_id):
    """Increments the failed-attempt counter for one account and sets
    locked_until once it crosses the configured threshold. `table` is
    always a hardcoded literal ('students'/'admins') from call sites,
    never user input."""
    conn = get_db_connection()
    row = conn.execute(f'SELECT failed_attempts FROM {table} WHERE id=?', (row_id,)).fetchone()  # nosec B608
    attempts = (row['failed_attempts'] or 0) + 1 if row else 1
    locked_until = None
    if attempts >= cfg.LOCKOUT_MAX_FAILED_ATTEMPTS:
        locked_until = (datetime.now() + timedelta(minutes=cfg.LOCKOUT_DURATION_MINUTES)).isoformat()
    conn.execute(f'UPDATE {table} SET failed_attempts=?, locked_until=? WHERE id=?', (attempts, locked_until, row_id))  # nosec B608
    conn.commit()
    conn.close()
    return attempts, locked_until


def _clear_failed_logins(table, row_id):
    """`table` is always a hardcoded literal ('students'/'admins'), never user input."""
    execute_db(f'UPDATE {table} SET failed_attempts=0, locked_until=NULL WHERE id=?', (row_id,))  # nosec B608


def _is_attendance_locked_out(student_row):
    """student_row is a students sqlite3.Row. Returns True if this
    student's attendance-marking is currently within its lockout window
    (see ATTENDANCE_SECURITY_FAILURE_ACTIONS / config.ATTENDANCE_LOCKOUT_*)
    OR they've been automatically escalated for a high risk score (see
    _maybe_escalate_student()) — escalation blocks marking the same way,
    until an admin clears it, even though it's a broader/longer-lived
    flag than the narrow spoof/liveness lockout counter. Distinct from
    _is_locked_out(), which is about the login form."""
    try:
        if student_row['security_escalated']:
            return True
    except (IndexError, KeyError):
        pass
    locked_until = student_row['attendance_locked_until']
    if not locked_until:
        return False
    try:
        return datetime.fromisoformat(locked_until) > datetime.now()
    except ValueError:
        return False


def _register_attendance_security_failure(student_id):
    """Increments a student's attendance_security_failures counter
    (spoof-suspected / liveness-challenge-failed events only — see
    ATTENDANCE_SECURITY_FAILURE_ACTIONS) and sets attendance_locked_until
    once it crosses cfg.ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS. Returns
    (attempts, locked_until) — locked_until is None unless this call just
    crossed the threshold."""
    conn = get_db_connection()
    row = conn.execute('SELECT attendance_security_failures FROM students WHERE id=?', (student_id,)).fetchone()
    attempts = (row['attendance_security_failures'] or 0) + 1 if row else 1
    locked_until = None
    if attempts >= cfg.ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS:
        locked_until = (datetime.now() + timedelta(minutes=cfg.ATTENDANCE_LOCKOUT_DURATION_MINUTES)).isoformat()
    conn.execute('UPDATE students SET attendance_security_failures=?, attendance_locked_until=? WHERE id=?',
                 (attempts, locked_until, student_id))
    conn.commit()
    conn.close()
    return attempts, locked_until


def _clear_attendance_security_failures(student_id):
    execute_db('UPDATE students SET attendance_security_failures=0, attendance_locked_until=NULL WHERE id=?', (student_id,))


# --- Security monitoring: fingerprinting, sessions, risk, escalation ----
#
# All of this is heuristic, self-hosted signal built entirely from data
# this app already has (IPs, user agents, its own audit log) — there is
# no third-party fingerprinting/geo-IP service involved. See the
# 0008_add_security_monitoring migration's docstring for the schema.

def _record_device_fingerprint(student_id, fingerprint_hash, ip_address, user_agent):
    """Upserts a (student, fingerprint) pairing. Returns True if this is
    the FIRST time this fingerprint has been seen for this student. A
    no-op (returns False) if fingerprint_hash is empty — the client-side
    fingerprint (see static/app.js) is best-effort and may be missing
    for an old cached page or a browser blocking the script."""
    if not fingerprint_hash:
        return False
    existing = query_db('SELECT id FROM device_fingerprints WHERE student_id=? AND fingerprint_hash=?',
                         (student_id, fingerprint_hash), one=True)
    now = datetime.now().isoformat()
    if existing:
        execute_db('UPDATE device_fingerprints SET last_seen=?, seen_count=seen_count+1, ip_address=?, user_agent=? WHERE id=?',
                   (now, ip_address, user_agent, existing['id']))
        return False
    execute_db('''INSERT INTO device_fingerprints(student_id, fingerprint_hash, user_agent, ip_address, first_seen, last_seen, seen_count)
                  VALUES (?, ?, ?, ?, ?, ?, 1)''',
               (student_id, fingerprint_hash, user_agent, ip_address, now, now))
    return True


def _check_suspicious_device(student_id, fingerprint_hash, ip_address, user_agent):
    """Suspicious-device detection: records the current device (see
    _record_device_fingerprint()) and, if it's new AND the student
    already has at least one other known device on file, logs
    'suspicious_device_detected'. A student's very first-ever device is
    never flagged — everyone has to start somewhere."""
    if not fingerprint_hash:
        return
    prior_device_count = query_db('SELECT COUNT(*) as c FROM device_fingerprints WHERE student_id=?', (student_id,), one=True)['c']
    is_new = _record_device_fingerprint(student_id, fingerprint_hash, ip_address, user_agent)
    if is_new and prior_device_count > 0:
        log_audit('student', str(student_id), 'suspicious_device_detected',
                  details=f'new device (fingerprint {fingerprint_hash[:12]}...), {prior_device_count} known device(s) already on file')
        student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
        target = f'{student["name"]} ({student["roll_no"]})' if student else f'student #{student_id}'
        _create_security_notification('high', student_id, 'suspicious_device', f'{target} logged in from a new, unrecognized device.')


def _ip_network_prefix(ip_address):
    """A rough 'same network' grouping for an IPv4/IPv6 address — the
    /24 (first three octets) for IPv4, or None (treated as always
    'different') for anything else, including IPv6 or an unparseable
    value. Coarse on purpose: this is meant to catch "clearly a
    different network" (different city, different ISP, VPN toggle), not
    to fingerprint a precise location."""
    if not ip_address:
        return None
    parts = ip_address.split('.')
    if len(parts) == 4:
        return '.'.join(parts[:3])
    return None


def _check_network_change(student_id, ip_address):
    """Network-change / "impossible location" detection — an IP-address
    heuristic only (see NETWORK_CHANGE_* in config.py's comments; there
    is no bundled GeoIP database, so this cannot measure true physical
    distance). Compares the current login IP to the student's
    last_login_ip/last_login_at: a different network within
    NETWORK_CHANGE_IMPOSSIBLE_MINUTES logs 'impossible_location_suspected'
    (critical); within the wider NETWORK_CHANGE_WINDOW_MINUTES logs
    'network_change_detected' (medium) instead. No-op if there's no
    prior login on record, or the current/previous network match."""
    student = query_db('SELECT last_login_ip, last_login_at FROM students WHERE id=?', (student_id,), one=True)
    if not student or not student['last_login_ip'] or not student['last_login_at']:
        return
    if _ip_network_prefix(ip_address) == _ip_network_prefix(student['last_login_ip']):
        return
    try:
        last_login_at = datetime.fromisoformat(student['last_login_at'])
    except ValueError:
        return
    elapsed_minutes = (datetime.now() - last_login_at).total_seconds() / 60
    if elapsed_minutes <= get_effective_setting('NETWORK_CHANGE_IMPOSSIBLE_MINUTES'):
        log_audit('student', str(student_id), 'impossible_location_suspected',
                  details=f'network changed from {student["last_login_ip"]} to {ip_address} within {elapsed_minutes:.1f} min')
        target_student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
        target = f'{target_student["name"]} ({target_student["roll_no"]})' if target_student else f'student #{student_id}'
        _create_security_notification('critical', student_id, 'impossible_location',
                                       f'{target} logged in from a very different network only {elapsed_minutes:.1f} minutes after their last login.')
    elif elapsed_minutes <= get_effective_setting('NETWORK_CHANGE_WINDOW_MINUTES'):
        log_audit('student', str(student_id), 'network_change_detected',
                  details=f'network changed from {student["last_login_ip"]} to {ip_address} within {elapsed_minutes:.1f} min')


def _check_concurrent_session(student_id, ip_address, fingerprint_hash):
    """Concurrent-session detection: flags when another login session
    for this student is still active (no logout recorded) and was last
    seen within CONCURRENT_SESSION_WINDOW_MINUTES — meaning two sessions
    look genuinely simultaneous, not just an old forgotten-open tab. A
    second login from the SAME device+IP as the still-active one isn't
    flagged (that's just the same person continuing on the same
    device); a different device or IP is."""
    window_start = (datetime.now() - timedelta(minutes=get_effective_setting('CONCURRENT_SESSION_WINDOW_MINUTES'))).isoformat()
    active_sessions = query_db(
        '''SELECT ip_address, fingerprint_hash FROM student_login_sessions
           WHERE student_id=? AND ended_at IS NULL AND last_seen_at >= ?''',
        (student_id, window_start)
    )
    for row in active_sessions:
        if row['ip_address'] != ip_address or row['fingerprint_hash'] != fingerprint_hash:
            log_audit('student', str(student_id), 'concurrent_session_detected',
                      details=f'active session from {row["ip_address"]}, new login from {ip_address}')
            student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
            target = f'{student["name"]} ({student["roll_no"]})' if student else f'student #{student_id}'
            _create_security_notification('high', student_id, 'concurrent_session',
                                           f'{target} appears to have two active login sessions at once (from {row["ip_address"]} and {ip_address}).')
            return


def _start_login_session(student_id, ip_address, fingerprint_hash):
    """Creates a new student_login_sessions row and returns its token,
    to be stashed in the Flask session — see _check_concurrent_session()
    and the logout route, which marks it ended."""
    token = secrets.token_hex(16)
    now = datetime.now().isoformat()
    execute_db('''INSERT INTO student_login_sessions(student_id, session_token, ip_address, fingerprint_hash, created_at, last_seen_at)
                  VALUES (?, ?, ?, ?, ?, ?)''',
               (student_id, token, ip_address, fingerprint_hash, now, now))
    return token


def _end_login_session(session_token):
    if session_token:
        execute_db('UPDATE student_login_sessions SET ended_at=? WHERE session_token=? AND ended_at IS NULL',
                   (datetime.now().isoformat(), session_token))


def _compute_risk_score(student_id):
    """A 0-100 heuristic risk score for one student, from
    RISK_EVENT_WEIGHTS-weighted counts of their security events over the
    last SECURITY_RISK_WINDOW_DAYS. Returns (score, breakdown) — breakdown
    maps action -> its point contribution, for display. A rough signal
    for review, not a certainty: it reflects volume of suspicious
    signals, not confirmed wrongdoing."""
    since = (datetime.now() - timedelta(days=cfg.SECURITY_RISK_WINDOW_DAYS)).isoformat()
    rows = query_db(
        "SELECT action, COUNT(*) as c FROM audit_log WHERE actor_type='student' AND actor_name=? AND timestamp>=? GROUP BY action",
        (str(student_id), since)
    )
    breakdown = {}
    total = 0
    for row in rows:
        weight = RISK_EVENT_WEIGHTS.get(row['action'])
        if weight:
            contribution = weight * row['c']
            breakdown[row['action']] = contribution
            total += contribution
    return min(total, 100), breakdown


def _create_security_notification(severity, student_id, event_type, message):
    execute_db('''INSERT INTO security_notifications(created_at, severity, student_id, event_type, message, is_read)
                  VALUES (?, ?, ?, ?, ?, 0)''',
               (datetime.now().isoformat(), severity, student_id, event_type, message))


def _maybe_escalate_student(student_id):
    """Automatic escalation of high-risk accounts: if the student's
    current risk score (see _compute_risk_score()) is at or above
    SECURITY_RISK_ESCALATION_THRESHOLD and they aren't already escalated,
    flips students.security_escalated on (which blocks their attendance
    marking — see _is_attendance_locked_out()), logs a critical audit
    event, and raises an admin-facing security notification. Idempotent:
    an already-escalated student is left alone (no repeat notifications)
    until an admin explicitly clears it. Returns the computed score."""
    score, _breakdown = _compute_risk_score(student_id)
    student = query_db('SELECT name, roll_no, security_escalated FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        return score
    if score >= get_effective_setting('SECURITY_RISK_ESCALATION_THRESHOLD') and not student['security_escalated']:
        execute_db('UPDATE students SET security_escalated=1, security_escalated_at=? WHERE id=?',
                   (datetime.now().isoformat(), student_id))
        target = f'{student["name"]} ({student["roll_no"]})'
        log_audit('system', 'auto', 'security_risk_escalated', target=target, details=f'risk score {score}')
        _create_security_notification('critical', student_id, 'escalation', f'{target} was automatically escalated (risk score {score}/100).')
    return score


#
# Sessions store their scheduled date/time as separate TEXT columns
# ('dd-mm-yyyy' and 'HH:MM' — see admin_sessions()'s to_24h()/
# convert_date()) rather than one combined column, so several of these
# need to parse and recombine them.
#
# (_parse_session_date itself is defined further below, next to
# find_conflicting_session, which is its main caller.)


def _session_start_datetime(session_row):
    """Combines a session's date + time columns into one datetime, or
    None if either is missing/unparseable."""
    session_date = _parse_session_date(session_row['date'])
    if session_date is None:
        return None
    try:
        session_time = datetime.strptime(session_row['time'], '%H:%M').time()
    except (ValueError, TypeError):
        return None
    return datetime.combine(session_date, session_time)


def _attendance_counts_as_present(status):
    """Whether a given attendance status counts toward a student's
    attendance percentage — see LATE_COUNTS_AS_PRESENT in config.py."""
    return status == 'Present' or (status == 'Late' and cfg.LATE_COUNTS_AS_PRESENT)


def _late_entry_status(session_row):
    """Implements the late-entry rule for self-service attendance
    marking (see LATE_ENTRY_* in config.py, and a session's own
    grace_period_minutes/attendance_window_minutes overrides) — a no-op
    returning ('Present', None) if the feature is disabled, the
    session's date/time can't be parsed, or the session hasn't started
    yet by its own schedule (marking ahead of a slightly-early admin
    start is not treated as suspicious here). Returns (status, None)
    when marking is currently allowed (status is 'Present' or 'Late'),
    or (None, error_message) once the late window has closed entirely."""
    if not cfg.LATE_ENTRY_ENFORCEMENT_ENABLED:
        return 'Present', None
    start_dt = _session_start_datetime(session_row)
    if start_dt is None:
        return 'Present', None
    grace_minutes = _session_grace_period_minutes(session_row)
    window_minutes = _session_attendance_window_minutes(session_row)
    elapsed_minutes = (datetime.now() - start_dt).total_seconds() / 60
    if elapsed_minutes <= grace_minutes:
        return 'Present', None
    if elapsed_minutes <= window_minutes:
        return 'Late', None
    return None, (
        f'The attendance window for this session closed {window_minutes} minutes after it started. '
        'Please contact an administrator, or submit a correction request from your attendance history.'
    )


def _session_attendance_window_minutes(session_row):
    """Total minutes after a session's scheduled start during which
    attendance can be marked at all — a session's own
    attendance_window_minutes override if set (see admin_sessions()),
    else the global LATE_ENTRY_GRACE_MINUTES + LATE_ENTRY_LATE_WINDOW_MINUTES.
    Used both by the late-entry rule and by automatic session closing."""
    try:
        override = session_row['attendance_window_minutes']
    except (IndexError, KeyError):
        override = None
    if override is not None:
        return override
    return cfg.LATE_ENTRY_GRACE_MINUTES + cfg.LATE_ENTRY_LATE_WINDOW_MINUTES


def _session_grace_period_minutes(session_row):
    """A session's own grace_period_minutes override if set, else the
    global LATE_ENTRY_GRACE_MINUTES — see _late_entry_status()."""
    try:
        override = session_row['grace_period_minutes']
    except (IndexError, KeyError):
        override = None
    if override is not None:
        return override
    return cfg.LATE_ENTRY_GRACE_MINUTES


def _session_group_restriction_error(session_row, student_row):
    """A session can optionally be restricted to one branch and/or one
    semester (see restrict_branch/restrict_semester in admin_sessions())
    — the "student-group assignment" feature. Neither set (the default,
    and the only possibility before this existed) means the session is
    open to every student, unchanged from before. Returns an error
    message if this student isn't part of the assigned group, else
    None."""
    try:
        restrict_branch = session_row['restrict_branch']
        restrict_semester = session_row['restrict_semester']
    except (IndexError, KeyError):
        return None
    if restrict_branch and student_row['branch'] != restrict_branch:
        return 'This session is restricted to a different branch.'
    if restrict_semester and student_row['semester'] != restrict_semester:
        return 'This session is restricted to a different semester.'
    return None


def _maybe_auto_close_session(session_row):
    """Closes a single session (status -> 'completed', active -> 0) if
    it's still marked 'active' but its attendance window has fully
    elapsed — see SESSION_AUTO_CLOSE_ENABLED in config.py. Called from
    the hot marking/recognition paths, which only ever need to check the
    one session in front of them; see _auto_close_expired_sessions() for
    the bulk sweep used by listing pages. Returns the (possibly updated)
    session row, or the original if nothing changed or it doesn't apply
    (no row, not currently 'active', unparseable schedule, or the
    feature is disabled)."""
    if not session_row or not cfg.SESSION_AUTO_CLOSE_ENABLED:
        return session_row
    try:
        if session_row['status'] != 'active':
            return session_row
    except (IndexError, KeyError):
        return session_row
    start_dt = _session_start_datetime(session_row)
    if start_dt is None:
        return session_row
    window_minutes = _session_attendance_window_minutes(session_row)
    if datetime.now() <= start_dt + timedelta(minutes=window_minutes):
        return session_row
    execute_db("UPDATE sessions SET status='completed', active=0 WHERE id=?", (session_row['id'],))
    log_audit('system', 'auto', 'session_auto_closed', target=f"session #{session_row['id']}",
              details=f'attendance window ({window_minutes} min) elapsed')
    return query_db('SELECT * FROM sessions WHERE id=?', (session_row['id'],), one=True)


def _auto_close_expired_sessions():
    """Bulk version of _maybe_auto_close_session() — sweeps every
    currently-'active' session and closes any whose attendance window
    has elapsed. Called from listing pages (admin_sessions,
    get_active_sessions) so the list a person sees is accurate without
    needing a background scheduler, which this project doesn't run —
    see the README's documented trade-offs."""
    if not cfg.SESSION_AUTO_CLOSE_ENABLED:
        return
    for s in query_db("SELECT * FROM sessions WHERE status='active'"):
        _maybe_auto_close_session(s)


def _is_session_edit_locked(session_row):
    """True once a session is more than ATTENDANCE_EDIT_LOCK_DAYS in the
    past (measured from its end_date if it has one, else its date) —
    see ATTENDANCE_EDIT_LOCK_* in config.py. Applied to both the admin
    manual-override endpoint and new student correction requests, so a
    session's attendance record can't keep shifting indefinitely."""
    if not cfg.ATTENDANCE_EDIT_LOCK_ENABLED:
        return False
    reference_date = _parse_session_date(session_row['end_date'] or session_row['date'])
    if reference_date is None:
        return False
    return (datetime.now().date() - reference_date).days > cfg.ATTENDANCE_EDIT_LOCK_DAYS


def _insert_attendance_if_absent(student_id, session_id, status, timestamp, note=None, confidence=None):
    """Atomically inserts an attendance row only if one doesn't already
    exist for this (student_id, session_id) pair. The callers of this
    already check-then-insert (an early, cheap 'already marked' check
    before the expensive face-recognition work runs) — this closes the
    narrow race between that check and the actual insert, without
    requiring a schema-level UNIQUE constraint (the attendance table is
    also used, deliberately, as an append-only per-mark log elsewhere).
    confidence (0-1 cosine similarity) is recorded for recognition-
    confidence reporting — see /admin/session/<id> and CSV exports.
    Returns True if a row was inserted, False if one already existed."""
    conn = get_db_connection()
    cursor = conn.execute(
        '''INSERT INTO attendance(student_id, session_id, status, timestamp, note, confidence)
           SELECT ?, ?, ?, ?, ?, ?
           WHERE NOT EXISTS (SELECT 1 FROM attendance WHERE student_id=? AND session_id=?)''',
        (student_id, session_id, status, timestamp, note, confidence, student_id, session_id)
    )
    conn.commit()
    inserted = cursor.rowcount > 0
    conn.close()
    if inserted:
        _publish_session_update(session_id)
    return inserted


def _compute_attendance_report(subject_id=None, start_date=None, end_date=None, semester=None, trend_granularity='day'):
    """Builds the data for /admin/reports: a per-student attendance
    summary over the filtered sessions, plus a time-bucketed class-wide
    attendance-rate trend. start_date/end_date are date objects
    (inclusive) or None. Covers subject-wise, date-range, and semester
    reports through one filterable computation rather than three
    separate code paths — the caller supplies whichever filters apply."""
    all_sessions = query_db('SELECT * FROM sessions')
    filtered_sessions = []
    for s in all_sessions:
        if subject_id and str(s['subject_id']) != str(subject_id):
            continue
        parsed_date = _parse_session_date(s['date'])
        if parsed_date is None:
            continue
        if start_date and parsed_date < start_date:
            continue
        if end_date and parsed_date > end_date:
            continue
        row = dict(s)
        row['parsed_date'] = parsed_date
        filtered_sessions.append(row)

    students = query_db('SELECT * FROM students ORDER BY name')
    if semester:
        students = [s for s in students if s['semester'] == semester]

    if not filtered_sessions or not students:
        return [], []

    session_ids = [s['id'] for s in filtered_sessions]
    # `placeholders` below is only '?' chars joined by ',', values bound separately.
    placeholders = ','.join('?' for _ in session_ids)
    attendance_rows = query_db(f'SELECT student_id, session_id, status FROM attendance WHERE session_id IN ({placeholders})', tuple(session_ids))  # nosec B608
    status_by_pair = {(r['session_id'], r['student_id']): r['status'] for r in attendance_rows}

    total_sessions = len(filtered_sessions)
    report_rows = []
    for student in students:
        present_count = late_count = absent_count = 0
        for s in filtered_sessions:
            status = status_by_pair.get((s['id'], student['id']))
            if status == 'Late':
                late_count += 1
            elif status and _attendance_counts_as_present(status):
                present_count += 1
            else:
                absent_count += 1
        counted_present = present_count + (late_count if cfg.LATE_COUNTS_AS_PRESENT else 0)
        percentage = round(counted_present / total_sessions * 100, 1) if total_sessions else 0.0
        report_rows.append({
            'student_id': student['id'],
            'name': student['name'],
            'roll_no': student['roll_no'],
            'semester': student['semester'],
            'total_sessions': total_sessions,
            'present': present_count,
            'late': late_count,
            'absent': absent_count,
            'percentage': percentage,
            'below_threshold': percentage < cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT,
        })
    report_rows.sort(key=lambda r: r['percentage'])

    buckets: dict = {}
    for s in filtered_sessions:
        if trend_granularity == 'month':
            key = s['parsed_date'].strftime('%Y-%m')
        elif trend_granularity == 'week':
            iso_year, iso_week, _ = s['parsed_date'].isocalendar()
            key = f'{iso_year}-W{iso_week:02d}'
        else:
            key = s['parsed_date'].isoformat()
        bucket = buckets.setdefault(key, {'present': 0, 'total': 0})
        for student in students:
            status = status_by_pair.get((s['id'], student['id']))
            bucket['total'] += 1
            if status and _attendance_counts_as_present(status):
                bucket['present'] += 1
    trend = [
        {'label': k, 'percentage': round(v['present'] / v['total'] * 100, 1) if v['total'] else 0.0}
        for k, v in sorted(buckets.items())
    ]
    return report_rows, trend


def generate_captcha_text():
    alphabet = string.ascii_uppercase + string.digits
    # Excludes visually ambiguous characters (0/O, 1/I) to keep a
    # legitimate user's success rate reasonable.
    alphabet = ''.join(c for c in alphabet if c not in 'O0I1')
    # The CAPTCHA answer is a security-relevant secret (it gates login
    # attempts), so it needs a CSPRNG -- plain `random` is predictable
    # given enough samples and must not be used here.
    return ''.join(secrets.choice(alphabet) for _ in range(cfg.CAPTCHA_LENGTH))


def render_captcha_image(text):
    """Renders `text` as a distorted-enough PNG to block naive scripted
    login attempts, without depending on a third-party CAPTCHA service.
    The randomness below is purely cosmetic (line/dot noise positions,
    per-character jitter and color) -- it doesn't need to be
    unpredictable, since the actual CAPTCHA secret is `text` itself,
    generated via secrets.choice() in generate_captcha_text()."""
    width, height = 160, 60
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Background noise lines
    for _ in range(6):
        x1, y1 = random.randint(0, width), random.randint(0, height)  # nosec B311
        x2, y2 = random.randint(0, width), random.randint(0, height)  # nosec B311
        draw.line((x1, y1, x2, y2), fill=(random.randint(150, 200),) * 3, width=1)  # nosec B311

    try:
        font: Union[ImageFont.FreeTypeFont, ImageFont.ImageFont] = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 32
        )
    except OSError:
        font = ImageFont.load_default()

    for i, char in enumerate(text):
        x = 12 + i * (width - 24) // len(text) + random.randint(-3, 3)  # nosec B311
        y = random.randint(8, 18)  # nosec B311
        angle = random.randint(-25, 25)  # nosec B311
        char_img = Image.new('RGBA', (40, 40), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        color = (random.randint(0, 90), random.randint(0, 90), random.randint(0, 90))  # nosec B311
        char_draw.text((5, 0), char, font=font, fill=color)
        char_img = char_img.rotate(angle, expand=True)
        image.paste(char_img, (x, y), char_img)

    # Noise dots
    for _ in range(80):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)  # nosec B311
        draw.point((x, y), fill=(random.randint(120, 180),) * 3)  # nosec B311

    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return buf.getvalue()


def validate_password_strength(password):
    """Returns None if `password` meets the configured policy, or an
    error message string explaining what's missing."""
    if not password or len(password) < cfg.PASSWORD_MIN_LENGTH:
        return f'Password must be at least {cfg.PASSWORD_MIN_LENGTH} characters long.'
    if cfg.PASSWORD_REQUIRE_LETTER_AND_DIGIT:
        has_letter = any(c.isalpha() for c in password)
        has_digit = any(c.isdigit() for c in password)
        if not (has_letter and has_digit):
            return 'Password must contain at least one letter and one number.'
    return None


_embedder_net = None
_eye_cascade = None


def get_eye_cascade():
    """Loads (and caches) the bundled Haar eye cascade, used for face
    alignment and blink-based liveness."""
    global _eye_cascade
    if _eye_cascade is None:
        _eye_cascade = cv2.CascadeClassifier(cfg.EYE_CASCADE_PATH)
    return _eye_cascade


def get_embedder():
    """
    Loads (and caches) the pretrained OpenFace CNN used to turn a face crop
    into a 128-d embedding vector. See models/README.md for what this
    model is and where it came from.
    """
    global _embedder_net
    if _embedder_net is None:
        if not os.path.exists(cfg.EMBEDDER_MODEL_PATH):
            raise RuntimeError(
                f'Face embedding model not found at {cfg.EMBEDDER_MODEL_PATH}. '
                'See models/README.md.'
            )
        _embedder_net = cv2.dnn.readNetFromTorch(cfg.EMBEDDER_MODEL_PATH)
    return _embedder_net


def compute_embedding(face_rgb):
    """
    face_rgb: an RGB numpy array crop of a single detected face (any size —
    it's resized internally to what the model expects).
    Returns a 128-d, L2-normalized float32 embedding vector. Two
    embeddings from the same person land close together in this space
    (high cosine similarity); different people land far apart.
    """
    embedder = get_embedder()
    size = cfg.EMBEDDING_INPUT_SIZE
    # face_rgb is already RGB (this app converts to RGB early throughout),
    # so no channel swap needed here.
    blob = cv2.dnn.blobFromImage(face_rgb, 1.0 / 255, (size, size), (0, 0, 0), swapRB=False, crop=False)
    embedder.setInput(blob)
    vec = embedder.forward()
    return vec.flatten().astype(np.float32)


def cosine_similarity(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def store_embedding(student_id, embedding, image_filename=None, quality_score=None, model_version=None):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        '''INSERT INTO face_embeddings(student_id, embedding, created_at, image_filename, quality_score, model_version)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (student_id, embedding.astype(np.float32).tobytes(), datetime.now().isoformat(),
         image_filename, quality_score, model_version or cfg.EMBEDDER_MODEL_VERSION)
    )
    conn.commit()
    conn.close()


def delete_student_embeddings(student_id):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute('DELETE FROM face_embeddings WHERE student_id=?', (student_id,))
    conn.commit()
    conn.close()


def _delete_student_face_files(student_id):
    """Removes every saved face-crop image file for a student from
    DATA_DIR. Shared by full student deletion, admin face-data reset,
    and student self-service re-enrollment."""
    try:
        for f in os.listdir(DATA_DIR):
            if f.startswith(f'User.{student_id}.'):
                os.remove(os.path.join(DATA_DIR, f))
    except OSError as e:
        logger.warning(f'Error deleting face image files for student #{student_id}: {e}', exc_info=True)


def _reset_student_face_data(student_id):
    """Wipes all of one student's face enrollment data — every stored
    embedding, their quality/filename metadata, and the underlying image
    files — resetting them to 'not enrolled'. Shared by the admin
    'Reset Face Data' action and the student's own re-enrollment
    workflow (see reenroll_face() / admin_reset_face_data())."""
    delete_student_embeddings(student_id)
    _delete_student_face_files(student_id)
    execute_db('UPDATE students SET photo_count=0 WHERE id=?', (student_id,))


def _delete_single_embedding(student_id, embedding_id):
    """Removes one specific registered face photo (its face_embeddings
    row and, if it has one, the underlying image file) — the "remove
    individual registered face images" feature, as opposed to wiping
    everything via _reset_student_face_data(). Scoped to student_id so a
    caller can't delete another student's row by guessing an id. Returns
    True if a row was actually deleted."""
    row = query_db('SELECT image_filename FROM face_embeddings WHERE id=? AND student_id=?', (embedding_id, student_id), one=True)
    if not row:
        return False
    if row['image_filename']:
        with contextlib.suppress(OSError):  # file already gone -- the DB row is still the source of truth to remove
            os.remove(os.path.join(DATA_DIR, row['image_filename']))
    execute_db('DELETE FROM face_embeddings WHERE id=? AND student_id=?', (embedding_id, student_id))
    new_total = len(get_student_embeddings(student_id))
    execute_db('UPDATE students SET photo_count=? WHERE id=?', (new_total, student_id))
    return True


def get_all_embeddings():
    """Every stored (student_id, embedding_ndarray) pair, across all students."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT student_id, embedding FROM face_embeddings').fetchall()
    conn.close()
    return [(row['student_id'], np.frombuffer(row['embedding'], dtype=np.float32)) for row in rows]


def get_student_embeddings(student_id):
    """All stored embeddings belonging to one specific student."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT embedding FROM face_embeddings WHERE student_id=?', (student_id,)).fetchall()
    conn.close()
    return [np.frombuffer(row['embedding'], dtype=np.float32) for row in rows]


def find_best_match(embedding, gallery=None, top_k=None):
    """
    1:N identification: compares `embedding` against every (student_id,
    embedding) pair in `gallery` (defaults to the full stored gallery) and
    returns (best_student_id, best_similarity) — or (None, 0.0) if the
    gallery is empty. This does NOT apply any threshold itself; callers
    decide what similarity counts as a match.

    top_k (defaults to cfg.MATCH_TOP_K, which itself defaults to 1)
    controls HOW a candidate's score is computed:
      - top_k <= 1 (the default): each candidate's score is their single
        closest stored embedding — exactly the original behavior, so
        MATCH_TOP_K=1 is a strict no-op.
      - top_k > 1: each candidate's score is the AVERAGE of their top_k
        closest stored embeddings (fewer, if they have fewer than top_k
        stored) — see config.MATCH_TOP_K's comment for why this can be a
        more robust match than any single embedding.
    """
    if gallery is None:
        gallery = get_all_embeddings()
    if top_k is None:
        top_k = cfg.MATCH_TOP_K

    if top_k <= 1:
        best_id, best_sim = None, -1.0
        for student_id, stored_embedding in gallery:
            sim = cosine_similarity(embedding, stored_embedding)
            if sim > best_sim:
                best_sim = sim
                best_id = student_id
        if best_id is None:
            return None, 0.0
        return int(best_id), best_sim

    sims_by_student = defaultdict(list)
    for student_id, stored_embedding in gallery:
        sims_by_student[int(student_id)].append(cosine_similarity(embedding, stored_embedding))
    best_id, best_avg = None, -1.0
    for student_id, sims in sims_by_student.items():
        sims.sort(reverse=True)
        top = sims[:top_k]
        avg = sum(top) / len(top)
        if avg > best_avg:
            best_avg = avg
            best_id = student_id
    if best_id is None:
        return None, 0.0
    return best_id, best_avg


def detect_faces_multiscale(face_cascade, gray, detect_params):
    """Thin wrapper around detectMultiScale, returning a plain list (not
    whatever array-like type OpenCV returns) sorted largest-first."""
    faces = face_cascade.detectMultiScale(gray, **detect_params)
    return sorted(list(faces), key=lambda f: f[2] * f[3], reverse=True)


def detect_largest_face(face_cascade, gray, detect_params):
    """Runs Haar cascade detection and returns the (x, y, w, h) box of the
    largest detected face, or None if no face was found."""
    faces = detect_faces_multiscale(face_cascade, gray, detect_params)
    if len(faces) == 0:
        return None
    return faces[0]


def detect_single_face(face_cascade, gray, detect_params):
    """Like detect_largest_face, but returns a (box, error_message) pair.
    error_message is set (and box is None) if no face was found, or if
    more than one face was found and cfg.REJECT_MULTIPLE_FACES is on —
    rather than silently proceeding with whichever face happens to be
    largest."""
    faces = detect_faces_multiscale(face_cascade, gray, detect_params)
    if len(faces) == 0:
        return None, 'No clear face detected! Please look directly at the camera in a well-lit area.'
    if len(faces) > 1 and cfg.REJECT_MULTIPLE_FACES:
        return None, 'Multiple faces detected in frame. Please make sure only you are visible to the camera.'
    return faces[0], None


def align_face_crop(gray, rgb, face_box, eye_cascade):
    """
    Improves embedding quality by leveling the face crop before it's fed
    to the embedder: detects both eyes within the face box (using the
    bundled Haar eye cascade), rotates the source image so the eye line
    is horizontal, then re-crops the face region from the rotated image.

    Falls back to the plain (unrotated) crop if eyes can't be reliably
    found — this is a best-effort improvement, not a hard requirement.

    A modern learned detector (e.g. YuNet) would give more reliable
    5-point landmarks for this than a Haar eye cascade does — that model's
    weights weren't reachable from this project's build environment (see
    models/README.md) — so this is the practical alternative: still Haar
    cascade for detection, but now with an alignment pass before
    embedding, which is the main accuracy benefit alignment provides.
    """
    x, y, w, h = face_box
    plain_crop = rgb[y:y + h, x:x + w]

    face_gray = gray[y:y + h, x:x + w]
    eyes = eye_cascade.detectMultiScale(face_gray, scaleFactor=1.1, minNeighbors=5, minSize=(15, 15))
    if len(eyes) < 2:
        return plain_crop

    # Take the two largest eye detections, then order them left/right by
    # x position (within the face crop).
    eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
    eyes = sorted(eyes, key=lambda e: e[0])
    (ex1, ey1, ew1, eh1), (ex2, ey2, ew2, eh2) = eyes
    left_eye_center = (x + ex1 + ew1 / 2, y + ey1 + eh1 / 2)
    right_eye_center = (x + ex2 + ew2 / 2, y + ey2 + eh2 / 2)

    dy = right_eye_center[1] - left_eye_center[1]
    dx = right_eye_center[0] - left_eye_center[0]
    if dx == 0:
        return plain_crop
    angle = np.degrees(np.arctan2(dy, dx))

    # Sanity bound: a legitimate near-frontal face shouldn't need a huge
    # rotation to level its eyes. A large angle usually means the eye
    # cascade found something spurious, not real eyes — fall back rather
    # than apply a distorting rotation.
    if abs(angle) > 30:
        return plain_crop

    face_center = (x + w / 2, y + h / 2)
    rotation_matrix = cv2.getRotationMatrix2D(face_center, angle, 1.0)
    rotated = cv2.warpAffine(rgb, rotation_matrix, (rgb.shape[1], rgb.shape[0]))

    aligned_crop = rotated[y:y + h, x:x + w]
    if aligned_crop.size == 0:
        return plain_crop
    return aligned_crop


def count_visible_eyes(gray, face_box, eye_cascade):
    x, y, w, h = face_box
    face_gray = gray[y:y + h, x:x + w]
    eyes = eye_cascade.detectMultiScale(face_gray, scaleFactor=1.1, minNeighbors=5, minSize=(15, 15))
    return len(eyes)


def check_blink_transition(frames_gray, face_boxes, eye_cascade):
    """
    Basic blink-based liveness signal: looks across a burst of frames for
    an eyes-visible -> eyes-not-visible -> eyes-visible pattern (or at
    minimum a clear visible/not-visible transition), which a static photo
    or a screen displaying a single frozen frame can't produce.

    frames_gray and face_boxes are parallel lists — one grayscale frame
    and that frame's already-detected face box, in capture order.

    This is a heuristic, not exact blink detection: Haar eye-cascade
    detection is noisy (glasses, angle, and lighting all affect it), so
    this looks for *any* clear transition rather than a strict
    open-closed-open triple, to avoid over-rejecting genuine attempts.
    """
    eye_counts = [count_visible_eyes(g, box, eye_cascade) for g, box in zip(frames_gray, face_boxes)]
    saw_eyes_visible = any(c >= 2 for c in eye_counts)
    saw_eyes_hidden = any(c == 0 for c in eye_counts)
    return saw_eyes_visible and saw_eyes_hidden


def assess_image_quality(gray_crop):
    """Returns a list of quality issues (empty list = no issues) found in
    a face crop, used to reject blurry/too-dark/too-bright registration
    photos before they're turned into an embedding."""
    if not cfg.IMAGE_QUALITY_CHECK_ENABLED:
        return []
    issues = []
    laplacian_variance = cv2.Laplacian(gray_crop, cv2.CV_64F).var()
    if laplacian_variance < get_effective_setting('IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE'):
        issues.append('blurry')
    brightness = float(np.mean(gray_crop))
    if brightness < cfg.IMAGE_QUALITY_MIN_BRIGHTNESS:
        issues.append('too dark')
    elif brightness > cfg.IMAGE_QUALITY_MAX_BRIGHTNESS:
        issues.append('too bright/overexposed')
    return issues


def _compute_quality_score(gray_crop):
    """A 0-100 heuristic enrollment quality score for one face crop, from
    the same sharpness (Laplacian variance) and exposure (mean
    brightness) signals as assess_image_quality() — but a continuous
    score rather than a pass/fail gate. Used for the enrollment-status
    feature (Not enrolled/Pending/Complete) and the re-enrollment
    reminder; NOT used to reject a photo at capture time — that's still
    assess_image_quality()'s job. This is a heuristic proxy for "will
    this photo make a good reference embedding", not a guarantee."""
    laplacian_variance = cv2.Laplacian(gray_crop, cv2.CV_64F).var()
    min_sharpness = max(get_effective_setting('IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE'), 1e-6)
    sharpness_score = min(100.0, (laplacian_variance / min_sharpness) * 50.0)
    brightness = float(np.mean(gray_crop))
    ideal_mid = (cfg.IMAGE_QUALITY_MIN_BRIGHTNESS + cfg.IMAGE_QUALITY_MAX_BRIGHTNESS) / 2
    half_range = max((cfg.IMAGE_QUALITY_MAX_BRIGHTNESS - cfg.IMAGE_QUALITY_MIN_BRIGHTNESS) / 2, 1e-6)
    exposure_score = max(0.0, 100.0 - (abs(brightness - ideal_mid) / half_range) * 100.0)
    return round(min(100.0, sharpness_score * 0.6 + exposure_score * 0.4), 1)


def _enrollment_status(student_id):
    """Face-enrollment status for one student — 'not_enrolled' (zero
    stored photos), 'pending' (some photos, but fewer than
    ENROLLMENT_MIN_PHOTOS or below-threshold average quality — see
    ENROLLMENT_QUALITY_REENROLL_THRESHOLD), or 'complete'. Returns a dict
    with photo_count and avg_quality alongside status, for display."""
    rows = query_db('SELECT quality_score FROM face_embeddings WHERE student_id=?', (student_id,))
    count = len(rows)
    if count == 0:
        return {'status': 'not_enrolled', 'photo_count': 0, 'avg_quality': None}
    scores = [r['quality_score'] for r in rows if r['quality_score'] is not None]
    avg_quality = round(sum(scores) / len(scores), 1) if scores else None
    needs_more_photos = count < cfg.ENROLLMENT_MIN_PHOTOS
    quality_low = avg_quality is not None and avg_quality < cfg.ENROLLMENT_QUALITY_REENROLL_THRESHOLD
    if needs_more_photos or quality_low:
        return {'status': 'pending', 'photo_count': count, 'avg_quality': avg_quality}
    return {'status': 'complete', 'photo_count': count, 'avg_quality': avg_quality}


# Runtime-configurable settings, backing /admin/recognition-settings and
# /admin/security-settings — an admin can override any of these from the
# DB (app_settings table) without editing the environment and
# restarting. Every other config value is still read straight from
# cfg/env, unchanged. Keys map to the caster used when reading a stored
# override back out. Split into two named subsets purely for which admin
# page shows which fields; get_effective_setting()/set_effective_setting()
# work the same way for all of them via the merged RUNTIME_CONFIGURABLE_SETTINGS.
RECOGNITION_SETTINGS_KEYS = {
    'RECOGNIZE_MATCH_THRESHOLD': float,
    'MARK_ATTENDANCE_MATCH_THRESHOLD': float,
    'DUPLICATE_FACE_MATCH_THRESHOLD': float,
    'ANTI_SPOOF_MAX_PERIODICITY_RATIO': float,
    'IMAGE_QUALITY_MIN_LAPLACIAN_VARIANCE': float,
}
SECURITY_SETTINGS_KEYS = {
    'SECURITY_RISK_ESCALATION_THRESHOLD': int,
    'CONCURRENT_SESSION_WINDOW_MINUTES': int,
    'NETWORK_CHANGE_WINDOW_MINUTES': int,
    'NETWORK_CHANGE_IMPOSSIBLE_MINUTES': int,
}
RUNTIME_CONFIGURABLE_SETTINGS = {**RECOGNITION_SETTINGS_KEYS, **SECURITY_SETTINGS_KEYS}
# Backward-compatible alias (recognition-settings code originally read this name).
RUNTIME_CONFIGURABLE_THRESHOLDS = RECOGNITION_SETTINGS_KEYS


def get_effective_setting(name):
    """Returns the admin-configured runtime override for `name` (see
    /admin/recognition-settings and /admin/security-settings) if one has
    been saved to the app_settings table, else falls back to
    getattr(cfg, name) — the .env/config.py default. Only looks at
    app_settings for names in RUNTIME_CONFIGURABLE_SETTINGS. Falls back
    to the cfg default on any database error too, since some callers
    (e.g. assess_image_quality()) are also exercised as plain functions
    without a database configured (unit tests operating on a raw image
    array), and a settings lookup failing should never block face
    detection/quality checks from running at all."""
    try:
        row = query_db('SELECT value FROM app_settings WHERE key=?', (name,), one=True)
    except sqlite3.Error:
        return getattr(cfg, name)
    if row is None:
        return getattr(cfg, name)
    caster = RUNTIME_CONFIGURABLE_SETTINGS.get(name, str)
    try:
        return caster(row['value'])
    except (TypeError, ValueError):
        return getattr(cfg, name)


def set_effective_setting(name, value, admin_user):
    execute_db(
        '''INSERT INTO app_settings(key, value, updated_at, updated_by) VALUES(?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by''',
        (name, str(value), datetime.now().isoformat(), admin_user)
    )


def is_ip_allowed_for_attendance(ip_address, session_row=None):
    """True if attendance-marking is permitted from this IP. A
    session-specific allowed_networks list (see admin_sessions()) — if
    the session has one set — REPLACES the global
    ATTENDANCE_ALLOWED_NETWORKS restriction for that one session; no
    restriction configured anywhere (the default) always returns True."""
    networks = cfg.ATTENDANCE_ALLOWED_NETWORKS
    if session_row is not None:
        try:
            session_networks = session_row['allowed_networks']
        except (IndexError, KeyError):
            session_networks = None
        if session_networks:
            networks = [n.strip() for n in session_networks.split(',') if n.strip()]
    if not networks:
        return True
    if not ip_address:
        return False
    try:
        addr = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    for network in networks:
        try:
            if '/' in network:
                if addr in ipaddress.ip_network(network, strict=False):
                    return True
            elif addr == ipaddress.ip_address(network):
                return True
        except ValueError:
            continue  # skip a malformed entry rather than failing every request
    return False


def _parse_session_date(date_str):
    """Sessions store dates as dd-mm-yyyy (see admin_sessions()). Returns
    a date object, or None if unparseable."""
    try:
        return datetime.strptime(date_str, '%d-%m-%Y').date()
    except (ValueError, TypeError):
        return None


def find_conflicting_session(date, end_date, time, end_time, exclude_session_id=None):
    """
    Returns the first existing session whose date+time window overlaps
    the given candidate window, or None if there's no conflict. A
    student can't physically attend two overlapping sessions, so this
    checks across ALL subjects, not just the same one.

    Dates are dd-mm-yyyy strings, times are HH:MM 24h strings (both as
    stored in the sessions table).
    """
    candidate_start_date = _parse_session_date(date)
    candidate_end_date = _parse_session_date(end_date) or candidate_start_date
    if candidate_start_date is None:
        return None  # can't evaluate, don't block on a data problem

    existing_sessions = query_db('SELECT * FROM sessions')
    for existing in existing_sessions:
        if exclude_session_id is not None and existing['id'] == exclude_session_id:
            continue
        try:
            if existing['status'] == 'cancelled':
                continue  # a cancelled session never happened -- its slot is free
        except (IndexError, KeyError):
            pass
        existing_start = _parse_session_date(existing['date'])
        existing_end = _parse_session_date(existing['end_date']) or existing_start
        if existing_start is None:
            continue

        dates_overlap = candidate_start_date <= existing_end and existing_start <= candidate_end_date
        if not dates_overlap:
            continue

        # HH:MM strings compare correctly lexically.
        times_overlap = time < (existing['end_time'] or '23:59') and (existing['time'] or '00:00') < end_time
        if times_overlap:
            return existing
    return None


def get_face_profile(student_id):
    conn = sqlite3.connect(FACE_DATABASE_PATH)
    cursor = conn.execute('SELECT id, name, gender, section FROM people WHERE id=?', (student_id,))
    profile = cursor.fetchone()
    conn.close()
    return profile


def save_face_profile(student_id, name, gender, section):
    conn = sqlite3.connect(FACE_DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM people WHERE id=?', (student_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO people(id, name, gender, section) VALUES(?, ?, ?, ?)', (student_id, name, gender, section))
    else:
        cursor.execute('UPDATE people SET name=?, gender=?, section=? WHERE id=?', (name, gender, section, student_id))
    conn.commit()
    conn.close()


def parse_base64_image(data):
    if ',' in data:
        data = data.split(',', 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data)))


def save_face_images(student_id, images):
    """
    For each captured registration image: detect the face, check its
    quality, save a color crop to disk (for provenance / so embeddings
    can be recomputed later — see migrate_embeddings.py), align it using
    detected eye positions, and immediately compute + store its
    embedding. There is no separate "training" step: adding a student's
    face is just inserting embedding rows, so it's O(1) per image rather
    than reprocessing the whole dataset.

    Returns (saved_count, skip_reasons) — skip_reasons is a list of short
    strings, one per image that didn't get stored, explaining why (e.g.
    'blurry', 'multiple faces detected'), so the caller can give the
    person useful feedback instead of a silent partial failure.
    """
    existing_images = [name for name in os.listdir(DATA_DIR) if name.startswith(f'User.{student_id}.')]
    count = len(existing_images)
    saved = 0
    skip_reasons = []
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    eye_cascade = get_eye_cascade()
    for image_data in images:
        image = parse_base64_image(image_data)
        frame = np.array(image.convert('RGB'))
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        box, error = detect_single_face(face_cascade, gray, cfg.REGISTRATION_DETECT_PARAMS)
        if box is None:
            skip_reasons.append('multiple faces detected' if error and 'Multiple' in error else 'no face detected')
            continue

        (x, y, w, h) = box
        face_crop_gray = gray[y:y+h, x:x+w]
        quality_issues = assess_image_quality(face_crop_gray)
        if quality_issues:
            skip_reasons.append(', '.join(quality_issues))
            continue
        if cfg.ANTI_SPOOF_ENABLED and face_security.is_likely_screen_replay(face_crop_gray, get_effective_setting('ANTI_SPOOF_MAX_PERIODICITY_RATIO')):
            skip_reasons.append('looks like a photo of a screen or printed photo, not a live face')
            continue

        face_crop_rgb = align_face_crop(gray, frame, box, eye_cascade)

        face_img = Image.fromarray(face_crop_rgb)
        count += 1
        filename = f'User.{student_id}.{count}.jpg'
        face_img.save(os.path.join(DATA_DIR, filename))

        embedding = compute_embedding(face_crop_rgb)
        quality_score = _compute_quality_score(face_crop_gray)
        store_embedding(student_id, embedding, image_filename=filename, quality_score=quality_score)
        saved += 1
    return saved, skip_reasons


def _face_crop_pair_has_motion(gray1, box1, gray2, box2):
    """Shared by check_liveness and check_liveness_sequence: True if two
    face crops (each from its own frame + detected box) differ enough to
    suggest genuine motion between frames, rather than a static image."""
    (x1, y1, w1, h1) = box1
    (x2, y2, w2, h2) = box2
    crop1 = gray1[y1:y1 + h1, x1:x1 + w1]
    crop2 = gray2[y2:y2 + h2, x2:x2 + w2]
    size = (100, 100)
    crop1 = cv2.resize(crop1, size)
    crop2 = cv2.resize(crop2, size)
    mean_diff = float(np.mean(cv2.absdiff(crop1, crop2)))
    return mean_diff >= cfg.LIVENESS_MIN_MEAN_PIXEL_DIFF


def check_liveness(face_cascade, gray1, gray2, detect_params):
    """
    Basic motion-based liveness check (see config.LIVENESS_CHECK_ENABLED for
    the full explanation). Detects a face in each of two frames captured a
    short moment apart and requires enough pixel-level difference between
    the two face crops to suggest a live, moving subject rather than a
    static photo or screen held in front of the camera.

    Returns True if the pair looks "live" (or if the check is disabled),
    False if it looks static. Also returns False if a face can't be found
    in both frames, since that's already handled as a detection failure by
    the caller.
    """
    if not cfg.LIVENESS_CHECK_ENABLED:
        return True

    faces1 = face_cascade.detectMultiScale(gray1, **detect_params)
    faces2 = face_cascade.detectMultiScale(gray2, **detect_params)
    if len(faces1) == 0 or len(faces2) == 0:
        return False

    faces1 = sorted(faces1, key=lambda f: f[2] * f[3], reverse=True)
    faces2 = sorted(faces2, key=lambda f: f[2] * f[3], reverse=True)
    return _face_crop_pair_has_motion(gray1, faces1[0], gray2, faces2[0])


def check_liveness_sequence(face_cascade, eye_cascade, frames_gray, detect_params):
    """
    Extended liveness check across a burst of frames (cfg.LIVENESS_FRAME_COUNT
    of them), combining two independent signals:

      1. Motion: at least half of consecutive frame pairs must show
         pixel-level change in the face region (same idea as
         check_liveness, just applied across more than one pair).
      2. Blink (if cfg.LIVENESS_REQUIRE_BLINK): the frame burst must show
         a detectable eyes-visible / eyes-not-visible transition.

    A video replay of the real person has genuine motion and would still
    likely pass signal 1 alone — that's the known limitation of motion-only
    liveness. Requiring a blink too raises the bar (a replay needs to
    contain a blink at the right moment), though it's still not
    production-grade anti-spoofing against a sufficiently prepared replay.

    Returns (is_live: bool, face_boxes: list) — face_boxes is the detected
    box per frame (None for frames with no detected face), so the caller
    can reuse them instead of re-running detection.
    """
    face_boxes = [detect_largest_face(face_cascade, g, detect_params) for g in frames_gray]
    if any(box is None for box in face_boxes):
        return False, face_boxes

    if cfg.LIVENESS_CHECK_ENABLED:
        motion_votes = sum(
            _face_crop_pair_has_motion(frames_gray[i], face_boxes[i], frames_gray[i + 1], face_boxes[i + 1])
            for i in range(len(frames_gray) - 1)
        )
        if motion_votes < (len(frames_gray) - 1) / 2:
            return False, face_boxes

    if cfg.LIVENESS_REQUIRE_BLINK and not check_blink_transition(frames_gray, face_boxes, eye_cascade):
        return False, face_boxes

    return True, face_boxes


# --- Active liveness challenge (see face_security.py + config.ACTIVE_LIVENESS_*) ---

def _issue_liveness_challenge():
    """Picks a random challenge, stores it server-side in the session
    (never trust a client-reported challenge type — see
    _consume_liveness_challenge), and returns the dict to send back to
    the client."""
    challenge = face_security.generate_liveness_challenge(cfg.ACTIVE_LIVENESS_CHALLENGE_TYPES)
    session['liveness_challenge'] = {
        'type': challenge['type'],
        'token': challenge['token'],
        'issued_at': datetime.now().isoformat(),
    }
    return challenge


def _consume_liveness_challenge(token):
    """Validates and single-use-consumes a challenge token against what
    was actually issued to this session (see _issue_liveness_challenge).
    Always pops the stored challenge — valid, invalid, or expired —
    before returning, so a token can never be checked against more than
    once regardless of the outcome. Returns (challenge_type, error) —
    challenge_type is None with error set on any failure."""
    stored = session.pop('liveness_challenge', None)
    if not stored:
        return None, 'No active liveness challenge found — please try again.'
    if not token or stored.get('token') != token:
        return None, 'Liveness challenge did not match — please try again.'
    try:
        issued_at = datetime.fromisoformat(stored['issued_at'])
    except (KeyError, ValueError):
        return None, 'Liveness challenge is invalid — please try again.'
    age_seconds = (datetime.now() - issued_at).total_seconds()
    if age_seconds > cfg.ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS:
        return None, 'Liveness challenge expired — please try again.'
    return stored['type'], None


def _verify_active_challenge(challenge_type, frames_gray, face_boxes, eye_cascade):
    """Thin wrapper around face_security.verify_challenge_response that
    supplies the one signal (blink detection) face_security.py can't
    compute on its own — it needs the grayscale frames and eye cascade,
    which stay in app.py alongside the rest of the OpenCV-cascade-using
    code. Only actually runs blink detection when the challenge calls for
    it, to avoid the extra (cheap, but unnecessary) work otherwise."""
    blink_detected = False
    if challenge_type == 'blink':
        blink_detected = check_blink_transition(frames_gray, face_boxes, eye_cascade)
    return face_security.verify_challenge_response(
        challenge_type, face_boxes, blink_detected,
        cfg.ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO,
        cfg.ACTIVE_LIVENESS_HEAD_NOD_MIN_SHIFT_RATIO,
    )


def recognize_face(image_data):
    """1:N identification: match the single face in `image_data` against
    the full embedding gallery. Returns (student_id, similarity, error) —
    student_id/similarity are None with error set if no face (or, when
    cfg.REJECT_MULTIPLE_FACES is on, more than one face) was found, or if
    the face looks like a screen/print replay (see
    cfg.ANTI_SPOOF_ENABLED); otherwise error is None and student_id is
    set only if the best match clears cfg.RECOGNIZE_MATCH_THRESHOLD
    (student_id may still be None with no error, meaning a face was found
    but didn't match anyone)."""
    gallery = get_all_embeddings()
    image = parse_base64_image(image_data).convert('RGB')
    frame = np.array(image)
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    box, error = detect_single_face(face_cascade, gray, cfg.RECOGNIZE_DETECT_PARAMS)
    if box is None:
        return None, None, error
    if not gallery:
        return None, None, None
    (x, y, w, h) = box
    if cfg.ANTI_SPOOF_ENABLED:
        face_crop_gray = gray[y:y + h, x:x + w]
        if face_security.is_likely_screen_replay(face_crop_gray, get_effective_setting('ANTI_SPOOF_MAX_PERIODICITY_RATIO')):
            return None, None, 'This looks like it might be a photo or screen rather than a live camera.'
    face_crop_rgb = align_face_crop(gray, frame, box, get_eye_cascade())
    embedding = compute_embedding(face_crop_rgb)
    student_id, similarity = find_best_match(embedding, gallery)
    if student_id is not None and similarity >= get_effective_setting('RECOGNIZE_MATCH_THRESHOLD'):
        return student_id, similarity, None
    return None, similarity, None


@app.route('/')
def home():
    if session.get('user_type') == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif session.get('user_type') == 'student':
        return redirect(url_for('student_login'))
    return render_template('index.html')


@app.before_request
def restrict_access():
    allowed_routes = ['home', 'login', 'student_login', 'student_register', 'static', 'test_image', 'get_active_sessions']
    if request.endpoint in allowed_routes or not request.endpoint:
        return

    if request.path.startswith('/admin/') and session.get('user_type') != 'admin':
        flash('Admin access required', 'error')
        return redirect(url_for('login'))

    if request.path.startswith('/student/'):
        # Some student routes might be public (login, register), handled by allowed_routes
        # But history and attend should be protected
        protected_student_routes = ['student_history', 'student_attend', 'student_mark_attendance', 'student_profile', 'student_change_password', 'student_register_face', 'student_liveness_challenge', 'request_attendance_correction', 'student_face_photos', 'student_face_photo_image', 'delete_own_face_photo', 'reenroll_face']
        if request.endpoint in protected_student_routes and session.get('user_type') != 'student':
            flash('Student login required', 'error')
            return redirect(url_for('student_login'))


@app.route('/captcha-image')
def captcha_image():
    text = generate_captcha_text()
    session['captcha_answer'] = text
    session['captcha_issued_at'] = datetime.now().isoformat()
    png_bytes = render_captcha_image(text)
    return Response(png_bytes, mimetype='image/png', headers={'Cache-Control': 'no-store'})


def _check_captcha(submitted):
    """Validates and consumes the CAPTCHA answer stored in the session.
    Always clears it afterward (pass or fail) so it can't be reused."""
    if not cfg.CAPTCHA_ENABLED:
        return True
    expected = session.pop('captcha_answer', None)
    session.pop('captcha_issued_at', None)
    if not expected or not submitted:
        return False
    return submitted.strip().upper() == expected.upper()


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit(lambda: cfg.RATE_LIMIT_LOGIN, methods=['POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        captcha_response = request.form.get('captcha')

        if not _check_captcha(captcha_response):
            flash('Incorrect CAPTCHA. Please try again.', 'error')
            log_audit('anonymous', username, 'admin_login_failed', details='captcha failed')
            return render_template('login.html')

        admin = query_db('SELECT * FROM admins WHERE username=?', (username,), one=True)

        if admin and _is_locked_out(admin):
            flash('This account is temporarily locked due to repeated failed login attempts. Please try again later.', 'error')
            log_audit('anonymous', username, 'admin_login_blocked', details='account locked')
            return render_template('login.html')

        if admin and check_password_hash(admin['password'], password):
            _clear_failed_logins('admins', admin['id'])
            session.clear()
            session.permanent = True
            session['admin_user'] = username
            session['user_type'] = 'admin'
            log_audit('admin', username, 'admin_login_success')
            return redirect(url_for('admin_dashboard'))

        if admin:
            attempts, locked_until = _register_failed_login('admins', admin['id'])
            if locked_until:
                flash(f'Too many failed attempts. This account is locked for {cfg.LOCKOUT_DURATION_MINUTES} minutes.', 'error')
            else:
                flash('Invalid username or password', 'error')
        else:
            flash('Invalid username or password', 'error')
        log_audit('anonymous', username, 'admin_login_failed')
    return render_template('login.html')


@app.route('/logout')
def logout():
    actor_name = session.get('admin_user') or session.get('student_id')
    actor_type = 'admin' if session.get('user_type') == 'admin' else 'student'
    if session.get('user_type'):
        log_audit(actor_type, str(actor_name), f'{actor_type}_logout')
    if actor_type == 'student':
        _end_login_session(session.get('login_token'))
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('home'))


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    student_count = query_db('SELECT COUNT(*) as count FROM students', one=True)['count']
    session_count = query_db('SELECT COUNT(*) as count FROM sessions', one=True)['count']
    attendance_count = query_db('SELECT COUNT(*) as count FROM attendance', one=True)['count']

    # Get detailed attendance records
    attendance_records = query_db('''
        SELECT a.id, a.status, a.timestamp, a.note,
               s.name as student_name, s.roll_no,
               ses.title as session_title, ses.date as session_date, ses.time as session_time,
               sub.name as subject_name, sub.code as subject_code
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.id
        LEFT JOIN sessions ses ON a.session_id = ses.id
        LEFT JOIN subjects sub ON ses.subject_id = sub.id
        ORDER BY a.timestamp DESC
        LIMIT 20
    ''')

    low_alerts = []
    threshold = cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT
    students = query_db('SELECT * FROM students')
    for student in students:
        total = query_db('SELECT COUNT(*) as count FROM attendance WHERE student_id=?', (student['id'],), one=True)['count']
        present = query_db('SELECT COUNT(*) as count FROM attendance WHERE student_id=? AND status=?', (student['id'], 'Present'), one=True)['count']
        percentage = (present / total * 100) if total > 0 else 0
        if percentage < threshold:
            low_alerts.append({'name': student['name'], 'roll_no': student['roll_no'], 'percentage': round(percentage, 1)})

    active_session_count = query_db('SELECT COUNT(*) as count FROM sessions WHERE active=1', one=True)['count']

    # Enrollment Reminders: students whose face enrollment is
    # incomplete or poor quality (see _enrollment_status() /
    # ENROLLMENT_MIN_PHOTOS / ENROLLMENT_QUALITY_REENROLL_THRESHOLD) —
    # the "automatic re-enrollment reminder" feature. There's no email/
    # SMS in this project (a documented trade-off), so the reminder
    # surfaces here for an admin, and separately on the student's own
    # profile page (see student_profile()).
    enrollment_reminders = []
    for student in students:
        status = _enrollment_status(student['id'])
        if status['status'] in ('pending', 'not_enrolled'):
            enrollment_reminders.append({
                'student_id': student['id'], 'name': student['name'], 'roll_no': student['roll_no'],
                'status': status['status'], 'photo_count': status['photo_count'], 'avg_quality': status['avg_quality'],
            })
    enrollment_reminders.sort(key=lambda r: (r['status'] != 'not_enrolled', r['avg_quality'] if r['avg_quality'] is not None else 0))
    enrollment_reminders = enrollment_reminders[:10]

    # Security Alerts: surfaces the audit_log's spoof/liveness/lockout
    # events (previously visible only by manually scanning the full
    # audit log) grouped per student, newest-active-first, over the last
    # SECURITY_ALERT_WINDOW_DAYS days. actor_name for these events is the
    # student's numeric id (see student_mark_attendance()'s log_audit
    # calls) — joined back to students here to show a name/roll number.
    security_window_start = (datetime.now() - timedelta(days=cfg.SECURITY_ALERT_WINDOW_DAYS)).isoformat()
    security_alerts = query_db('''
        SELECT s.name as student_name, s.roll_no as roll_no, al.actor_name as student_id,
               COUNT(*) as event_count, MAX(al.timestamp) as last_seen
        FROM audit_log al
        LEFT JOIN students s ON s.id = CAST(al.actor_name AS INTEGER)
        WHERE al.actor_type = 'student'
          AND al.action IN ('attendance_spoof_suspected', 'attendance_liveness_challenge_failed', 'attendance_security_lockout')
          AND al.timestamp >= ?
        GROUP BY al.actor_name
        ORDER BY event_count DESC, last_seen DESC
        LIMIT 10
    ''', (security_window_start,))

    return render_template('admin_dashboard.html',
                         students=student_count,
                         sessions=session_count,
                         active_sessions=active_session_count,
                         attendance=attendance_count,
                         attendance_records=attendance_records,
                         low_alerts=low_alerts,
                         threshold=threshold,
                         security_alerts=security_alerts,
                         security_window_days=cfg.SECURITY_ALERT_WINDOW_DAYS,
                         enrollment_reminders=enrollment_reminders,
                         enrollment_min_photos=cfg.ENROLLMENT_MIN_PHOTOS)


@app.route('/admin/students')
def admin_students():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    search = (request.args.get('q') or '').strip()
    if search:
        total_count = query_db(
            'SELECT COUNT(*) as count FROM students WHERE name LIKE ? OR roll_no LIKE ?',
            (f'%{search}%', f'%{search}%'), one=True
        )['count']
    else:
        total_count = query_db('SELECT COUNT(*) as count FROM students', one=True)['count']

    pagination = get_pagination(total_count, cfg.STUDENTS_PER_PAGE)

    if search:
        students = query_db(
            'SELECT * FROM students WHERE name LIKE ? OR roll_no LIKE ? ORDER BY name LIMIT ? OFFSET ?',
            (f'%{search}%', f'%{search}%', pagination['per_page'], pagination['offset'])
        )
    else:
        students = query_db(
            'SELECT * FROM students ORDER BY name LIMIT ? OFFSET ?',
            (pagination['per_page'], pagination['offset'])
        )
    # Which of the students on this page currently have their attendance-
    # marking locked (see _is_attendance_locked_out) — used by the
    # template to show a "Locked" badge and a "Clear Lock" action instead
    # of forcing an admin to dig through the audit log to find out.
    locked_student_ids = {s['id'] for s in students if _is_attendance_locked_out(s)}
    enrollment_by_student = {s['id']: _enrollment_status(s['id']) for s in students}
    return render_template('admin_students.html', students=students, pagination=pagination, search=search,
                            locked_student_ids=locked_student_ids, enrollment_by_student=enrollment_by_student)


@app.route('/admin/students/bulk-import', methods=['GET', 'POST'])
def bulk_import_students():
    """CSV bulk-import for onboarding many students at once. Creates
    accounts (name, roll_no, branch, semester, password) with NO face
    data — bulk face capture isn't practical via CSV. Each imported
    student completes their own registration afterward by logging in and
    visiting /student/register-face (linked from their profile page).

    Expected columns: name, roll_no, branch, semester, and optionally
    password (auto-generated and shown in the results if omitted)."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    if request.method == 'GET':
        return render_template('admin_bulk_import.html', results=None)

    upload = request.files.get('csv_file')
    if not upload or not upload.filename:
        flash('Please choose a CSV file to upload.', 'error')
        return redirect(url_for('bulk_import_students'))

    try:
        raw = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        flash('Could not read the file — please upload a UTF-8 encoded CSV.', 'error')
        return redirect(url_for('bulk_import_students'))

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or 'roll_no' not in reader.fieldnames:
        flash('CSV must include at least "name", "roll_no", "branch", and "semester" columns.', 'error')
        return redirect(url_for('bulk_import_students'))

    rows = list(reader)
    if len(rows) > cfg.MAX_BULK_IMPORT_ROWS:
        flash(f'Too many rows ({len(rows)}). Maximum is {cfg.MAX_BULK_IMPORT_ROWS} per import.', 'error')
        return redirect(url_for('bulk_import_students'))

    existing_roll_numbers = {r['roll_no'] for r in query_db('SELECT roll_no FROM students')}
    seen_in_file = set()
    created = []
    skipped = []

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        name = (row.get('name') or '').strip()
        roll_no = (row.get('roll_no') or '').strip()
        branch = (row.get('branch') or '').strip()
        semester = (row.get('semester') or '').strip()
        password = (row.get('password') or '').strip()

        if not name or not roll_no or not branch or not semester:
            skipped.append({'row': i, 'roll_no': roll_no or '(blank)', 'reason': 'missing required field(s)'})
            continue
        if roll_no in existing_roll_numbers:
            skipped.append({'row': i, 'roll_no': roll_no, 'reason': 'roll number already registered'})
            continue
        if roll_no in seen_in_file:
            skipped.append({'row': i, 'roll_no': roll_no, 'reason': 'duplicate roll number within this file'})
            continue

        generated_password = False
        if not password:
            password = secrets.token_urlsafe(9)
            generated_password = True
        else:
            password_error = validate_password_strength(password)
            if password_error:
                skipped.append({'row': i, 'roll_no': roll_no, 'reason': f'password: {password_error}'})
                continue

        student_id = execute_db(
            'INSERT INTO students(name, roll_no, branch, semester, password) VALUES(?, ?, ?, ?, ?)',
            (name, roll_no, branch, semester, generate_password_hash(password))
        )
        seen_in_file.add(roll_no)
        created.append({
            'student_id': student_id, 'name': name, 'roll_no': roll_no,
            'password': password if generated_password else None,
        })

    if created:
        log_audit('admin', session.get('admin_user'), 'bulk_student_import',
                   details=f'{len(created)} created, {len(skipped)} skipped')

    return render_template('admin_bulk_import.html', results={'created': created, 'skipped': skipped})


@app.route('/admin/students/bulk-import/template.csv')
def bulk_import_template():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['name', 'roll_no', 'branch', 'semester', 'password'])
    writer.writerow(['Jane Doe', '21CS001', 'CSE', '5', ''])
    writer.writerow(['John Smith', '21CS002', 'CSE', '5', 'OptionalPass123'])
    response = Response(buf.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=student_import_template.csv'
    return response


@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    """Admin account management: list existing admins, add a new one, or
    delete one (never the last remaining account — that would lock
    everyone out with no self-service way back in, given there's no
    email-based recovery for admin accounts; see README for the CLI-based
    recovery path)."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        new_username = (request.form.get('new_username') or '').strip()
        new_password = request.form.get('new_password')

        if not new_username or not new_password:
            flash('Username and password are required.', 'error')
        else:
            password_error = validate_password_strength(new_password)
            if password_error:
                flash(password_error, 'error')
            elif query_db('SELECT 1 FROM admins WHERE username=?', (new_username,), one=True):
                flash('That username is already taken.', 'error')
            else:
                execute_db('INSERT INTO admins(username, password) VALUES (?, ?)',
                           (new_username, generate_password_hash(new_password)))
                log_audit('admin', session.get('admin_user'), 'admin_account_created', target=new_username)
                flash(f'Admin account "{new_username}" created.', 'success')
        return redirect(url_for('admin_settings'))

    admins = query_db('SELECT id, username FROM admins ORDER BY username')
    return render_template('admin_settings.html', admins=admins)


@app.route('/admin/settings/admins/<int:admin_id>/delete', methods=['POST'])
def delete_admin(admin_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    total_admins = query_db('SELECT COUNT(*) as c FROM admins', one=True)['c']
    if total_admins <= 1:
        flash('Cannot delete the last remaining admin account.', 'error')
        return redirect(url_for('admin_settings'))

    target_admin = query_db('SELECT username FROM admins WHERE id=?', (admin_id,), one=True)
    if not target_admin:
        flash('Admin account not found.', 'error')
        return redirect(url_for('admin_settings'))

    if target_admin['username'] == session.get('admin_user'):
        flash('You cannot delete the account you are currently logged in as.', 'error')
        return redirect(url_for('admin_settings'))

    execute_db('DELETE FROM admins WHERE id=?', (admin_id,))
    log_audit('admin', session.get('admin_user'), 'admin_account_deleted', target=target_admin['username'])
    flash(f'Admin account "{target_admin["username"]}" deleted.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/admin/recognition-settings', methods=['GET', 'POST'])
def recognition_settings():
    """Recognition model/configuration management + threshold
    configuration from the admin panel: view every effective face-
    recognition setting (env/config.py defaults, with any saved runtime
    override applied) and edit the five thresholds in
    RUNTIME_CONFIGURABLE_THRESHOLDS without touching the environment or
    restarting the app. Everything else here (feature toggles, the
    embedding model path, detection tuning) is shown read-only — editing
    those safely at runtime would touch a lot more code paths than a
    numeric threshold comparison does."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        errors = []
        for key in RUNTIME_CONFIGURABLE_THRESHOLDS:
            raw = (request.form.get(key) or '').strip()
            if not raw:
                continue
            try:
                float(raw)
            except ValueError:
                errors.append(f'{key}: "{raw}" is not a number.')
                continue
            set_effective_setting(key, raw, session.get('admin_user'))
        if errors:
            flash(' '.join(errors), 'error')
        else:
            log_audit('admin', session.get('admin_user'), 'recognition_settings_updated',
                      details=', '.join(f'{k}={request.form.get(k)}' for k in RUNTIME_CONFIGURABLE_THRESHOLDS if request.form.get(k)))
            flash('Recognition settings updated.', 'success')
        return redirect(url_for('recognition_settings'))

    effective = {key: get_effective_setting(key) for key in RUNTIME_CONFIGURABLE_THRESHOLDS}
    overridden = {r['key'] for r in query_db('SELECT key FROM app_settings')}
    override_rows = query_db('SELECT key, value, updated_at, updated_by FROM app_settings ORDER BY key')

    model_version_counts = query_db('''
        SELECT COALESCE(model_version, 'unknown') as model_version, COUNT(*) as count
        FROM face_embeddings GROUP BY model_version ORDER BY count DESC
    ''')

    return render_template('admin_recognition_settings.html',
                            effective=effective, defaults={k: getattr(cfg, k) for k in RUNTIME_CONFIGURABLE_THRESHOLDS},
                            overridden=overridden, override_rows=override_rows,
                            current_model_version=cfg.EMBEDDER_MODEL_VERSION, model_version_counts=model_version_counts,
                            feature_toggles={
                                'ACTIVE_LIVENESS_ENABLED': cfg.ACTIVE_LIVENESS_ENABLED,
                                'ANTI_SPOOF_ENABLED': cfg.ANTI_SPOOF_ENABLED,
                                'LIVENESS_CHECK_ENABLED': cfg.LIVENESS_CHECK_ENABLED,
                                'LIVENESS_REQUIRE_BLINK': cfg.LIVENESS_REQUIRE_BLINK,
                                'REJECT_MULTIPLE_FACES': cfg.REJECT_MULTIPLE_FACES,
                                'IMAGE_QUALITY_CHECK_ENABLED': cfg.IMAGE_QUALITY_CHECK_ENABLED,
                            },
                            model_info={
                                'EMBEDDER_MODEL_PATH': cfg.EMBEDDER_MODEL_PATH,
                                'EMBEDDING_INPUT_SIZE': cfg.EMBEDDING_INPUT_SIZE,
                                'MATCH_TOP_K': cfg.MATCH_TOP_K,
                            })


@app.route('/admin/recognition-settings/<key>/reset', methods=['POST'])
def reset_recognition_setting(key):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    if key not in RUNTIME_CONFIGURABLE_THRESHOLDS:
        flash('Unknown setting.', 'error')
        return redirect(url_for('recognition_settings'))
    row = query_db('SELECT key FROM app_settings WHERE key=?', (key,), one=True)
    if row:
        conn = get_db_connection()
        conn.execute('DELETE FROM app_settings WHERE key=?', (key,))
        conn.commit()
        conn.close()
        log_audit('admin', session.get('admin_user'), 'recognition_settings_reset', target=key)
        flash(f'{key} reset to its default ({getattr(cfg, key)}).', 'success')
    return redirect(url_for('recognition_settings'))


@app.route('/admin/security-settings/<key>/reset', methods=['POST'])
def reset_security_setting(key):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    if key not in SECURITY_SETTINGS_KEYS:
        flash('Unknown setting.', 'error')
        return redirect(url_for('security_settings'))
    row = query_db('SELECT key FROM app_settings WHERE key=?', (key,), one=True)
    if row:
        conn = get_db_connection()
        conn.execute('DELETE FROM app_settings WHERE key=?', (key,))
        conn.commit()
        conn.close()
        log_audit('admin', session.get('admin_user'), 'security_settings_reset', target=key)
        flash(f'{key} reset to its default ({getattr(cfg, key)}).', 'success')
    return redirect(url_for('security_settings'))


@app.route('/admin/settings/change-password', methods=['POST'])
def admin_change_own_password():
    """Lets the logged-in admin change their own password, given their
    current one — the self-service counterpart to reset_admin_password.py
    (which exists for when an admin is locked out entirely)."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    admin = query_db('SELECT * FROM admins WHERE username=?', (session['admin_user'],), one=True)
    if not admin or not check_password_hash(admin['password'], current_password):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('admin_settings'))

    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('admin_settings'))

    password_error = validate_password_strength(new_password)
    if password_error:
        flash(password_error, 'error')
        return redirect(url_for('admin_settings'))

    execute_db('UPDATE admins SET password=? WHERE id=?', (generate_password_hash(new_password), admin['id']))
    log_audit('admin', session['admin_user'], 'admin_changed_own_password')
    flash('Password updated.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/admin/attendance')
def admin_attendance_records():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    total_count = query_db('SELECT COUNT(*) as count FROM attendance', one=True)['count']
    pagination = get_pagination(total_count, cfg.ATTENDANCE_RECORDS_PER_PAGE)
    records = query_db('''
        SELECT a.id, a.status, a.timestamp, a.note, a.confidence,
               s.name as student_name, s.roll_no,
               ses.title as session_title, ses.date as session_date, ses.time as session_time,
               sub.name as subject_name, sub.code as subject_code
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.id
        LEFT JOIN sessions ses ON a.session_id = ses.id
        LEFT JOIN subjects sub ON ses.subject_id = sub.id
        ORDER BY a.timestamp DESC
        LIMIT ? OFFSET ?
    ''', (pagination['per_page'], pagination['offset']))
    return render_template('admin_attendance.html', records=records, pagination=pagination)


@app.route('/admin/audit-log')
def audit_log_view():
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    # 'security' narrows the log to SECURITY_AUDIT_ACTIONS (logins,
    # lockouts, spoof/liveness failures) — added so an admin can jump
    # straight to security-relevant events instead of having to scan the
    # full log by hand. 'actor' additionally narrows to one actor_name
    # (e.g. a student id), used by the dashboard's Security Alerts widget
    # to link straight to one student's events.
    filter_type = request.args.get('filter', 'all')
    actor = (request.args.get('actor') or '').strip()

    where_clauses = []
    params: list = []
    if filter_type == 'security':
        placeholders = ', '.join('?' for _ in SECURITY_AUDIT_ACTIONS)
        where_clauses.append(f'action IN ({placeholders})')
        params.extend(SECURITY_AUDIT_ACTIONS)
    if actor:
        where_clauses.append('actor_name = ?')
        params.append(actor)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
    # where_sql above is built only from fixed clause strings ('action IN (?,?)',
    # 'actor_name = ?'); values are always bound via params, never interpolated.

    total_count = query_db(f'SELECT COUNT(*) as count FROM audit_log {where_sql}', tuple(params), one=True)['count']  # nosec B608
    pagination = get_pagination(total_count, cfg.AUDIT_LOG_PER_PAGE)
    entries = query_db(
        f'SELECT * FROM audit_log {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?',  # nosec B608
        tuple(params) + (pagination['per_page'], pagination['offset'])
    )

    extra_args = {}
    if filter_type != 'all':
        extra_args['filter'] = filter_type
    if actor:
        extra_args['actor'] = actor

    return render_template('audit_log.html', entries=entries, pagination=pagination,
                            filter_type=filter_type, actor=actor, extra_args=extra_args)


@app.route('/admin/student/<int:student_id>/delete', methods=['POST'])
def delete_student(student_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401

    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)

    # 1. Delete attendance records
    execute_db('DELETE FROM attendance WHERE student_id=?', (student_id,))

    # 2. Delete face profile from FaceBase.db
    face_conn = sqlite3.connect(FACE_DATABASE_PATH)
    face_cursor = face_conn.cursor()
    face_cursor.execute('DELETE FROM people WHERE id=?', (student_id,))
    face_conn.commit()
    face_conn.close()

    # 3. Delete face images from Datasets directory
    _delete_student_face_files(student_id)

    # 4. Delete student from app.db
    execute_db('DELETE FROM students WHERE id=?', (student_id,))

    # 5. Delete this student's face embeddings. No retraining step is
    # needed — unlike the old LBPH approach, which had to reprocess the
    # entire dataset from scratch on every add/delete, embeddings are an
    # incremental gallery: removing a student is just deleting their rows.
    delete_student_embeddings(student_id)

    target = f'{student["name"]} ({student["roll_no"]})' if student else f'student #{student_id}'
    log_audit('admin', session.get('admin_user'), 'student_deleted', target=target)

    flash('Student and all related records deleted successfully', 'success')
    return redirect(url_for('admin_students'))


@app.route('/admin/student/<int:student_id>/reset-password', methods=['POST'])
def reset_student_password(student_id):
    """Admin-initiated password reset for a student. There's no email/SMTP
    infrastructure in this project (see README), so a fully self-service
    "forgot password" isn't available — this is the practical alternative:
    the student contacts an admin, who generates a temporary password here
    and communicates it to the student out-of-band. The student can then
    set their own password from /student/profile."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401

    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    temp_password = secrets.token_urlsafe(9)  # short, readable-enough random password
    execute_db('UPDATE students SET password=?, failed_attempts=0, locked_until=NULL WHERE id=?',
               (generate_password_hash(temp_password), student_id))

    log_audit('admin', session.get('admin_user'), 'student_password_reset',
              target=f'{student["name"]} ({student["roll_no"]})')

    flash(f'Password for {student["name"]} ({student["roll_no"]}) has been reset to: {temp_password} — share this with the student securely; it will not be shown again.', 'success')
    return redirect(url_for('admin_students'))


@app.route('/admin/student/<int:student_id>/clear-attendance-lock', methods=['POST'])
def clear_attendance_lock(student_id):
    """Admin override for the attendance-marking lockout (see
    _register_attendance_security_failure / ATTENDANCE_LOCKOUT_* in
    config.py). A student can trip this from genuinely bad lighting or a
    flaky camera, not just an actual proxy attempt, so an admin needs a
    way to lift it without doing a full password reset."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401

    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    _clear_attendance_security_failures(student_id)
    log_audit('admin', session.get('admin_user'), 'attendance_lockout_cleared',
              target=f'{student["name"]} ({student["roll_no"]})')

    flash(f'Attendance-marking lock cleared for {student["name"]} ({student["roll_no"]}).', 'success')
    return redirect(request.referrer or url_for('admin_students'))


@app.route('/admin/student/<int:student_id>/clear-escalation', methods=['POST'])
def clear_security_escalation(student_id):
    """Clears automatic risk-based escalation (see _maybe_escalate_student())
    — a distinct, more serious flag than the attendance-abuse lockout
    above, so it gets its own explicit admin action rather than being
    silently lifted by clear_attendance_lock(). Does NOT retroactively
    remove the audit events that caused the escalation; re-escalation can
    happen again later if new events push the score back over threshold."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    execute_db('UPDATE students SET security_escalated=0, security_escalated_at=NULL WHERE id=?', (student_id,))
    log_audit('admin', session.get('admin_user'), 'security_escalation_cleared', target=f'{student["name"]} ({student["roll_no"]})')

    flash(f'Security escalation cleared for {student["name"]} ({student["roll_no"]}).', 'success')
    return redirect(request.referrer or url_for('admin_students'))


@app.route('/admin/student/<int:student_id>/security-history')
def student_security_history(student_id):
    """IP/device history per student: every device fingerprint seen for
    them, their recent security-relevant audit events, and their current
    risk score/breakdown — one place to review before deciding whether
    to escalate/clear/lock an account by hand."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    devices = query_db('SELECT * FROM device_fingerprints WHERE student_id=? ORDER BY last_seen DESC', (student_id,))
    login_sessions = query_db('SELECT * FROM student_login_sessions WHERE student_id=? ORDER BY created_at DESC LIMIT 20', (student_id,))
    # Only '?' placeholders are interpolated below; values are always bound separately.
    security_events = query_db(
        "SELECT * FROM audit_log WHERE actor_type='student' AND actor_name=? AND action IN ({}) ORDER BY timestamp DESC LIMIT 50".format(  # nosec B608
            ','.join('?' for _ in SECURITY_AUDIT_ACTIONS)),
        (str(student_id), *SECURITY_AUDIT_ACTIONS)
    )
    score, breakdown = _compute_risk_score(student_id)

    return render_template('admin_student_security_history.html', student=student, devices=devices,
                            login_sessions=login_sessions, security_events=security_events,
                            risk_score=score, risk_breakdown=breakdown, event_severity=event_severity,
                            escalation_threshold=get_effective_setting('SECURITY_RISK_ESCALATION_THRESHOLD'))


@app.route('/admin/security-notifications/<int:notification_id>/read', methods=['POST'])
def mark_security_notification_read(notification_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    execute_db('UPDATE security_notifications SET is_read=1 WHERE id=?', (notification_id,))
    return redirect(request.referrer or url_for('security_dashboard'))


@app.route('/admin/security-dashboard')
def security_dashboard():
    """Security dashboard with trends: daily security-event counts by
    severity over SECURITY_ALERT_WINDOW_DAYS, a risk-score leaderboard,
    and the notification feed (see _create_security_notification()) —
    the consolidated view the other, narrower widgets (dashboard's
    Security Alerts, the audit log's Security filter) feed into."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    window_start_date = (datetime.now() - timedelta(days=cfg.SECURITY_ALERT_WINDOW_DAYS))
    window_start = window_start_date.isoformat()
    # `placeholders` below is only '?' chars joined by ',', values bound separately.
    placeholders = ','.join('?' for _ in SECURITY_AUDIT_ACTIONS)
    events = query_db(
        f'SELECT timestamp, action FROM audit_log WHERE action IN ({placeholders}) AND timestamp >= ? ORDER BY timestamp',  # nosec B608
        (*SECURITY_AUDIT_ACTIONS, window_start)
    )

    # Daily trend, bucketed by severity -- a CSS-only bar chart like
    # /admin/reports', stacked isn't worth the complexity here, so this
    # renders one bar series per severity, highest severity most visible.
    buckets: dict = {}
    for e in events:
        day = e['timestamp'][:10]
        severity = event_severity(e['action'])
        buckets.setdefault(day, {'low': 0, 'medium': 0, 'high': 0, 'critical': 0})
        buckets[day][severity] += 1
    trend = [{'label': day, **counts} for day, counts in sorted(buckets.items())]

    severity_totals = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
    for e in events:
        severity_totals[event_severity(e['action'])] += 1

    students = query_db('SELECT id, name, roll_no, security_escalated FROM students')
    risk_rows = []
    for s in students:
        score, _breakdown = _compute_risk_score(s['id'])
        if score > 0:
            risk_rows.append({'student_id': s['id'], 'name': s['name'], 'roll_no': s['roll_no'],
                               'score': score, 'escalated': bool(s['security_escalated'])})
    risk_rows.sort(key=lambda r: r['score'], reverse=True)
    risk_rows = risk_rows[:10]

    notifications = query_db('''
        SELECT n.*, s.name as student_name, s.roll_no
        FROM security_notifications n
        LEFT JOIN students s ON n.student_id = s.id
        ORDER BY n.is_read ASC, n.created_at DESC
        LIMIT 20
    ''')
    unread_count = query_db('SELECT COUNT(*) as c FROM security_notifications WHERE is_read=0', one=True)['c']

    return render_template('admin_security_dashboard.html', trend=trend, severity_totals=severity_totals,
                            risk_rows=risk_rows, notifications=notifications, unread_count=unread_count,
                            window_days=cfg.SECURITY_ALERT_WINDOW_DAYS,
                            escalation_threshold=get_effective_setting('SECURITY_RISK_ESCALATION_THRESHOLD'))


@app.route('/admin/security-settings', methods=['GET', 'POST'])
def security_settings():
    """Configurable security policies from the admin panel: the same
    runtime-override mechanism as /admin/recognition-settings
    (RUNTIME_CONFIGURABLE_SETTINGS / app_settings), scoped to
    SECURITY_SETTINGS_KEYS instead."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        errors = []
        for key in SECURITY_SETTINGS_KEYS:
            raw = (request.form.get(key) or '').strip()
            if not raw:
                continue
            try:
                int(raw)
            except ValueError:
                errors.append(f'{key}: "{raw}" is not a whole number.')
                continue
            set_effective_setting(key, raw, session.get('admin_user'))
        if errors:
            flash(' '.join(errors), 'error')
        else:
            log_audit('admin', session.get('admin_user'), 'security_settings_updated',
                      details=', '.join(f'{k}={request.form.get(k)}' for k in SECURITY_SETTINGS_KEYS if request.form.get(k)))
            flash('Security settings updated.', 'success')
        return redirect(url_for('security_settings'))

    effective = {key: get_effective_setting(key) for key in SECURITY_SETTINGS_KEYS}
    overridden = {r['key'] for r in query_db('SELECT key FROM app_settings')}
    return render_template('admin_security_settings.html', effective=effective,
                            defaults={k: getattr(cfg, k) for k in SECURITY_SETTINGS_KEYS}, overridden=overridden)


@app.route('/admin/student/<int:student_id>/face-photos')
def admin_student_face_photos(student_id):
    """Admin view of one student's registered face photos, enrollment
    status, and which embedding model produced each — see
    _enrollment_status() and EMBEDDER_MODEL_VERSION in config.py."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    photos = query_db(
        'SELECT id, image_filename, quality_score, model_version, created_at FROM face_embeddings WHERE student_id=? ORDER BY created_at',
        (student_id,)
    )
    return render_template('admin_student_face_photos.html', student=student, photos=photos,
                            enrollment=_enrollment_status(student_id), current_model_version=cfg.EMBEDDER_MODEL_VERSION)


@app.route('/admin/student/<int:student_id>/face-photos/<int:embedding_id>/delete', methods=['POST'])
def admin_delete_face_photo(student_id, embedding_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    if _delete_single_embedding(student_id, embedding_id):
        target = f'{student["name"]} ({student["roll_no"]})' if student else f'student #{student_id}'
        log_audit('admin', session.get('admin_user'), 'face_photo_removed', target=target, details=f'embedding #{embedding_id}')
        flash('Photo removed.', 'success')
    else:
        flash('Photo not found.', 'error')
    return redirect(url_for('admin_student_face_photos', student_id=student_id))


@app.route('/admin/student/<int:student_id>/reset-face-data', methods=['POST'])
def admin_reset_face_data(student_id):
    """Admin-initiated full face-data reset — wipes every registered
    photo/embedding for a student (their account, password, and
    attendance history are untouched), leaving them 'not enrolled' and
    needing to go through registration/register-face again. Distinct
    from deleting the student entirely."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    student = query_db('SELECT name, roll_no FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))

    _reset_student_face_data(student_id)
    log_audit('admin', session.get('admin_user'), 'face_data_reset', target=f'{student["name"]} ({student["roll_no"]})')

    flash(f'Face data reset for {student["name"]} ({student["roll_no"]}) — they will need to re-enroll.', 'success')
    return redirect(url_for('admin_students'))


@app.route('/admin/sessions', methods=['GET', 'POST'])
def admin_sessions():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title')
        subject_name = request.form.get('subject_name')
        subject_code = request.form.get('subject_code')
        date = request.form.get('date')

        start_h = int(request.form.get('start_h', 12))
        start_m = int(request.form.get('start_m', 0))
        start_p = request.form.get('start_p', 'AM')

        end_h = int(request.form.get('end_h', 12))
        end_m = int(request.form.get('end_m', 0))
        end_p = request.form.get('end_p', 'PM')

        def to_24h(h, m, p):
            if p == 'PM' and h < 12:
                h += 12
            if p == 'AM' and h == 12:
                h = 0
            return f"{h:02d}:{m:02d}"

        time = to_24h(start_h, start_m, start_p)
        end_time = to_24h(end_h, end_m, end_p)

        end_date = request.form.get('end_date')
        is_recurring = 1 if request.form.get('is_recurring') else 0

        # Optional per-session overrides -- all NULL (open/global-default)
        # unless explicitly set here. See admin_sessions.html's "Advanced"
        # fields and the 0006 migration's docstring for what each means.
        def _optional_int(field):
            raw = (request.form.get(field) or '').strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        attendance_window_minutes = _optional_int('attendance_window_minutes')
        grace_period_minutes = _optional_int('grace_period_minutes')
        allowed_networks = (request.form.get('allowed_networks') or '').strip() or None
        restrict_branch = (request.form.get('restrict_branch') or '').strip() or None
        restrict_semester = (request.form.get('restrict_semester') or '').strip() or None

        # Convert yyyy-mm-dd to dd-mm-yyyy for internal consistency
        def convert_date(d):
            if not d:
                return d
            try:
                return datetime.strptime(d, '%Y-%m-%d').strftime('%d-%m-%Y')
            except ValueError:
                return d

        date = convert_date(date)
        end_date = convert_date(end_date)

        if title and subject_name and subject_code and date and time and end_date and end_time:
            conflict = None
            if not cfg.ALLOW_OVERLAPPING_SESSIONS:
                conflict = find_conflicting_session(date, end_date, time, end_time)
            if conflict:
                flash(
                    f'This overlaps with an existing session: "{conflict["title"]}" '
                    f'({conflict["date"]} {conflict["time"]}-{conflict["end_time"]}). '
                    'A student can\'t be marked present in two places at once — adjust the time or delete the conflicting session first.',
                    'error'
                )
            else:
                subject = query_db('SELECT * FROM subjects WHERE code=?', (subject_code,), one=True)
                if not subject:
                    subject_id = execute_db('INSERT INTO subjects(name, code) VALUES(?, ?)', (subject_name, subject_code))
                else:
                    subject_id = subject['id']
                execute_db('''INSERT INTO sessions(subject_id, title, date, time, end_date, end_time, is_recurring, active, status,
                              attendance_window_minutes, grace_period_minutes, allowed_networks, restrict_branch, restrict_semester)
                              VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?, ?)''',
                           (subject_id, title, date, time, end_date, end_time, is_recurring, 0,
                            attendance_window_minutes, grace_period_minutes, allowed_networks, restrict_branch, restrict_semester))
                flash('Session scheduled successfully', 'success')
        else:
            flash('Please fill all session fields', 'error')
    _auto_close_expired_sessions()
    sessions_raw = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id')
    sessions = []
    for s in sessions_raw:
        sd = dict(s)
        try:
            sd['time_12h'] = datetime.strptime(s['time'], '%H:%M').strftime('%I:%M %p')
            sd['end_time_12h'] = datetime.strptime(s['end_time'], '%H:%M').strftime('%I:%M %p')
        except (ValueError, TypeError):
            sd['time_12h'] = s['time']
            sd['end_time_12h'] = s['end_time']
        sessions.append(sd)
    branches = [r['branch'] for r in query_db('SELECT DISTINCT branch FROM students WHERE branch IS NOT NULL ORDER BY branch')]
    semesters = [r['semester'] for r in query_db('SELECT DISTINCT semester FROM students WHERE semester IS NOT NULL ORDER BY semester')]
    return render_template('admin_sessions.html', sessions=sessions, branches=branches, semesters=semesters)


@app.route('/admin/session/<int:session_id>/delete', methods=['POST'])
def delete_session(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401

    # Delete attendance records for this session
    execute_db('DELETE FROM attendance WHERE session_id=?', (session_id,))

    # Delete the session
    execute_db('DELETE FROM sessions WHERE id=?', (session_id,))

    log_audit('admin', session.get('admin_user'), 'session_deleted', target=f'session #{session_id}')
    flash('Session and related attendance records deleted successfully', 'success')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/start', methods=['POST'])
def start_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    execute_db("UPDATE sessions SET active=1, status='active' WHERE id=?", (session_id,))
    log_audit('admin', session.get('admin_user'), 'session_started', target=f'session #{session_id}')
    return redirect(url_for('attendance_session', session_id=session_id))


@app.route('/admin/session/<int:session_id>/stop', methods=['POST'])
def stop_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    execute_db("UPDATE sessions SET active=0, status='completed' WHERE id=?", (session_id,))
    log_audit('admin', session.get('admin_user'), 'session_stopped', target=f'session #{session_id}')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/cancel', methods=['POST'])
def cancel_session(session_id):
    """Session cancellation: distinct from stopping a session that ran
    (status='completed') — a cancelled session never took place at all,
    is blocked from attendance marking with its own explicit message
    (see student_mark_attendance()), and is excluded from overlap
    detection for future scheduling (see find_conflicting_session())."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_row = query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True)
    if not session_row:
        flash('Session not found.', 'error')
        return redirect(url_for('admin_sessions'))
    reason = (request.form.get('reason') or '').strip()[:cfg.CORRECTION_REASON_MAX_LENGTH]
    execute_db("UPDATE sessions SET active=0, status='cancelled', cancelled_at=?, cancellation_reason=? WHERE id=?",
               (datetime.now().isoformat(), reason or None, session_id))
    log_audit('admin', session.get('admin_user'), 'session_cancelled', target=f'session #{session_id}', details=reason or None)
    flash('Session cancelled.', 'success')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/reschedule', methods=['POST'])
def reschedule_session(session_id):
    """Rescheduling changes a session's date/time window — reusing the
    same overlap check as creating a new one — and, if the session had
    been cancelled, brings it back to 'scheduled'. A 'completed' session
    (attendance already happened) can't be rescheduled — create a new
    session instead; that's a deliberate rule to avoid rewriting a
    session's history after the fact, same spirit as
    ATTENDANCE_EDIT_LOCK_*."""
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_row = query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True)
    if not session_row:
        flash('Session not found.', 'error')
        return redirect(url_for('admin_sessions'))
    if session_row['status'] == 'completed':
        flash('A completed session cannot be rescheduled — create a new session instead.', 'error')
        return redirect(url_for('admin_sessions'))

    def convert_date(d):
        if not d:
            return d
        try:
            return datetime.strptime(d, '%Y-%m-%d').strftime('%d-%m-%Y')
        except ValueError:
            return d

    date = convert_date(request.form.get('date'))
    end_date = convert_date(request.form.get('end_date'))
    time = request.form.get('time')
    end_time = request.form.get('end_time')
    if not (date and end_date and time and end_time):
        flash('Please fill all date/time fields to reschedule.', 'error')
        return redirect(url_for('admin_sessions'))

    conflict = None
    if not cfg.ALLOW_OVERLAPPING_SESSIONS:
        conflict = find_conflicting_session(date, end_date, time, end_time, exclude_session_id=session_id)
    if conflict:
        flash(
            f'This overlaps with an existing session: "{conflict["title"]}" '
            f'({conflict["date"]} {conflict["time"]}-{conflict["end_time"]}).',
            'error'
        )
        return redirect(url_for('admin_sessions'))

    new_status = 'scheduled' if session_row['status'] == 'cancelled' else session_row['status']
    execute_db("UPDATE sessions SET date=?, end_date=?, time=?, end_time=?, status=?, active=0, cancelled_at=NULL, cancellation_reason=NULL WHERE id=?",
               (date, end_date, time, end_time, new_status, session_id))
    log_audit('admin', session.get('admin_user'), 'session_rescheduled', target=f'session #{session_id}',
              details=f'{session_row["date"]} {session_row["time"]} -> {date} {time}')
    flash('Session rescheduled.', 'success')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/attendance-data')
def session_attendance_data(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(_compute_session_attendance_payload(session_id))


def _compute_session_attendance_payload(session_id):
    """Shared by session_attendance_data() (the plain polling endpoint)
    and session_updates() (the long-polling one) so both always return
    the exact same shape from one place."""
    # Get all students with their attendance status for this session
    students_with_attendance = query_db('''
        SELECT s.id, s.name, s.roll_no, s.branch, s.semester,
               a.status, a.timestamp,
               CASE WHEN a.status IS NOT NULL THEN 1 ELSE 0 END as has_attendance
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
        ORDER BY s.roll_no
    ''', (session_id,))

    session_row = query_db('SELECT restrict_branch, restrict_semester FROM sessions WHERE id=?', (session_id,), one=True)
    in_scope = students_with_attendance
    if session_row:
        in_scope = [
            s for s in students_with_attendance
            if (not session_row['restrict_branch'] or s['branch'] == session_row['restrict_branch'])
            and (not session_row['restrict_semester'] or s['semester'] == session_row['restrict_semester'])
        ]

    # Calculate summary stats — Present/Absent/Late (see the
    # Present/Absent/Late status feature) plus failed self-service
    # attempts (spoof-suspected/liveness-challenge-failed) from the
    # audit log, scoped to students this session's group actually
    # applies to (restrict_branch/restrict_semester, if set).
    total = len(in_scope)
    present = len([s for s in in_scope if s['status'] == 'Present'])
    late = len([s for s in in_scope if s['status'] == 'Late'])
    absent = total - present - late
    failed_attempts = query_db(
        "SELECT COUNT(*) as count FROM audit_log WHERE target=? AND action IN ('attendance_spoof_suspected', 'attendance_liveness_challenge_failed')",
        (f'session #{session_id}',), one=True
    )['count']

    # Aggregate counts only — not a per-student dump (roll numbers/names)
    # of who was present/absent; that already lives in the database and
    # the audit log, which are access-controlled, unlike log output.
    logger.debug(f'Session {session_id} attendance summary: total={total} present={present} late={late} absent={absent} failed_attempts={failed_attempts}')

    return {
        'students': [dict(s) for s in students_with_attendance],
        'summary': {
            'total': total,
            'present': present,
            'late': late,
            'absent': absent,
            'failed_attempts': failed_attempts
        }
    }


# --- Real-time updates for the admin session-monitor page -----------------
# Long-polling rather than a persistent SSE/websocket stream, deliberately:
# this app runs under gunicorn's sync worker model (see gunicorn.conf.py),
# where a worker handles one request at a time — an indefinitely-open
# stream would tie up a worker for as long as an admin keeps that tab
# open. A bounded long-poll (see REALTIME_LONGPOLL_TIMEOUT_SECONDS) ties
# up a worker for at most that many seconds per cycle instead, and always
# recomputes fresh data on return regardless of *why* it woke up — so
# correctness never depends on the version-counter signal actually being
# seen (see the note on multi-worker delivery below). Still: don't open
# more concurrent session-monitor tabs than you have spare
# GUNICORN_WORKERS, or those requests will simply queue.
#
# _SESSION_UPDATE_VERSIONS is an in-memory per-worker-process counter, so
# under gunicorn's default multiple worker PROCESSES, a change handled by
# one worker doesn't wake a long-poll parked in another — that request
# just waits out its full timeout and then returns current data anyway
# (correct, just not instant). Setting GUNICORN_WORKERS=1 makes the
# signal fully reliable, at the cost of no request parallelism at all.
_SESSION_UPDATES_LOCK = threading.Lock()
_SESSION_UPDATE_VERSIONS: defaultdict = defaultdict(int)


def _publish_session_update(session_id):
    with _SESSION_UPDATES_LOCK:
        _SESSION_UPDATE_VERSIONS[session_id] += 1


@app.route('/admin/session/<int:session_id>/updates')
def session_updates(session_id):
    """Long-polling endpoint backing real-time updates on the admin
    session-monitor page — see the module comment above. Blocks (via a
    short sleep loop, not a database wait) until either the session's
    update version changes or REALTIME_LONGPOLL_TIMEOUT_SECONDS elapses,
    whichever comes first, then always returns the current attendance
    data either way."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        since_version = int(request.args.get('since', 0))
    except (TypeError, ValueError):
        since_version = 0

    with _SESSION_UPDATES_LOCK:
        current_version = _SESSION_UPDATE_VERSIONS[session_id]

    if cfg.REALTIME_UPDATES_ENABLED:
        deadline = time.monotonic() + cfg.REALTIME_LONGPOLL_TIMEOUT_SECONDS
        while current_version == since_version and time.monotonic() < deadline:
            time.sleep(0.5)
            with _SESSION_UPDATES_LOCK:
                current_version = _SESSION_UPDATE_VERSIONS[session_id]

    payload = _compute_session_attendance_payload(session_id)
    payload['version'] = current_version
    return jsonify(payload)


@app.route('/admin/session/<int:session_id>')
def attendance_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_info = _maybe_auto_close_session(query_db(
        'SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id WHERE s.id=?',
        (session_id,), one=True
    ))
    if not session_info:
        flash('Session not found.', 'error')
        return redirect(url_for('admin_sessions'))

    # Get all students with their attendance status for this session
    students_with_attendance = query_db('''
        SELECT s.id, s.name, s.roll_no, s.branch, s.semester,
               a.status, a.timestamp,
               CASE WHEN a.status IS NOT NULL THEN 1 ELSE 0 END as has_attendance
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
        ORDER BY s.roll_no
    ''', (session_id,))

    # Session attendance summary: present/late/absent among the students
    # this session actually applies to (its restrict_branch/
    # restrict_semester group, if any — see _session_group_restriction_error),
    # plus failed self-service attempts pulled from the audit log (see the
    # target=f'session #{id}' tagging added to those log_audit calls).
    in_scope = [
        s for s in students_with_attendance
        if (not session_info['restrict_branch'] or s['branch'] == session_info['restrict_branch'])
        and (not session_info['restrict_semester'] or s['semester'] == session_info['restrict_semester'])
    ]
    summary = {
        'total_in_scope': len(in_scope),
        'present': len([s for s in in_scope if s['status'] == 'Present']),
        'late': len([s for s in in_scope if s['status'] == 'Late']),
        'absent': len([s for s in in_scope if not s['status'] or s['status'] == 'Absent']),
    }
    summary['failed_attempts'] = query_db(
        "SELECT COUNT(*) as count FROM audit_log WHERE target=? AND action IN ('attendance_spoof_suspected', 'attendance_liveness_challenge_failed')",
        (f'session #{session_id}',), one=True
    )['count']

    return render_template('attendance_session.html', session_info=session_info, students=students_with_attendance, summary=summary)


@app.route('/admin/session/<int:session_id>/recognize', methods=['POST'])
def session_recognize(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    payload = request.get_json() or {}
    image_data = payload.get('image')
    if not image_data:
        return jsonify({'error': 'No image sent'}), 400

    # Optional motion-based liveness: the client (attendance_session.html)
    # samples the same live camera feed repeatedly and can send along the
    # previous sample as image_prev — if it does, require pixel-level
    # motion between the two (same check as the student self-service
    # flow's pairwise signal — see check_liveness()) before running
    # recognition at all. Backward compatible: a client that doesn't send
    # image_prev (or an older one) skips this check entirely, same as
    # before this was added. This is intentionally lighter-weight than
    # the student flow's full active-challenge liveness — this endpoint
    # is admin-supervised (a staff member is watching the room), which is
    # a meaningfully different threat model from an unsupervised
    # self-service kiosk.
    image_prev = payload.get('image_prev')
    if cfg.LIVENESS_CHECK_ENABLED and image_prev:
        gray_curr = decode_base64_to_gray(image_data)
        gray_prev = decode_base64_to_gray(image_prev)
        if gray_curr is not None and gray_prev is not None:
            face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
            if not check_liveness(face_cascade, gray_prev, gray_curr, cfg.RECOGNIZE_DETECT_PARAMS):
                return jsonify({
                    'status': 'error',
                    'message': 'Could not confirm a live camera feed (no motion detected between samples).'
                }), 200

    student_id, similarity, error = recognize_face(image_data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 200
    if student_id is None:
        # A face WAS detected (recognize_face only returns a similarity
        # value when detection succeeded) but didn't match anyone in the
        # gallery well enough -- an "unknown face" attempt at the kiosk,
        # as opposed to a detection failure. Logged for admin review
        # (see the Security Alerts widget / audit log) without recording
        # any attendance.
        if similarity is not None:
            log_audit('anonymous', 'unknown', 'unknown_face_detected', target=f'session #{session_id}',
                      details=f'best similarity {similarity:.2f}')
        return jsonify({'status': 'not_found'})
    _insert_attendance_if_absent(student_id, session_id, 'Present', datetime.now().isoformat(), confidence=similarity)
    profile = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    score_out_of_100 = max(0, min(similarity, 1.0)) * 100
    return jsonify({'status': 'present', 'student': {'id': profile['id'], 'name': profile['name'], 'roll_no': profile['roll_no']}, 'confidence': score_out_of_100})


@app.route('/admin/session/<int:session_id>/override', methods=['POST'])
def session_override(session_id):
    """Admin manual attendance editing: set a student's status for this
    session to Present/Absent/Late, or clear their record entirely back
    to "Not Marked" — always with a reason recorded in the attendance
    row's note and in the audit log. Blocked once the session is past
    ATTENDANCE_EDIT_LOCK_DAYS — see _is_session_edit_locked()."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    student_id = request.form.get('student_id')
    action = request.form.get('action')
    reason = (request.form.get('reason') or '').strip()[:cfg.CORRECTION_REASON_MAX_LENGTH]
    if not student_id or action not in ('present', 'absent', 'late', 'clear'):
        return redirect(url_for('attendance_session', session_id=session_id))

    session_row = query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True)
    if not session_row:
        flash('Session not found.', 'error')
        return redirect(url_for('admin_sessions'))
    if _is_session_edit_locked(session_row):
        flash(f'This session is more than {cfg.ATTENDANCE_EDIT_LOCK_DAYS} days old — its attendance record is locked and can no longer be edited.', 'error')
        return redirect(url_for('attendance_session', session_id=session_id))

    execute_db('DELETE FROM attendance WHERE session_id=? AND student_id=?', (session_id, student_id))
    if action == 'clear':
        log_audit('admin', session.get('admin_user'), 'attendance_cleared',
                  target=f'student #{student_id} in session #{session_id}', details=reason or None)
        _publish_session_update(session_id)
        flash('Attendance record cleared.', 'success')
        return redirect(url_for('attendance_session', session_id=session_id))

    status = {'present': 'Present', 'absent': 'Absent', 'late': 'Late'}[action]
    note = f'Manual override: {reason}' if reason else 'Manual override'
    execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp, note) VALUES(?, ?, ?, ?, ?)',
               (student_id, session_id, status, datetime.now().isoformat(), note))
    log_audit('admin', session.get('admin_user'), 'attendance_overridden',
              target=f'student #{student_id} in session #{session_id}', details=f'set to {status}' + (f' — {reason}' if reason else ''))
    _publish_session_update(session_id)
    return redirect(url_for('attendance_session', session_id=session_id))


@app.route('/admin/session/<int:session_id>/export')
def export_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    records = query_db('SELECT a.*, st.name, st.roll_no, st.branch, st.semester FROM attendance a LEFT JOIN students st ON a.student_id=st.id WHERE a.session_id=?', (session_id,))
    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(['Student ID', 'Name', 'Roll No', 'Branch', 'Semester', 'Status', 'Timestamp', 'Confidence', 'Note'])
    for row in records:
        confidence_pct = round(row['confidence'] * 100, 1) if row['confidence'] is not None else ''
        writer.writerow([row['student_id'], row['name'], row['roll_no'], row['branch'], row['semester'], row['status'], row['timestamp'], confidence_pct, row['note']])
    csv_file.seek(0)
    return send_file(io.BytesIO(csv_file.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name=f'session_{session_id}_attendance.csv')


@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        data = request.get_json() or {}
        name = data.get('name')
        roll_no = data.get('roll_no')
        branch = data.get('branch')
        semester = data.get('semester')
        password = data.get('password')
        gender = data.get('gender', 'other')
        images = data.get('images', [])
        if not name or not roll_no or not branch or not semester or not password or not images:
            return jsonify({'error': 'Missing fields'}), 400

        password_error = validate_password_strength(password)
        if password_error:
            return jsonify({'error': password_error}), 400

        # Payload-size guardrails, on top of the global MAX_CONTENT_LENGTH:
        # cap how many images and how large any single one can be.
        if len(images) > cfg.MAX_REGISTRATION_IMAGES:
            return jsonify({'error': f'Too many images (max {cfg.MAX_REGISTRATION_IMAGES}).'}), 400
        for img_data in images:
            if not isinstance(img_data, str) or len(img_data) > cfg.MAX_IMAGE_BASE64_CHARS:
                return jsonify({'error': 'One or more images is too large.'}), 400

        # Check if roll_no already exists
        existing_roll = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
        if existing_roll:
            return jsonify({'error': 'Roll number already registered'}), 400

        # Check if a face is actually present in the provided images
        has_face = False
        face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
        for img_data in images:
            img = parse_base64_image(img_data)
            frame = np.array(img.convert('RGB'))
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            if len(face_cascade.detectMultiScale(gray, **cfg.REGISTRATION_DETECT_PARAMS)) > 0:
                has_face = True
                break

        if not has_face:
            return jsonify({'error': 'No face detected in the images! Please look directly at the camera in a well-lit area.'}), 400

        # Check if this face already exists (skips cleanly if the
        # embedding gallery is still empty — recognize_face() handles
        # that case on its own).
        existing_student_id = None
        for img_data in images[:5]:  # Check the first 5 images
            found_id, similarity, _error = recognize_face(img_data)
            if found_id is not None and similarity >= get_effective_setting('DUPLICATE_FACE_MATCH_THRESHOLD'):  # Strict threshold for duplicate check
                existing_student_id = found_id
                break

        if existing_student_id is not None:
            existing_student = query_db('SELECT * FROM students WHERE id=?', (existing_student_id,), one=True)
            if existing_student:
                return jsonify({'error': f'Face already registered to {existing_student["name"]} ({existing_student["roll_no"]})'}), 400

        student_id = execute_db('INSERT INTO students(name, roll_no, branch, semester, password) VALUES(?, ?, ?, ?, ?)', (name, roll_no, branch, semester, generate_password_hash(password)))
        save_face_profile(student_id, name, gender, branch)
        saved, skip_reasons = save_face_images(student_id, images)
        if saved == 0:
            # Every image failed quality/detection checks — roll back the
            # student record rather than leaving an account with zero
            # usable face data.
            execute_db('DELETE FROM students WHERE id=?', (student_id,))
            reason_summary = '; '.join(sorted(set(skip_reasons))) if skip_reasons else 'no usable face images'
            return jsonify({'error': f'None of the photos could be used ({reason_summary}). Please retake in good, even lighting with only your face in frame.'}), 400
        execute_db('UPDATE students SET photo_count=? WHERE id=?', (saved, student_id))
        log_audit('student', roll_no, 'student_registered', details=f'{saved} face photo(s) enrolled')
        response = {'status': 'ok', 'student_id': student_id}
        if skip_reasons:
            response['warning'] = f'{len(skip_reasons)} of {len(images)} photos were skipped ({", ".join(sorted(set(skip_reasons)))}); {saved} were used.'
        return jsonify(response)
    return render_template('student_register.html')


@app.route('/student/login', methods=['GET', 'POST'])
@limiter.limit(lambda: cfg.RATE_LIMIT_LOGIN, methods=['POST'])
def student_login():
    if session.get('user_type') == 'student':
        return render_template('student_login.html', logged_in=True)

    if request.method == 'POST':
        data = request.get_json() or {}
        roll_no = data.get('roll_no')
        password = data.get('password')
        captcha_response = data.get('captcha')

        if not roll_no or not password:
            return jsonify({'status': 'error', 'message': 'Roll number and password required'}), 400

        if not _check_captcha(captcha_response):
            log_audit('anonymous', roll_no, 'student_login_failed', details='captcha failed')
            return jsonify({'status': 'error', 'message': 'Incorrect CAPTCHA'}), 400

        student = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
        if not student:
            log_audit('anonymous', roll_no, 'student_login_failed', details='no such roll number')
            return jsonify({'status': 'error', 'message': 'Student not found. Please register first.'}), 404

        if _is_locked_out(student):
            log_audit('anonymous', roll_no, 'student_login_blocked', details='account locked')
            return jsonify({'status': 'error', 'message': 'This account is temporarily locked due to repeated failed login attempts. Please try again in a few minutes.'}), 403

        if not check_password_hash(student['password'], password):
            attempts, locked_until = _register_failed_login('students', student['id'])
            log_audit('anonymous', roll_no, 'student_login_failed', details='wrong password')
            if locked_until:
                return jsonify({'status': 'error', 'message': f'Too many failed attempts. This account is locked for {cfg.LOCKOUT_DURATION_MINUTES} minutes.'}), 403
            return jsonify({'status': 'error', 'message': 'Incorrect password'}), 401

        _clear_failed_logins('students', student['id'])
        session.clear()
        session.permanent = True
        session['student_id'] = student['id']
        session['student_name'] = student['name']
        session['user_type'] = 'student'
        log_audit('student', roll_no, 'student_login_success')

        # Security monitoring: device fingerprinting, network-change/
        # concurrent-session detection, and risk-based escalation — see
        # the helpers' docstrings above student_login(). fingerprint is
        # a lightweight, self-hosted hash computed client-side (see
        # static/app.js's getDeviceFingerprint()); its absence (an old
        # cached page, or a client that blocks it) just means those
        # specific checks are skipped for this login, not an error.
        fingerprint_hash = (data.get('device_fingerprint') or '').strip()[:128] or None
        ip_address = request.remote_addr
        _check_suspicious_device(student['id'], fingerprint_hash, ip_address, request.user_agent.string[:255])
        _check_network_change(student['id'], ip_address)
        _check_concurrent_session(student['id'], ip_address, fingerprint_hash)
        session['login_token'] = _start_login_session(student['id'], ip_address, fingerprint_hash)
        execute_db('UPDATE students SET last_login_ip=?, last_login_at=? WHERE id=?',
                   (ip_address, datetime.now().isoformat(), student['id']))
        _maybe_escalate_student(student['id'])

        student_data = dict(student)
        student_data.pop('password', None)  # never send the password hash to the client
        return jsonify({'status': 'ok', 'student': student_data})

    return render_template('student_login.html', logged_in=False)


@app.route('/student/search')
def student_search():
    roll_no = request.args.get('roll_no', '').strip()
    if not roll_no:
        return jsonify({'status': 'error', 'message': 'Roll number required'}), 400
    student = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
    if student:
        return jsonify({'status': 'ok', 'student': dict(student)})
    return jsonify({'status': 'error', 'message': 'Student not found'}), 404


@app.route('/get-active-sessions')
def get_active_sessions():
    _auto_close_expired_sessions()
    now = datetime.now()
    current_date = now.strftime('%d-%m-%Y')
    current_time = now.strftime('%H:%M')

    # If a student is logged in, only show sessions their group is
    # actually assigned to (see restrict_branch/restrict_semester and
    # _session_group_restriction_error()) — an unrestricted session
    # (the default) still shows for everyone. No student logged in yet
    # (this is also fetched from the pre-login page) means no filtering
    # is possible here; student_mark_attendance() enforces the
    # restriction server-side regardless of what this listing shows.
    viewer = None
    viewer_id = session.get('student_id')
    if viewer_id:
        viewer = query_db('SELECT branch, semester FROM students WHERE id=?', (viewer_id,), one=True)

    sessions = query_db('''
        SELECT s.*, sub.name as subject_name, sub.code as subject_code
        FROM sessions s
        LEFT JOIN subjects sub ON s.subject_id = sub.id
        ORDER BY s.date DESC, s.time DESC
    ''')

    active_sessions = []
    for s in sessions:
        if s['status'] == 'cancelled':
            continue
        if viewer and _session_group_restriction_error(s, viewer):
            continue
        is_active = False
        if s['active'] == 1:
            is_active = True
        elif s['date'] and s['time'] and s['end_date'] and s['end_time']:
            try:
                start_dt = datetime.strptime(f"{s['date']} {s['time']}", '%d-%m-%Y %H:%M')
                end_dt = datetime.strptime(f"{s['end_date']} {s['end_time']}", '%d-%m-%Y %H:%M')

                if s['is_recurring'] == 1:
                    # Recurring every day: check if current date is in range and current time is in range
                    curr_date_dt = datetime.strptime(current_date, '%d-%m-%Y')
                    start_date_dt = datetime.strptime(s['date'], '%d-%m-%Y')
                    end_date_dt = datetime.strptime(s['end_date'], '%d-%m-%Y')

                    if start_date_dt <= curr_date_dt <= end_date_dt and s['time'] <= current_time <= s['end_time']:
                        # Date and time are both in range
                        is_active = True
                else:
                    # Not recurring: just check if now is between start and end
                    if start_dt <= now <= end_dt:
                        is_active = True
            except Exception as e:
                logger.warning(f"Error checking session bounds for ID {s['id']}: {e}", exc_info=True)

        if is_active:
            # Add formatted info for the UI
            session_dict = dict(s)
            session_dict['is_auto_active'] = (s['active'] == 0)
            try:
                session_dict['time_12h'] = datetime.strptime(s['time'], '%H:%M').strftime('%I:%M %p')
                session_dict['end_time_12h'] = datetime.strptime(s['end_time'], '%H:%M').strftime('%I:%M %p')
            except (ValueError, TypeError):
                session_dict['time_12h'] = s['time']
                session_dict['end_time_12h'] = s['end_time']
            active_sessions.append(session_dict)

    return jsonify({'sessions': active_sessions})


@app.route('/student/attend')
def student_attend():
    session_id_raw = request.args.get('session_id')
    student_id = session.get('student_id')

    if not student_id:
        return redirect(url_for('student_login'))

    try:
        session_id = int(session_id_raw) if session_id_raw else None
    except (TypeError, ValueError):
        session_id = None

    if not session_id:
        flash('Please select a session first', 'warning')
        return redirect(url_for('student_login'))

    session_info = query_db('SELECT s.*, sub.name as subject_name, sub.code as subject_code FROM sessions s LEFT JOIN subjects sub ON s.subject_id = sub.id WHERE s.id=?', (session_id,), one=True)
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)

    if not session_info or not student:
        flash('Session or student record not found', 'error')
        return redirect(url_for('student_login'))

    group_error = _session_group_restriction_error(session_info, student)
    if group_error:
        flash(group_error, 'error')
        return redirect(url_for('student_login'))

    return render_template('student_attend.html', session_info=dict(session_info), student=dict(student))


@app.route('/student/liveness-challenge')
def student_liveness_challenge():
    """Issues one random active-liveness challenge for the logged-in
    student to perform during their next attendance-marking capture — see
    face_security.py and config.ACTIVE_LIVENESS_* for what this defends
    against. The client (student_attend.html) calls this right before
    starting its capture burst, shows the returned prompt on screen, and
    sends the token back with the captured frames to
    /student/attend/mark, where _consume_liveness_challenge() verifies it
    was actually issued to this session and hasn't expired or been used
    already.

    Defense-in-depth: the before_request handler already requires a
    logged-in student for this endpoint (see restrict_access()), but
    checked again here in case that gate is ever changed.
    """
    if not session.get('student_id'):
        return jsonify({'status': 'error', 'message': 'Student login required'}), 401
    if not cfg.ACTIVE_LIVENESS_ENABLED:
        return jsonify({'enabled': False})
    challenge = _issue_liveness_challenge()
    return jsonify({'enabled': True, 'challenge': challenge['type'], 'token': challenge['token'], 'prompt': challenge['prompt']})


@app.route('/student/test-image', methods=['POST'])
def test_image():
    """Test endpoint to check if image is being received properly"""
    try:
        data = request.get_json() or {}
        image = data.get('image')

        if not image:
            return jsonify({'status': 'error', 'message': 'No image received'}), 400

        # Process image data
        if not image.startswith('data:image/'):
            return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400

        img_data = image.split(',')[1] if ',' in image else image
        img_bytes = base64.b64decode(img_data)
        img = Image.open(io.BytesIO(img_bytes))

        # Convert to numpy array and grayscale
        nparr = np.array(img)
        if len(nparr.shape) == 3 and nparr.shape[2] == 3:
            gray = cv2.cvtColor(nparr, cv2.COLOR_RGB2GRAY)
        elif len(nparr.shape) == 2:
            gray = nparr
        else:
            return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400

        # Save the image for inspection
        test_img_path = cfg.TEST_CAPTURE_PATH
        cv2.imwrite(test_img_path, gray)

        return jsonify({
            'status': 'success',
            'message': f'Image received and saved to {test_img_path}',
            'shape': gray.shape,
            'size': len(img_bytes)
        })

    except Exception as e:
        logger.error(f'Test image error: {e}', exc_info=True)
        return jsonify({'status': 'error', 'message': f'Error: {str(e)}'}), 500


def decode_base64_to_gray(image):
    """Decode a data-URL base64 image string into a grayscale numpy array,
    or return None if it isn't a valid image."""
    if not image or not image.startswith('data:image/'):
        return None
    img_data = image.split(',')[1] if ',' in image else image
    img_bytes = base64.b64decode(img_data)
    img = Image.open(io.BytesIO(img_bytes))
    nparr = np.array(img)
    if len(nparr.shape) == 3 and nparr.shape[2] == 3:
        return cv2.cvtColor(nparr, cv2.COLOR_RGB2GRAY)
    elif len(nparr.shape) == 2:
        return nparr
    return None


def decode_base64_to_rgb(image):
    """Decode a data-URL base64 image string into an RGB numpy array (for
    embedding computation, which needs color), or None if invalid."""
    if not image or not image.startswith('data:image/'):
        return None
    img_data = image.split(',')[1] if ',' in image else image
    img_bytes = base64.b64decode(img_data)
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    return np.array(img)


@app.route('/student/attend/mark', methods=['POST'])
@limiter.limit(lambda: cfg.RATE_LIMIT_ATTENDANCE)
def student_mark_attendance():
    try:
        # Defense-in-depth: the before_request handler already requires a
        # logged-in student for this endpoint, but we check again here so
        # this function is safe even if that gate is ever changed.
        student_id = session.get('student_id')
        if not student_id:
            return jsonify({'status': 'error', 'message': 'Student login required'}), 401

        student_row = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
        if not student_row:
            return jsonify({'status': 'error', 'message': 'Student record not found.'}), 404

        # Keeps this login session "active" for concurrent-session
        # detection (see _check_concurrent_session()) — best-effort, a
        # missing/stale token here just means this one signal is less
        # precise, not an error worth failing the request over.
        login_token = session.get('login_token')
        if login_token:
            execute_db('UPDATE student_login_sessions SET last_seen_at=? WHERE session_token=?',
                       (datetime.now().isoformat(), login_token))

        data = request.get_json() or {}
        session_id = data.get('session_id')
        images = data.get('images')
        # Back-compat: accept the older two-field {image, image2} shape
        # too, padding it out (this just means the blink signal won't
        # have much of a burst to work with — motion-only, effectively).
        if images is None:
            legacy_image, legacy_image2 = data.get('image'), data.get('image2')
            images = [legacy_image, legacy_image2] if legacy_image and legacy_image2 else None
        # student_id is intentionally NOT read from the request body — it is
        # always the currently logged-in student, taken from the server-side
        # session, so one student can't mark attendance as another by
        # editing the payload.

        # Convert to integers with validation
        try:
            session_id = int(session_id) if session_id else None
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'Invalid session_id'}), 400

        if not session_id or not images or len(images) < 2:
            return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        if len(images) > cfg.LIVENESS_FRAME_COUNT:
            images = images[:cfg.LIVENESS_FRAME_COUNT]

        for img in images:
            if not isinstance(img, str) or len(img) > cfg.MAX_IMAGE_BASE64_CHARS:
                return jsonify({'status': 'error', 'message': 'Image too large.'}), 400

        # Lazily auto-close this one session if its attendance window has
        # elapsed (see SESSION_AUTO_CLOSE_ENABLED) before evaluating
        # anything else against it.
        session_row = _maybe_auto_close_session(query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True))
        if not session_row:
            return jsonify({'status': 'error', 'message': 'Session not found'}), 404
        if session_row['status'] == 'cancelled':
            return jsonify({'status': 'error', 'message': 'This session has been cancelled.'}), 400
        if not session_row['active']:
            return jsonify({'status': 'error', 'message': 'Session not active'}), 400

        # Session participant/group restriction (see restrict_branch/
        # restrict_semester in admin_sessions()) — a session with neither
        # set (the default, and the only possibility before this feature
        # existed) is open to every student, unchanged from before.
        group_error = _session_group_restriction_error(session_row, student_row)
        if group_error:
            log_audit('student', str(student_id), 'attendance_blocked_group', target=f'session #{session_id}')
            return jsonify({'status': 'error', 'message': group_error}), 403

        # Optional network restriction ("geofencing" by IP rather than
        # GPS) — a session-specific allowed_networks list overrides the
        # global ATTENDANCE_ALLOWED_NETWORKS for this one session; see
        # is_ip_allowed_for_attendance(). No-op if neither is configured.
        if not is_ip_allowed_for_attendance(request.remote_addr, session_row):
            log_audit('student', str(student_id), 'attendance_blocked_network', target=f'session #{session_id}', details=f'ip={request.remote_addr}')
            return jsonify({'status': 'error', 'message': 'Attendance can only be marked from an allowed network.'}), 403

        # Attendance-marking lockout: blocks a student account that has
        # racked up too many spoof-suspected / liveness-challenge-failed
        # events in a row (see ATTENDANCE_SECURITY_FAILURE_ACTIONS and
        # _register_attendance_security_failure() below) — separate from
        # the login lockout, and from RATE_LIMIT_ATTENDANCE's per-IP
        # throttling. Checked before any of the expensive face-processing
        # work below. Also covers automatic risk-score escalation (see
        # _is_attendance_locked_out()'s docstring) — both give the same
        # generic message so a flagged student can't tell which specific
        # signal tripped it.
        if _is_attendance_locked_out(student_row):
            log_audit('student', str(student_id), 'attendance_marking_blocked_lockout',
                       details=f'escalated={bool(student_row["security_escalated"])} locked_until={student_row["attendance_locked_until"]}')
            return jsonify({
                'status': 'error',
                'message': 'Attendance marking is temporarily locked for this account due to suspicious activity. Please contact an administrator.'
            }), 403

        # Check if already marked
        existing = query_db('SELECT * FROM attendance WHERE student_id=? AND session_id=?', (student_id, session_id), one=True)
        if existing:
            return jsonify({'status': 'already_marked', 'message': 'Already marked attendance'}), 200

        # Late-entry rule (see LATE_ENTRY_* in config.py, and a session's
        # own attendance_window_minutes/grace_period_minutes overrides):
        # a no-op unless explicitly enabled. Checked before the expensive
        # face-processing work below, same reasoning as the lockout check
        # above.
        mark_status, late_entry_error = _late_entry_status(session_row)
        if late_entry_error:
            log_audit('student', str(student_id), 'attendance_blocked_late', target=f'session #{session_id}')
            return jsonify({'status': 'error', 'message': late_entry_error}), 200

        # Active liveness challenge (see face_security.py + config.py's
        # ACTIVE_LIVENESS_* comments): the client must have already
        # fetched a challenge from /student/liveness-challenge and
        # performed the requested action during capture. Consumed
        # (single-use) here regardless of what happens next, so a token
        # can't be retried against a second attempt.
        challenge_type = None
        if cfg.ACTIVE_LIVENESS_ENABLED:
            challenge_token = data.get('challenge_token')
            challenge_type, challenge_error = _consume_liveness_challenge(challenge_token)
            if challenge_error:
                return jsonify({'status': 'error', 'message': challenge_error}), 200

        # Try to recognize face
        try:
            if not all(isinstance(img, str) and img.startswith('data:image/') for img in images):
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400

            frames_gray = [decode_base64_to_gray(img) for img in images]
            if any(g is None for g in frames_gray):
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400

            if not os.path.exists(CASCADE_PATH):
                return jsonify({'status': 'error', 'message': 'Face cascade file not found'}), 500

            # Debug: save the first frame to check what the server saw
            cv2.imwrite(cfg.DEBUG_IMAGE_PATH, frames_gray[0])

            cascade = cv2.CascadeClassifier(CASCADE_PATH)
            eye_cascade = get_eye_cascade()

            # Multi-face rejection on the reference (first) frame: catches
            # someone else visibly in frame during the capture.
            primary_box, multi_face_error = detect_single_face(cascade, frames_gray[0], cfg.MARK_ATTENDANCE_DETECT_PRIMARY_PARAMS)
            if primary_box is None and multi_face_error and 'Multiple' in multi_face_error:
                return jsonify({'status': 'error', 'message': multi_face_error}), 200
            if primary_box is None:
                # Fall back to the looser detection params before giving up.
                primary_box, multi_face_error = detect_single_face(cascade, frames_gray[0], cfg.MARK_ATTENDANCE_DETECT_FALLBACK_PARAMS)
                if primary_box is None:
                    return jsonify({'status': 'error', 'message': multi_face_error}), 200

            # Liveness: motion across the frame burst, plus (if enabled) a
            # detectable blink — see check_liveness_sequence()'s docstring
            # for what this does and doesn't defend against.
            is_live, _boxes = check_liveness_sequence(cascade, eye_cascade, frames_gray, cfg.MARK_ATTENDANCE_DETECT_PRIMARY_PARAMS)
            if not is_live:
                return jsonify({
                    'status': 'error',
                    'message': 'Could not confirm a live camera feed. Please make sure you are using a live camera (not a photo or screen), blink naturally, and try again.'
                }), 200

            # Active liveness challenge: the specific random action issued
            # above must actually show up in this burst — see
            # _verify_active_challenge()'s docstring. Distinct from (and
            # in addition to) the passive motion+blink check just above.
            if cfg.ACTIVE_LIVENESS_ENABLED and challenge_type and not _verify_active_challenge(challenge_type, frames_gray, _boxes, eye_cascade):
                log_audit('student', str(student_id), 'attendance_liveness_challenge_failed',
                          target=f'session #{session_id}', details=f'challenge={challenge_type}')
                _attempts, _locked_until = _register_attendance_security_failure(student_id)
                prompt_phrase = challenge_type.replace('_', ' ')
                if _locked_until:
                    log_audit('student', str(student_id), 'attendance_security_lockout',
                               target=f'{student_row["name"]} ({student_row["roll_no"]})',
                               details=f'{_attempts} consecutive spoof/liveness failures; locked until {_locked_until}')
                    return jsonify({
                        'status': 'error',
                        'message': f'Too many failed verification attempts. Attendance marking is now locked for this account for {cfg.ATTENDANCE_LOCKOUT_DURATION_MINUTES} minutes. Please contact an administrator.'
                    }), 403
                return jsonify({
                    'status': 'error',
                    'message': f'Could not confirm the requested action ({prompt_phrase}). Please try again.'
                }), 200

            # Quality + presentation-attack ("spoof") checks on the
            # reference frame's face crop — the same blur/brightness gate
            # already applied to registration photos (see
            # assess_image_quality()), plus a heuristic screen/print
            # replay check (see face_security.compute_screen_replay_score
            # and config.ANTI_SPOOF_ENABLED).
            primary_face_crop_gray = frames_gray[0][primary_box[1]:primary_box[1] + primary_box[3], primary_box[0]:primary_box[0] + primary_box[2]]
            quality_issues = assess_image_quality(primary_face_crop_gray)
            if quality_issues:
                return jsonify({
                    'status': 'error',
                    'message': f'Image quality issue ({", ".join(quality_issues)}). Please retry with better lighting/focus.'
                }), 200
            if cfg.ANTI_SPOOF_ENABLED and face_security.is_likely_screen_replay(primary_face_crop_gray, get_effective_setting('ANTI_SPOOF_MAX_PERIODICITY_RATIO')):
                log_audit('student', str(student_id), 'attendance_spoof_suspected', target=f'session #{session_id}', details='screen/print replay pattern detected')
                _attempts, _locked_until = _register_attendance_security_failure(student_id)
                if _locked_until:
                    log_audit('student', str(student_id), 'attendance_security_lockout',
                               target=f'{student_row["name"]} ({student_row["roll_no"]})',
                               details=f'{_attempts} consecutive spoof/liveness failures; locked until {_locked_until}')
                    return jsonify({
                        'status': 'error',
                        'message': f'Too many failed verification attempts. Attendance marking is now locked for this account for {cfg.ATTENDANCE_LOCKOUT_DURATION_MINUTES} minutes. Please contact an administrator.'
                    }), 403
                return jsonify({
                    'status': 'error',
                    'message': 'This looks like it might be a photo or screen rather than a live camera. Please use your live camera.'
                }), 200

            # 1:1 verification: compare the captured face only against the
            # logged-in student's OWN stored embeddings (not the whole
            # gallery). This is both faster and a better fit for "confirm
            # this is who they claim to be" than open-ended identification.
            student_embeddings = get_student_embeddings(student_id)
            if not student_embeddings:
                return jsonify({'status': 'error', 'message': 'No registered face data found for your account. Please contact administrator.'}), 200

            rgb = decode_base64_to_rgb(images[0])
            if rgb is None:
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
            face_crop_rgb = align_face_crop(frames_gray[0], rgb, primary_box, eye_cascade)

            embedding = compute_embedding(face_crop_rgb)
            _, similarity = find_best_match(embedding, [(student_id, e) for e in student_embeddings])

            logger.debug(f'Best match similarity: {similarity:.3f}, Student ID: {student_id}')

            # Cosine similarity: HIGHER is a better match (opposite
            # direction from the old LBPH "confidence" scale).
            if similarity >= get_effective_setting('MARK_ATTENDANCE_MATCH_THRESHOLD'):
                status = mark_status  # 'Present' or 'Late' — see _late_entry_status() above
                note = f'Face recognized (similarity: {similarity:.2f})'
                # Atomic insert-if-absent (see its docstring): the
                # 'already marked' check above is the fast, common-case
                # path — this covers the narrow race where two requests
                # for the same student+session land close together.
                inserted = _insert_attendance_if_absent(student_id, session_id, status, datetime.now().isoformat(), note, confidence=similarity)
                if not inserted:
                    _record_attendance_mark_metric('already_marked')
                    return jsonify({'status': 'already_marked', 'message': 'Already marked attendance'}), 200
                # A genuine, successful mark clears any accumulated
                # spoof/liveness-failure count — see
                # _register_attendance_security_failure()'s docstring.
                _clear_attendance_security_failures(student_id)
                _record_attendance_mark_metric('late' if status == 'Late' else 'present')
                # Return success with face match details
                profile = query_db('SELECT name FROM students WHERE id=?', (student_id,), one=True)
                score_out_of_100 = max(0, min(similarity, 1.0)) * 100
                if status == 'Late':
                    match_msg = f'Face detected and matched to {profile["name"]} (match score: {score_out_of_100:.1f}/100) — marked Late.'
                else:
                    match_msg = f'Face detected and matched to {profile["name"]} (match score: {score_out_of_100:.1f}/100)'
                return jsonify({'status': 'present', 'attendance_status': status, 'message': match_msg, 'confidence': score_out_of_100})
            else:
                score_out_of_100 = max(0, min(similarity, 1.0)) * 100
                _record_attendance_mark_metric('error')
                return jsonify({'status': 'error', 'message': f'Face not recognized with high enough confidence (Score: {score_out_of_100:.1f}/100). Please improve lighting and try again.'}), 200

        except Exception as e:
            logger.error(f'Face recognition error during attendance marking: {e}', exc_info=True)
            error_reporting.capture_exception(e)
            return jsonify({'status': 'error', 'message': f'Face recognition failed: {str(e)}'}), 200

    except Exception as e:
        logger.error(f'Unexpected error in student_mark_attendance: {e}', exc_info=True)
        error_reporting.capture_exception(e)
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 200


@app.route('/student/history')
def student_history():
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))

    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    # All sessions, left-joined against this student's own attendance row
    # (if any) — shows explicit "Absent" for a session they were never
    # marked present/late for, not just the sessions they do have a row
    # for, so they can request a correction for a session they believe
    # they were wrongly marked absent in too.
    attendance = query_db('''
        SELECT ses.id as session_id, ses.title, ses.date, ses.time, ses.end_date,
               a.status, a.timestamp
        FROM sessions ses
        LEFT JOIN attendance a ON a.session_id = ses.id AND a.student_id = ?
        ORDER BY ses.id DESC
    ''', (student_id,))
    total = len(attendance)
    counted_present = len([r for r in attendance if r['status'] and _attendance_counts_as_present(r['status'])])
    percentage = round((counted_present / total * 100) if total > 0 else 0, 1)

    # Sessions this student already has a pending correction request for
    # — the template disables the "Request Correction" action for those,
    # rather than letting a second request pile up before the first is
    # resolved.
    pending_session_ids = {
        r['session_id'] for r in
        query_db("SELECT session_id FROM attendance_correction_requests WHERE student_id=? AND status='pending'", (student_id,))
    }
    locked_session_ids = {r['session_id'] for r in attendance if _is_session_edit_locked(r)}

    correction_requests = query_db('''
        SELECT cr.*, ses.title as session_title, ses.date as session_date
        FROM attendance_correction_requests cr
        LEFT JOIN sessions ses ON cr.session_id = ses.id
        WHERE cr.student_id=?
        ORDER BY cr.created_at DESC
    ''', (student_id,))

    return render_template('student_history.html', student=student, attendance=attendance, percentage=percentage,
                            pending_session_ids=pending_session_ids, locked_session_ids=locked_session_ids,
                            correction_requests=correction_requests, low_attendance_threshold=cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT)


@app.route('/student/attendance/correction-request', methods=['POST'])
def request_attendance_correction():
    """Student half of the correction workflow: file a request against
    one session, with a reason, for an admin to approve or reject — see
    resolve_attendance_correction() for the admin half."""
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))

    session_id = request.form.get('session_id')
    requested_status = request.form.get('requested_status')
    reason = (request.form.get('reason') or '').strip()[:cfg.CORRECTION_REASON_MAX_LENGTH]

    if not session_id or requested_status not in ('Present', 'Absent', 'Late') or not reason:
        flash('Please choose the correct status and give a reason for the request.', 'error')
        return redirect(url_for('student_history'))

    session_row = query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True)
    if not session_row:
        flash('Session not found.', 'error')
        return redirect(url_for('student_history'))
    if _is_session_edit_locked(session_row):
        flash(f'This session is more than {cfg.ATTENDANCE_EDIT_LOCK_DAYS} days old — the correction window for it has closed.', 'error')
        return redirect(url_for('student_history'))

    existing_pending = query_db(
        "SELECT 1 FROM attendance_correction_requests WHERE student_id=? AND session_id=? AND status='pending'",
        (student_id, session_id), one=True
    )
    if existing_pending:
        flash('You already have a pending correction request for this session.', 'error')
        return redirect(url_for('student_history'))

    execute_db('''INSERT INTO attendance_correction_requests(student_id, session_id, requested_status, reason, status, created_at)
                  VALUES(?, ?, ?, ?, 'pending', ?)''',
               (student_id, session_id, requested_status, reason, datetime.now().isoformat()))
    log_audit('student', str(student_id), 'attendance_correction_requested',
              target=f'session #{session_id}', details=f'requested {requested_status}')

    flash('Correction request submitted — an admin will review it.', 'success')
    return redirect(url_for('student_history'))


@app.route('/admin/attendance/corrections')
def admin_corrections():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    status_filter = request.args.get('status', 'pending')
    if status_filter not in ('pending', 'approved', 'rejected', 'all'):
        status_filter = 'pending'

    query = '''
        SELECT cr.*, st.name as student_name, st.roll_no,
               ses.title as session_title, ses.date as session_date, ses.time as session_time,
               subj.name as subject_name
        FROM attendance_correction_requests cr
        LEFT JOIN students st ON cr.student_id = st.id
        LEFT JOIN sessions ses ON cr.session_id = ses.id
        LEFT JOIN subjects subj ON ses.subject_id = subj.id
    '''
    params: tuple = ()
    if status_filter != 'all':
        query += ' WHERE cr.status = ?'
        params = (status_filter,)
    query += ' ORDER BY cr.created_at DESC'
    requests_rows = query_db(query, params)

    return render_template('admin_corrections.html', requests=requests_rows, status_filter=status_filter)


@app.route('/admin/attendance/corrections/<int:request_id>/<action>', methods=['POST'])
def resolve_attendance_correction(request_id, action):
    """Admin half of the correction workflow. Approving applies
    requested_status to the attendance table (same delete+insert pattern
    as session_override, so it shows up identically as a manual change);
    rejecting just records the decision. Both respect the same edit-lock
    window as a direct admin override."""
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    if action not in ('approve', 'reject'):
        return redirect(url_for('admin_corrections'))

    correction = query_db('SELECT * FROM attendance_correction_requests WHERE id=?', (request_id,), one=True)
    if not correction:
        flash('Correction request not found.', 'error')
        return redirect(url_for('admin_corrections'))
    if correction['status'] != 'pending':
        flash('This request has already been resolved.', 'error')
        return redirect(url_for('admin_corrections'))

    admin_note = (request.form.get('admin_note') or '').strip()[:cfg.CORRECTION_REASON_MAX_LENGTH]

    if action == 'approve':
        session_row = query_db('SELECT * FROM sessions WHERE id=?', (correction['session_id'],), one=True)
        if session_row and _is_session_edit_locked(session_row):
            flash(f'This session is more than {cfg.ATTENDANCE_EDIT_LOCK_DAYS} days old — its attendance record is locked; the request cannot be approved as-is.', 'error')
            return redirect(url_for('admin_corrections'))
        execute_db('DELETE FROM attendance WHERE session_id=? AND student_id=?', (correction['session_id'], correction['student_id']))
        note = f"Correction approved: {correction['reason']}" if correction['reason'] else 'Correction approved'
        execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp, note) VALUES(?, ?, ?, ?, ?)',
                   (correction['student_id'], correction['session_id'], correction['requested_status'], datetime.now().isoformat(), note))
        _publish_session_update(correction['session_id'])
        new_status = 'approved'
    else:
        new_status = 'rejected'

    execute_db('''UPDATE attendance_correction_requests
                  SET status=?, resolved_at=?, resolved_by=?, admin_note=?
                  WHERE id=?''',
               (new_status, datetime.now().isoformat(), session.get('admin_user'), admin_note or None, request_id))
    log_audit('admin', session.get('admin_user'), f'attendance_correction_{new_status}',
              target=f'student #{correction["student_id"]} in session #{correction["session_id"]}',
              details=f'requested {correction["requested_status"]}' + (f' — {admin_note}' if admin_note else ''))

    flash(f'Correction request {new_status}.', 'success')
    return redirect(url_for('admin_corrections'))


@app.route('/admin/reports')
def admin_reports():
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    subjects = query_db('SELECT * FROM subjects ORDER BY name')
    semesters = [r['semester'] for r in query_db('SELECT DISTINCT semester FROM students WHERE semester IS NOT NULL ORDER BY semester')]

    subject_id = request.args.get('subject_id') or None
    semester = request.args.get('semester') or None
    granularity = request.args.get('granularity', 'day')
    if granularity not in ('day', 'week', 'month'):
        granularity = 'day'

    def _parse_input_date(field):
        raw = request.args.get(field)
        if not raw:
            return None
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            return None

    start_date = _parse_input_date('start_date')
    end_date = _parse_input_date('end_date')

    report_rows, trend = _compute_attendance_report(subject_id=subject_id, start_date=start_date, end_date=end_date,
                                                      semester=semester, trend_granularity=granularity)

    return render_template('admin_reports.html', report_rows=report_rows, trend=trend, subjects=subjects,
                            semesters=semesters, threshold=cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT,
                            filters={
                                'subject_id': subject_id, 'semester': semester, 'granularity': granularity,
                                'start_date': request.args.get('start_date', ''), 'end_date': request.args.get('end_date', ''),
                            })


@app.route('/admin/reports/export')
def export_attendance_report():
    if not session.get('admin_user'):
        return redirect(url_for('login'))

    subject_id = request.args.get('subject_id') or None
    semester = request.args.get('semester') or None

    def _parse_input_date(field):
        raw = request.args.get(field)
        if not raw:
            return None
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            return None

    report_rows, _trend = _compute_attendance_report(subject_id=subject_id, start_date=_parse_input_date('start_date'),
                                                       end_date=_parse_input_date('end_date'), semester=semester)

    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(['Roll No', 'Name', 'Semester', 'Total Sessions', 'Present', 'Late', 'Absent', 'Percentage', 'Below Threshold'])
    for row in report_rows:
        writer.writerow([row['roll_no'], row['name'], row['semester'], row['total_sessions'],
                          row['present'], row['late'], row['absent'], row['percentage'], 'Yes' if row['below_threshold'] else 'No'])
    csv_file.seek(0)
    return send_file(io.BytesIO(csv_file.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name='attendance_report.csv')


@app.route('/student/register-face', methods=['GET', 'POST'])
def student_register_face():
    """Self-service face registration for a student who already has an
    account but no face photos yet — the case for anyone created via
    admin CSV bulk-import (see bulk_import_students()), which creates
    accounts without capturing faces. Reuses the same per-image
    processing as registration (multi-face rejection, quality checks,
    alignment) but adds to the ALREADY-authenticated student from the
    session, never a client-supplied id."""
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    if request.method == 'GET':
        existing_count = len(get_student_embeddings(student_id))
        return render_template('student_register_face.html', student=student, existing_count=existing_count)

    data = request.get_json() or {}
    images = data.get('images') or []
    if not images:
        return jsonify({'error': 'No images provided'}), 400
    if len(images) > cfg.MAX_REGISTRATION_IMAGES:
        return jsonify({'error': f'Too many images (max {cfg.MAX_REGISTRATION_IMAGES}).'}), 400
    for img in images:
        if not isinstance(img, str) or len(img) > cfg.MAX_IMAGE_BASE64_CHARS:
            return jsonify({'error': 'One or more images is too large.'}), 400

    saved, skip_reasons = save_face_images(student_id, images)
    if saved == 0:
        reason_summary = '; '.join(sorted(set(skip_reasons))) if skip_reasons else 'no usable face images'
        return jsonify({'error': f'None of the photos could be used ({reason_summary}). Please retake in good, even lighting with only your face in frame.'}), 400

    new_total = len(get_student_embeddings(student_id))
    execute_db('UPDATE students SET photo_count=? WHERE id=?', (new_total, student_id))
    log_audit('student', student['roll_no'], 'student_added_face_photos', details=f'+{saved} photo(s)')

    response = {'status': 'ok', 'saved': saved, 'total_photos': new_total}
    if skip_reasons:
        response['warning'] = f'{len(skip_reasons)} of {len(images)} photos were skipped ({", ".join(sorted(set(skip_reasons)))}); {saved} were used.'
    return jsonify(response)


@app.route('/student/face-photos')
def student_face_photos():
    """Lists this student's own registered face photos — the "remove
    individual registered face images" feature — plus their current
    enrollment status/quality, so they can see why they might be
    flagged for re-enrollment (see _enrollment_status())."""
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    photos = query_db(
        'SELECT id, image_filename, quality_score, model_version, created_at FROM face_embeddings WHERE student_id=? ORDER BY created_at',
        (student_id,)
    )
    return render_template('student_face_photos.html', student=student, photos=photos,
                            enrollment=_enrollment_status(student_id))


@app.route('/student/face-photos/<int:embedding_id>/image')
def student_face_photo_image(embedding_id):
    """Serves the thumbnail for one of the logged-in student's own
    registered photos — scoped to student_id so a guessed embedding id
    belonging to someone else 404s rather than leaking their photo."""
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))
    row = query_db('SELECT image_filename FROM face_embeddings WHERE id=? AND student_id=?', (embedding_id, student_id), one=True)
    if not row or not row['image_filename']:
        return ('', 404)
    return send_from_directory(DATA_DIR, row['image_filename'])


@app.route('/admin/face-photos/<int:embedding_id>/image')
def admin_face_photo_image(embedding_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    row = query_db('SELECT image_filename FROM face_embeddings WHERE id=?', (embedding_id,), one=True)
    if not row or not row['image_filename']:
        return ('', 404)
    return send_from_directory(DATA_DIR, row['image_filename'])


@app.route('/student/face-photos/<int:embedding_id>/delete', methods=['POST'])
def delete_own_face_photo(embedding_id):
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))

    student = query_db('SELECT roll_no FROM students WHERE id=?', (student_id,), one=True)
    if _delete_single_embedding(student_id, embedding_id):
        log_audit('student', student['roll_no'] if student else str(student_id), 'face_photo_removed', target=f'embedding #{embedding_id}')
        flash('Photo removed.', 'success')
    else:
        flash('Photo not found.', 'error')
    return redirect(url_for('student_face_photos'))


@app.route('/student/reenroll', methods=['POST'])
def reenroll_face():
    """Face re-enrollment/update workflow: wipes ALL of this student's
    existing face data (unlike student_register_face(), which only ever
    adds) and sends them to re-capture from scratch — for someone whose
    appearance has changed enough, or whose enrollment quality is poor
    enough (see the re-enrollment reminder on their profile), that
    starting over is more useful than adding a few more photos to an
    already-weak gallery."""
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))
    student = query_db('SELECT roll_no FROM students WHERE id=?', (student_id,), one=True)

    _reset_student_face_data(student_id)
    log_audit('student', student['roll_no'] if student else str(student_id), 'face_reenrollment_started')

    flash('Your previous face photos have been cleared. Please capture new ones now.', 'success')
    return redirect(url_for('student_register_face'))


@app.route('/student/profile')
def student_profile():
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    enrollment = _enrollment_status(student_id)
    attendance = query_db('SELECT status FROM attendance WHERE student_id=?', (student_id,))
    total = len(attendance)
    present = len([r for r in attendance if r['status'] == 'Present'])
    percentage = round((present / total * 100) if total > 0 else 0, 1)

    return render_template(
        'student_profile.html',
        student=student,
        embedding_count=enrollment['photo_count'],
        enrollment=enrollment,
        total_sessions=total,
        percentage=percentage,
        low_attendance_threshold=cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT,
    )


@app.route('/student/profile/change-password', methods=['POST'])
def student_change_password():
    student_id = session.get('student_id')
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))

    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not check_password_hash(student['password'], current_password):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('student_profile'))

    if new_password != confirm_password:
        flash('New password and confirmation do not match.', 'error')
        return redirect(url_for('student_profile'))

    password_error = validate_password_strength(new_password)
    if password_error:
        flash(password_error, 'error')
        return redirect(url_for('student_profile'))

    execute_db('UPDATE students SET password=? WHERE id=?', (generate_password_hash(new_password), student_id))
    log_audit('student', student['roll_no'], 'student_changed_own_password')
    flash('Password updated.', 'success')
    return redirect(url_for('student_profile'))


@app.route('/metrics')
def metrics():
    """Prometheus text exposition format — see METRICS_ENABLED/
    METRICS_AUTH_TOKEN in config.py and the "In-process metrics" comment
    near log_audit() above for what's tracked and its limits (in-memory,
    per-worker-process only)."""
    if not cfg.METRICS_ENABLED:
        return ('metrics disabled', 404)
    if cfg.METRICS_AUTH_TOKEN:
        auth_header = request.headers.get('Authorization', '')
        bearer_token = auth_header[7:] if auth_header.startswith('Bearer ') else None
        provided = request.args.get('token') or bearer_token
        if provided != cfg.METRICS_AUTH_TOKEN:
            return ('unauthorized', 401)

    lines = []

    def _metric(name, help_text, metric_type):
        lines.append(f'# HELP {name} {help_text}')
        lines.append(f'# TYPE {name} {metric_type}')

    with _METRICS_LOCK:
        _metric('http_requests_total', 'Total HTTP requests by route, method, and status class.', 'counter')
        for (endpoint, method, status_class), count in sorted(_metrics_http_requests_total.items()):
            lines.append(f'http_requests_total{{endpoint="{endpoint}",method="{method}",status="{status_class}"}} {count}')

        _metric('http_request_duration_seconds_sum', 'Total time spent handling requests, by route.', 'counter')
        for endpoint, total_seconds in sorted(_metrics_http_request_duration_sum.items()):
            lines.append(f'http_request_duration_seconds_sum{{endpoint="{endpoint}"}} {total_seconds:.6f}')

        _metric('http_request_duration_seconds_count', 'Total number of timed requests, by route.', 'counter')
        for endpoint, count in sorted(_metrics_http_request_duration_count.items()):
            lines.append(f'http_request_duration_seconds_count{{endpoint="{endpoint}"}} {count}')

        _metric('audit_events_total', 'Total audit log events recorded, by action.', 'counter')
        for action, count in sorted(_metrics_audit_events_total.items()):
            lines.append(f'audit_events_total{{action="{action}"}} {count}')

        _metric('attendance_marks_total', 'Total self-service attendance-marking attempts, by outcome.', 'counter')
        for outcome, count in sorted(_metrics_attendance_marks_total.items()):
            lines.append(f'attendance_marks_total{{outcome="{outcome}"}} {count}')

    _metric('app_uptime_seconds', 'Seconds since this worker process started.', 'gauge')
    lines.append(f'app_uptime_seconds {time.time() - _METRICS_START_TIME:.2f}')

    # A handful of current-state gauges straight from the database,
    # cheap enough to compute on every scrape (small/single-department
    # scale — see README's documented trade-offs).
    _metric('students_total', 'Total registered student accounts.', 'gauge')
    lines.append(f"students_total {query_db('SELECT COUNT(*) as c FROM students', one=True)['c']}")
    _metric('sessions_active', 'Sessions currently marked active.', 'gauge')
    lines.append(f"sessions_active {query_db('SELECT COUNT(*) as c FROM sessions WHERE active=1', one=True)['c']}")
    _metric('security_notifications_unread', 'Unread security notifications awaiting admin review.', 'gauge')
    lines.append(f"security_notifications_unread {query_db('SELECT COUNT(*) as c FROM security_notifications WHERE is_read=0', one=True)['c']}")

    return Response('\n'.join(lines) + '\n', mimetype='text/plain; version=0.0.4')


if __name__ == '__main__':
    init_databases()
    # Safe-by-default: binds to localhost only, debug mode off.
    # Override deliberately via environment variables for local development,
    # e.g.: FLASK_DEBUG=1 FLASK_RUN_HOST=0.0.0.0 python app.py
    app.run(host=cfg.FLASK_RUN_HOST, port=cfg.FLASK_RUN_PORT, debug=cfg.FLASK_DEBUG)
