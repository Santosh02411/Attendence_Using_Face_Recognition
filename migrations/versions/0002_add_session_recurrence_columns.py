"""Add end_date/end_time/is_recurring to sessions.

Replaces the old:
    cursor.execute("PRAGMA table_info(sessions)")
    ...
    if 'end_date' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN end_date TEXT")
    ...
conditional block from app.py's init_databases().

Revision ID: 0002_add_session_recurrence_columns
Revises: 0001_initial_schema
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002_add_session_recurrence_columns'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.add_column(sa.Column('end_date', sa.Text))
        batch_op.add_column(sa.Column('end_time', sa.Text))
        batch_op.add_column(sa.Column('is_recurring', sa.Integer, server_default='0'))


def downgrade():
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.drop_column('is_recurring')
        batch_op.drop_column('end_time')
        batch_op.drop_column('end_date')
