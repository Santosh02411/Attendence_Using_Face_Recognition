"""Add session lifecycle, cancellation, and per-session overrides.

Extends `sessions` for several related features that all live on the
session row itself:

- status: an explicit lifecycle ('scheduled' -> 'active' -> 'completed',
  or -> 'cancelled') alongside the existing `active` boolean, which is
  kept as-is and stays the source of truth for "can attendance be
  marked right now" (see student_mark_attendance()) so nothing that
  reads `active` needs to change. `status` adds the richer states
  `active` alone can't express: distinguishing a session that hasn't
  started yet from one that's finished, and cancellation as a distinct,
  explicitly-blocked state rather than just another kind of "inactive".
- cancelled_at / cancellation_reason: set by cancel_session().
- attendance_window_minutes / grace_period_minutes: optional per-session
  override of the global LATE_ENTRY_* window (see
  _session_attendance_window_minutes()/_session_grace_period_minutes()
  in app.py) — NULL means "use the global config", so existing sessions
  are unaffected.
- allowed_networks: optional per-session override of
  ATTENDANCE_ALLOWED_NETWORKS (see is_ip_allowed_for_attendance()).
- restrict_branch / restrict_semester: optional "student-group
  assignment" — if set, only students in that branch/semester can mark
  attendance for the session (see _session_group_restriction_error()).
  NULL/NULL (the default) is open to everyone, matching every session
  created before this feature existed.

Revision ID: 0006_add_session_lifecycle_columns
Revises: 0005_add_attendance_correction_requests
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0006_add_session_lifecycle_columns'
down_revision = '0005_add_attendance_correction_requests'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.add_column(sa.Column('status', sa.Text, nullable=False, server_default='scheduled'))
        batch_op.add_column(sa.Column('cancelled_at', sa.Text))
        batch_op.add_column(sa.Column('cancellation_reason', sa.Text))
        batch_op.add_column(sa.Column('attendance_window_minutes', sa.Integer))
        batch_op.add_column(sa.Column('grace_period_minutes', sa.Integer))
        batch_op.add_column(sa.Column('allowed_networks', sa.Text))
        batch_op.add_column(sa.Column('restrict_branch', sa.Text))
        batch_op.add_column(sa.Column('restrict_semester', sa.Text))

    # Backfill status for any session that already exists so it reflects
    # the same state `active` currently encodes, rather than every
    # pre-existing session defaulting to 'scheduled' regardless of its
    # actual active flag.
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE sessions SET status='active' WHERE active=1"))


def downgrade():
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.drop_column('restrict_semester')
        batch_op.drop_column('restrict_branch')
        batch_op.drop_column('allowed_networks')
        batch_op.drop_column('grace_period_minutes')
        batch_op.drop_column('attendance_window_minutes')
        batch_op.drop_column('cancellation_reason')
        batch_op.drop_column('cancelled_at')
        batch_op.drop_column('status')
