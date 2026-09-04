"""Add student contact info + notification prefs, password-reset tokens,
and Google OAuth linkage.

Three related additions:

- students gains email, phone_number (both nullable/optional — an
  existing account has neither until the student fills them in on
  /student/profile), notify_email/notify_sms (default on, so a student
  who adds an email/phone starts receiving notifications without an
  extra opt-in step), last_low_attendance_alert_at (cooldown tracking so
  a persistently-low-attendance student isn't emailed on every single
  mark — see LOW_ATTENDANCE_ALERT_COOLDOWN_HOURS in config.py), and
  oauth_google_sub (the stable Google account identifier once a student
  has signed in via Google at least once — see configure_oauth() and
  /auth/google/callback in app.py).
- password_reset_tokens: one row per issued reset link. Stores a
  SHA-256 hash of the token (never the raw token itself) plus an
  expiry and a used_at marker, so a token is single-use and
  time-limited — see request_student_password_reset() /
  reset_student_password() in app.py.

Revision ID: 0009_add_notifications_and_sso
Revises: 0008_add_security_monitoring
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0009_add_notifications_and_sso'
down_revision = '0008_add_security_monitoring'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('students') as batch_op:
        batch_op.add_column(sa.Column('email', sa.Text))
        batch_op.add_column(sa.Column('phone_number', sa.Text))
        batch_op.add_column(sa.Column('notify_email', sa.Integer, server_default='1'))
        batch_op.add_column(sa.Column('notify_sms', sa.Integer, server_default='1'))
        batch_op.add_column(sa.Column('last_low_attendance_alert_at', sa.Text))
        batch_op.add_column(sa.Column('oauth_google_sub', sa.Text))

    # Non-unique index: several students could plausibly go without an
    # email (NULL), and SQLite's own uniqueness handling for NULLs in a
    # UNIQUE index would allow duplicate NULLs anyway, so a plain index
    # for lookup speed is what actually matters here — email-match
    # collisions for OAuth/password-reset are checked explicitly in
    # app.py rather than relied on at the schema level.
    op.create_index('idx_students_email', 'students', ['email'])
    op.create_index('idx_students_oauth_google_sub', 'students', ['oauth_google_sub'])

    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False),
        sa.Column('token_hash', sa.Text, nullable=False),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('expires_at', sa.Text, nullable=False),
        sa.Column('used_at', sa.Text),
        sa.Column('requested_ip', sa.Text),
    )
    op.create_index('idx_password_reset_tokens_student_id', 'password_reset_tokens', ['student_id'])
    op.create_index('idx_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'])


def downgrade():
    op.drop_index('idx_password_reset_tokens_token_hash', table_name='password_reset_tokens')
    op.drop_index('idx_password_reset_tokens_student_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')

    op.drop_index('idx_students_oauth_google_sub', table_name='students')
    op.drop_index('idx_students_email', table_name='students')

    with op.batch_alter_table('students') as batch_op:
        batch_op.drop_column('oauth_google_sub')
        batch_op.drop_column('last_low_attendance_alert_at')
        batch_op.drop_column('notify_sms')
        batch_op.drop_column('notify_email')
        batch_op.drop_column('phone_number')
        batch_op.drop_column('email')
