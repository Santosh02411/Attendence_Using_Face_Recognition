"""Structured (JSON) logging for the app, plus a gunicorn Logger subclass
that reformats gunicorn's own access/error logs the same way.

Design:
  - Every log record emitted anywhere in the process (this app's own
    logger, Flask's/werkzeug's request logging, gunicorn's access/error
    logs when running under gunicorn) goes through JsonFormatter, so a
    log shipper downstream sees one consistent shape everywhere instead
    of a mix of plain-text lines.
  - A per-request request_id (see app.py's before_request/after_request
    hooks) is threaded through via RequestIdFilter, which reads it from
    Flask's `g` when a request context is active and attaches it to
    every LogRecord's `request_id` attribute — so log lines emitted deep
    inside a request (e.g. from recognize_face()) automatically carry
    the same id as the access-log line for that request, without every
    call site having to pass it explicitly.

Deliberately does NOT log request/response bodies anywhere (they can
contain face-image base64 payloads and, on some legacy paths, plaintext
passwords) — only method/path/status/timing/identity fields. See
app.py's after_request hook for the one place a full request "access log"
line is built.
"""
import json
import logging
import sys

try:
    from flask import g, has_request_context
except ImportError:  # pragma: no cover - flask is a hard dependency of the
    # app itself, but logging_config.py is also imported by gunicorn.conf.py
    # before gunicorn's app-loading has necessarily made Flask importable
    # in every possible invocation; degrade to "no request id" rather than
    # crash the log pipeline over an import ordering quirk.
    g = None  # type: ignore[assignment]

    def has_request_context() -> bool:
        return False


# Standard LogRecord attributes, so JsonFormatter can tell "the caller
# passed logger.info(..., extra={'foo': 'bar'})" apart from attributes
# every LogRecord already has — anything not in this set gets folded into
# the JSON output as its own field.
_STANDARD_RECORD_ATTRS = set(logging.LogRecord('', 0, '', 0, '', (), None).__dict__.keys()) | {'message'}


class RequestIdFilter(logging.Filter):
    """Attaches the current request's id (see app.py) to every LogRecord
    that passes through a handler this filter is installed on, so
    JsonFormatter can include it without every call site passing it."""

    def filter(self, record):
        request_id = None
        if has_request_context():
            request_id = getattr(g, 'request_id', None)
        record.request_id = request_id
        return True


class JsonFormatter(logging.Formatter):
    """Renders one LogRecord as one JSON object per line (newline-delimited
    JSON — the format most log shippers/aggregators expect)."""

    def format(self, record):
        payload = {
            'timestamp': self.formatTime(record, '%Y-%m-%dT%H:%M:%S') + f'.{int(record.msecs):03d}Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        request_id = getattr(record, 'request_id', None)
        if request_id:
            payload['request_id'] = request_id
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        # Anything passed via logger.info(..., extra={...}) that isn't a
        # built-in LogRecord attribute gets surfaced as its own JSON field
        # (e.g. the after_request access-log call passes method/path/
        # status/duration_ms this way).
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key == 'request_id':
                continue
            try:
                json.dumps(value)  # skip anything that isn't JSON-serializable
                payload[key] = value
            except TypeError:
                payload[key] = str(value)
        return json.dumps(payload, default=str)


def _resolve_level(level_name):
    return getattr(logging, str(level_name).upper(), logging.INFO)


def configure_app_logging(level=None, fmt=None):
    """Configures the root logger (and therefore every logger that
    propagates to it — this app's own, 'werkzeug', and, unless gunicorn's
    own JsonGunicornLogger below is in use, gunicorn's) to emit
    newline-delimited JSON to stdout.

    Safe to call more than once (e.g. once from app.py at import time, and
    again if a test or another entrypoint imports app.py fresh) — it
    clears any handlers it previously installed first rather than
    stacking duplicate handlers/duplicate log lines.

    level: an env var name is NOT expected here — pass the resolved
    value (defaults to config.LOG_LEVEL if omitted and config is
    importable, else 'INFO').
    fmt: 'json' (default) or 'plain' — 'plain' is for a nicer-to-read
    local dev console; production should stay on 'json'.
    """
    if level is None:
        try:
            import config as cfg
            level = cfg.LOG_LEVEL
        except Exception:
            level = 'INFO'
    if fmt is None:
        try:
            import config as cfg
            fmt = cfg.LOG_FORMAT
        except Exception:
            fmt = 'json'

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, '_attendance_app_handler', False):
            root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler._attendance_app_handler = True  # type: ignore[attr-defined]  # marks this handler as ours, for the dedup check above
    handler.addFilter(RequestIdFilter())
    if fmt == 'plain':
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-8s [%(name)s] %(message)s%(request_id_suffix)s'
        ))
        # logging.Formatter has no built-in "only if present" field, so
        # inject a plain-text filter that computes the suffix string.
        handler.addFilter(_PlainRequestIdSuffixFilter())
    else:
        handler.setFormatter(JsonFormatter())

    root_logger.addHandler(handler)
    root_logger.setLevel(_resolve_level(level))

    # werkzeug's own per-request log line ("127.0.0.1 - - [date] "GET / HTTP/1.1" 200 -")
    # propagates to root by default, so it picks up the same
    # formatter/handler automatically — nothing extra needed here beyond
    # making sure it doesn't have its own separate handler fighting ours.
    logging.getLogger('werkzeug').handlers = []
    logging.getLogger('werkzeug').propagate = True


class _PlainRequestIdSuffixFilter(logging.Filter):
    def filter(self, record):
        request_id = None
        if has_request_context():
            request_id = getattr(g, 'request_id', None)
        record.request_id_suffix = f' request_id={request_id}' if request_id else ''
        return True


def get_request_id():
    """Returns the current request's id, or None outside a request
    context. Exposed for call sites (e.g. error handlers) that want to
    include it in a JSON response body, not just in logs."""
    if has_request_context():
        return getattr(g, 'request_id', None)
    return None


# --- Optional: gunicorn access/error log reformatting ---------------------
# Only imported by gunicorn itself (via gunicorn.conf.py's `logger_class`
# setting) when running under gunicorn — never imported by the app/tests,
# so `gunicorn` need not be installed for `import logging_config` to work
# (requirements.txt only installs gunicorn via requirements-prod.txt).
try:
    from gunicorn.glogging import Logger as _GunicornLogger

    class JsonGunicornLogger(_GunicornLogger):
        """Drop-in replacement for gunicorn's default Logger that emits
        its access and error logs as JSON via JsonFormatter, instead of
        gunicorn's normal Apache-combined-style access log text. The
        request_id output token in gunicorn.conf.py's access_log_format
        (`%({x-request-id}o)s`) correlates a gunicorn access-log line
        with this app's own per-request JSON log line for the same
        request (see app.py's after_request hook) — both end up tagged
        with the same id, one generated by the app and echoed back as a
        response header, the other read from that header by gunicorn.
        """

        def setup(self, cfg):
            super().setup(cfg)
            for log in (self.error_log, self.access_log):
                for h in log.handlers:
                    h.setFormatter(JsonFormatter())

except ImportError:  # pragma: no cover - gunicorn isn't installed outside
    # requirements-prod.txt (e.g. in the default dev/test environment);
    # nothing in this module needs JsonGunicornLogger to exist there.
    pass
