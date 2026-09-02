"""Add attendance-marking abuse lockout tracking to students.

Separate from the login lockout added in 0003: that one protects the
*login* form from password brute-forcing. This one protects the
*attendance-marking* endpoint from someone retrying a presentation-attack
(spoofed photo/screen) or failing the active-liveness challenge over and
over — previously each attempt was only ever written to the audit log,
with nothing to stop indefinite retries or flag the account for review.

attendance_security_failures counts consecutive
attendance_spoof_suspected / attendance_liveness_challenge_failed events
for a student; it resets to 0 on a successful attendance mark.
attendance_locked_until (an ISO timestamp) blocks further attendance
attempts once the count crosses
config.ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS, until it's in the past —
see config.ATTENDANCE_LOCKOUT_DURATION_MINUTES.

Revision ID: 0004_add_attendance_lockout_columns
Revises: 0003_add_lockout_columns
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004_add_attendance_lockout_columns'
down_revision = '0003_add_lockout_columns'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('students') as batch_op:
        batch_op.add_column(sa.Column('attendance_security_failures', sa.Integer, server_default='0'))
        batch_op.add_column(sa.Column('attendance_locked_until', sa.Text))


def downgrade():
    with op.batch_alter_table('students') as batch_op:
        batch_op.drop_column('attendance_locked_until')
        batch_op.drop_column('attendance_security_failures')
