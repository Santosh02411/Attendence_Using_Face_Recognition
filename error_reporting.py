"""Optional error alerting / crash reporting via Sentry.

Entirely opt-in and safe-by-default: nothing is sent anywhere, and no
network call is ever made, unless config.SENTRY_DSN is set. Every public
function here degrades to a silent no-op when Sentry isn't configured
(DSN unset) or isn't installed (sentry-sdk not in the environment), so the
rest of the app never needs to check "is Sentry on?" before calling these
— init_sentry()/capture_exception() are always safe to call.

sentry-sdk is intentionally NOT a hard requirement.txt dependency for the
same reason opencv/Flask are hard dependencies but this isn't: most people
running this app locally or in a class project have no Sentry account and
shouldn't need one to `pip install -r requirements.txt` and run the app.
It's listed in requirements-prod.txt instead — see that file's comment —
so it's there for anyone doing a real deployment while staying opt-in for
everyone else.
"""
import logging

import config as cfg
import logging_config

logger = logging.getLogger('attendance_app')

_initialized = False


def init_sentry(app=None):
    """Initializes the Sentry SDK if config.SENTRY_DSN is set. Safe to
    call unconditionally at app startup (from app.py) regardless of
    whether Sentry is actually configured for this deployment — it's a
    no-op otherwise. Call once; a second call is also a no-op (guarded by
    _initialized) so importing app.py more than once in the same process
    (as the test suite does) doesn't re-initialize/double-register the
    Flask integration.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    if not cfg.SENTRY_DSN:
        logger.info('SENTRY_DSN not set — error alerting is disabled.')
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        logger.warning(
            'SENTRY_DSN is set but the sentry-sdk package is not installed. '
            'Install it (see requirements-prod.txt) to enable error alerting. '
            'Continuing without it.'
        )
        return

    sentry_sdk.init(
        dsn=cfg.SENTRY_DSN,
        environment=cfg.SENTRY_ENVIRONMENT,
        integrations=[FlaskIntegration()],
        traces_sample_rate=cfg.SENTRY_TRACES_SAMPLE_RATE,
        # Request bodies can contain face-image base64 payloads and, on
        # some legacy paths, plaintext passwords — never send them to
        # Sentry. This matches logging_config.py's same rule for the
        # app's own logs.
        send_default_pii=False,
        max_request_body_size='never',
    )
    logger.info(f'Sentry error alerting enabled (environment={cfg.SENTRY_ENVIRONMENT}).')


def capture_exception(exc):
    """Reports exc to Sentry if it's configured, tagged with the current
    request's id (see logging_config.get_request_id()) so a Sentry event
    can be cross-referenced with the matching JSON log line for the same
    request. No-op (and never raises) if Sentry isn't configured/
    installed — callers don't need to check first."""
    if not cfg.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        return

    request_id = logging_config.get_request_id()
    if request_id:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag('request_id', request_id)
            sentry_sdk.capture_exception(exc)
    else:
        sentry_sdk.capture_exception(exc)


def capture_message(message, level='info'):
    """Reports a message (not an exception) to Sentry if configured —
    e.g. for backup.py to alert on a failed automated backup without an
    in-process exception to attach it to. No-op if Sentry isn't
    configured/installed."""
    if not cfg.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.capture_message(message, level=level)
