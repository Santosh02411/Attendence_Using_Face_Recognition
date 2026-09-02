"""Add attendance_correction_requests.

Backs the "student requests a correction -> admin approves/rejects"
workflow: a student who believes their attendance for a session is
wrong (marked absent when they were there, marked present in error,
etc.) can file a request instead of it just being their word against
the log. See app.py's request_attendance_correction() /
resolve_attendance_correction().

Deliberately its own table rather than new columns on `attendance`:
a request can exist for a session where the student has NO attendance
row at all yet (e.g. they were never marked present), and the request/
approval history (who asked, why, who resolved it, when) is a
different shape of data than the attendance record itself.

Revision ID: 0005_add_attendance_correction_requests
Revises: 0004_add_attendance_lockout_columns
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0005_add_attendance_correction_requests'
down_revision = '0004_add_attendance_lockout_columns'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'attendance_correction_requests',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False),
        sa.Column('session_id', sa.Integer, sa.ForeignKey('sessions.id'), nullable=False),
        # 'Present' / 'Absent' / 'Late' — what the student says it should be.
        sa.Column('requested_status', sa.Text, nullable=False),
        sa.Column('reason', sa.Text),
        # 'pending' / 'approved' / 'rejected'.
        sa.Column('status', sa.Text, nullable=False, server_default='pending'),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('resolved_at', sa.Text),
        sa.Column('resolved_by', sa.Text),
        sa.Column('admin_note', sa.Text),
    )
    op.create_index('idx_correction_requests_student_id', 'attendance_correction_requests', ['student_id'])
    op.create_index('idx_correction_requests_session_id', 'attendance_correction_requests', ['session_id'])
    op.create_index('idx_correction_requests_status', 'attendance_correction_requests', ['status'])


def downgrade():
    op.drop_index('idx_correction_requests_status', table_name='attendance_correction_requests')
    op.drop_index('idx_correction_requests_session_id', table_name='attendance_correction_requests')
    op.drop_index('idx_correction_requests_student_id', table_name='attendance_correction_requests')
    op.drop_table('attendance_correction_requests')
