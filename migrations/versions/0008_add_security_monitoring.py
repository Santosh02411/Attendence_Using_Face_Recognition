"""Add device fingerprinting, login-session tracking, security
notifications, and per-student risk escalation state.

Four related additions for the anti-spoof/security feature set:

- device_fingerprints: one row per (student, device) pairing seen at
  login — a lightweight, self-hosted fingerprint (browser/screen/
  timezone signals hashed client-side; see static/app.js), NOT a
  commercial-grade fingerprinting service. Backs "suspicious-device
  detection" and "IP/device history per student".
- student_login_sessions: one row per login, with a random token stored
  in the Flask session, used only to detect a second concurrently-active
  login for the same student (see _check_concurrent_session() in
  app.py) — not a replacement for Flask's own session mechanism.
- security_notifications: admin-facing alerts for the higher-severity
  security events (escalation, concurrent session, network change) —
  there's no email/SMS in this project (a documented trade-off), so
  this is the in-app "notification" surface, read/unread like an inbox.
- students gains last_login_ip/last_login_at (for network-change
  detection at the next login) and security_escalated/
  security_escalated_at (persisted state set by automatic escalation —
  see _maybe_escalate_student()), independent of the existing
  attendance_locked_until (that's specifically about repeated spoof/
  liveness failures; escalation is a broader, risk-score-driven flag
  that also blocks attendance marking until an admin clears it).

Revision ID: 0008_add_security_monitoring
Revises: 0007_add_face_enrollment_metadata
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0008_add_security_monitoring'
down_revision = '0007_add_face_enrollment_metadata'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('students') as batch_op:
        batch_op.add_column(sa.Column('last_login_ip', sa.Text))
        batch_op.add_column(sa.Column('last_login_at', sa.Text))
        batch_op.add_column(sa.Column('security_escalated', sa.Integer, server_default='0'))
        batch_op.add_column(sa.Column('security_escalated_at', sa.Text))

    op.create_table(
        'device_fingerprints',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False),
        sa.Column('fingerprint_hash', sa.Text, nullable=False),
        sa.Column('user_agent', sa.Text),
        sa.Column('ip_address', sa.Text),
        sa.Column('first_seen', sa.Text, nullable=False),
        sa.Column('last_seen', sa.Text, nullable=False),
        sa.Column('seen_count', sa.Integer, nullable=False, server_default='1'),
        sa.UniqueConstraint('student_id', 'fingerprint_hash', name='uq_device_fingerprints_student_fp'),
    )
    op.create_index('idx_device_fingerprints_student_id', 'device_fingerprints', ['student_id'])

    op.create_table(
        'student_login_sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False),
        sa.Column('session_token', sa.Text, nullable=False, unique=True),
        sa.Column('ip_address', sa.Text),
        sa.Column('fingerprint_hash', sa.Text),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('last_seen_at', sa.Text, nullable=False),
        sa.Column('ended_at', sa.Text),
    )
    op.create_index('idx_login_sessions_student_id', 'student_login_sessions', ['student_id'])

    op.create_table(
        'security_notifications',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('severity', sa.Text, nullable=False),
        sa.Column('student_id', sa.Integer),
        sa.Column('event_type', sa.Text, nullable=False),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('is_read', sa.Integer, nullable=False, server_default='0'),
        sa.Column('resolved_at', sa.Text),
    )
    op.create_index('idx_security_notifications_is_read', 'security_notifications', ['is_read'])


def downgrade():
    op.drop_index('idx_security_notifications_is_read', table_name='security_notifications')
    op.drop_table('security_notifications')

    op.drop_index('idx_login_sessions_student_id', table_name='student_login_sessions')
    op.drop_table('student_login_sessions')

    op.drop_index('idx_device_fingerprints_student_id', table_name='device_fingerprints')
    op.drop_table('device_fingerprints')

    with op.batch_alter_table('students') as batch_op:
        batch_op.drop_column('security_escalated_at')
        batch_op.drop_column('security_escalated')
        batch_op.drop_column('last_login_at')
        batch_op.drop_column('last_login_ip')
