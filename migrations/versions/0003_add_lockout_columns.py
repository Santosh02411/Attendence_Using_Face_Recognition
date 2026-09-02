"""Add brute-force lockout tracking to admins and students.

failed_attempts resets to 0 on a successful login; locked_until (an ISO
timestamp) blocks further attempts until it's in the past — see
config.LOCKOUT_MAX_FAILED_ATTEMPTS / LOCKOUT_DURATION_MINUTES.

Replaces the old:
    for table in ('admins', 'students'):
        cursor.execute(f"PRAGMA table_info({table})")
        ...
        if 'failed_attempts' not in table_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN failed_attempts INTEGER DEFAULT 0")
        if 'locked_until' not in table_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN locked_until TEXT")
conditional block from app.py's init_databases().

Revision ID: 0003_add_lockout_columns
Revises: 0002_add_session_recurrence_columns
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003_add_lockout_columns'
down_revision = '0002_add_session_recurrence_columns'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('admins', 'students'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('failed_attempts', sa.Integer, server_default='0'))
            batch_op.add_column(sa.Column('locked_until', sa.Text))


def downgrade():
    for table in ('admins', 'students'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('locked_until')
            batch_op.drop_column('failed_attempts')
