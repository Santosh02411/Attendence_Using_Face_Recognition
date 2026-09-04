"""Optional email (SMTP) and SMS (Twilio REST API) notifications.

Entirely opt-in and safe-by-default, following the same pattern as
error_reporting.py's Sentry integration: every public function here
degrades to a silent no-op (logged at debug level, never raised) when
the relevant feature flag is off or its configuration is incomplete, so
the rest of the app never needs to check "is email/SMS configured?"
before calling these — they're always safe to call.

Two channels:
- Email via smtplib (standard library — no extra dependency), gated by
  config.EMAIL_NOTIFICATIONS_ENABLED + config.SMTP_HOST.
- SMS via Twilio's plain HTTPS REST API using `requests` (already a
  transitive dependency), gated by config.SMS_NOTIFICATIONS_ENABLED +
  the three TWILIO_* settings. The twilio SDK itself is intentionally
  NOT a dependency — a single REST call is all this needs.

Neither channel is a hard requirement for the rest of the app to run:
an unconfigured deployment behaves exactly as it did before this module
existed (in-app banners/dashboard widgets only), which is why both are
off by default.
"""
import logging
import smtplib
from email.message import EmailMessage

import config as cfg

logger = logging.getLogger('attendance_app')


def email_enabled():
    """True only if email notifications are turned on AND an SMTP host
    is actually configured — mirrors error_reporting.py's
    "DSN unset -> disabled" check."""
    return cfg.EMAIL_NOTIFICATIONS_ENABLED and bool(cfg.SMTP_HOST)


def sms_enabled():
    """True only if SMS notifications are turned on AND all three
    Twilio settings are present."""
    return (
        cfg.SMS_NOTIFICATIONS_ENABLED
        and bool(cfg.TWILIO_ACCOUNT_SID)
        and bool(cfg.TWILIO_AUTH_TOKEN)
        and bool(cfg.TWILIO_FROM_NUMBER)
    )


def send_email(to_address, subject, body):
    """Sends a plain-text email. No-op (returns False, never raises) if
    email isn't configured/enabled, or if to_address is falsy. Returns
    True only on an actual successful send — callers that just want
    "fire and forget" can ignore the return value."""
    if not to_address:
        return False
    if not email_enabled():
        logger.debug('Email notification suppressed (not configured/enabled): %s', subject)
        return False

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f'{cfg.SMTP_FROM_NAME} <{cfg.SMTP_FROM_EMAIL}>'
    msg['To'] = to_address
    msg.set_content(body)

    try:
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=cfg.SMTP_TIMEOUT_SECONDS) as server:
            if cfg.SMTP_USE_TLS:
                server.starttls()
            if cfg.SMTP_USERNAME:
                server.login(cfg.SMTP_USERNAME, cfg.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        # A notification failure should never break the request that
        # triggered it (an attendance mark, a password-reset request,
        # etc.) — log and swallow, same reasoning as
        # error_reporting.capture_exception() never raising.
        logger.warning('Failed to send email notification to %s: %s', to_address, e)
        return False


def send_sms(to_number, body):
    """Sends an SMS via Twilio's REST API. No-op (returns False, never
    raises) if SMS isn't configured/enabled, or if to_number is falsy."""
    if not to_number:
        return False
    if not sms_enabled():
        logger.debug('SMS notification suppressed (not configured/enabled)')
        return False

    try:
        import requests
    except ImportError:
        logger.warning('SMS_NOTIFICATIONS_ENABLED is set but the requests package is not installed.')
        return False

    url = f'https://api.twilio.com/2010-04-01/Accounts/{cfg.TWILIO_ACCOUNT_SID}/Messages.json'
    try:
        response = requests.post(
            url,
            data={'From': cfg.TWILIO_FROM_NUMBER, 'To': to_number, 'Body': body},
            auth=(cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN),
            timeout=cfg.SMTP_TIMEOUT_SECONDS,
        )
        if response.status_code >= 300:
            logger.warning('Twilio SMS send failed (%s): %s', response.status_code, response.text[:300])
            return False
        return True
    except Exception as e:
        logger.warning('Failed to send SMS notification to %s: %s', to_number, e)
        return False


def notify_attendance_marked(student, session_row, status, subject_name=None):
    """Notifies a student that their attendance was just recorded.
    Respects the student's own notify_email/notify_sms columns (set on
    /student/profile) on top of the global NOTIFY_ON_ATTENDANCE_MARK
    switch — either can suppress it independently. Safe no-op if the
    student has no email/phone on file, or if neither channel is
    configured."""
    if not cfg.NOTIFY_ON_ATTENDANCE_MARK:
        return
    subject_label = subject_name or 'your session'
    body = (
        f"Hi {student['name']},\n\n"
        f"Your attendance for {subject_label} was just marked as {status}.\n\n"
        f"If this wasn't you, please contact your administrator immediately.\n\n"
        f"— {cfg.SMTP_FROM_NAME}"
    )
    if student['notify_email'] and student['email']:
        send_email(student['email'], f'Attendance marked: {status}', body)
    if student['notify_sms'] and student['phone_number']:
        send_sms(student['phone_number'], f'Attendance marked {status} for {subject_label}.')


def notify_low_attendance(student, percentage, threshold):
    """Notifies a student that their overall attendance has dropped
    below the configured threshold. Callers are responsible for their
    own cooldown/rate-limiting (see students.last_low_attendance_alert_at
    and _maybe_notify_low_attendance() in app.py) — this function itself
    sends unconditionally whenever called."""
    if not cfg.NOTIFY_ON_LOW_ATTENDANCE:
        return
    body = (
        f"Hi {student['name']},\n\n"
        f"Your attendance is currently {percentage}%, which is below the "
        f"{threshold}% threshold. If you think this is incorrect, you can "
        f"file a correction request from your attendance history page.\n\n"
        f"— {cfg.SMTP_FROM_NAME}"
    )
    if student['notify_email'] and student['email']:
        send_email(student['email'], 'Low attendance alert', body)
    if student['notify_sms'] and student['phone_number']:
        send_sms(student['phone_number'], f'Low attendance alert: {percentage}% (threshold {threshold}%).')


def send_password_reset_email(student, reset_url):
    """Emails a password-reset link to a student. Returns True/False —
    see send_email()'s docstring. SMS is deliberately not offered for
    password resets (a link isn't practical over SMS)."""
    if not student['email']:
        return False
    body = (
        f"Hi {student['name']},\n\n"
        f"We received a request to reset your password. Click the link "
        f"below to choose a new one — this link expires in "
        f"{cfg.PASSWORD_RESET_TOKEN_TTL_MINUTES} minutes and can only be "
        f"used once:\n\n{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email — "
        f"your password will not be changed.\n\n"
        f"— {cfg.SMTP_FROM_NAME}"
    )
    return send_email(student['email'], 'Reset your password', body)
