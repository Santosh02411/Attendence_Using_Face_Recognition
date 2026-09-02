"""Initial schema.

Baseline tables for database/app.db, matching what the old hand-rolled
`CREATE TABLE IF NOT EXISTS` block in app.py's init_databases() created
before this migration chain existed. Two schema changes that block used to
apply via conditional `ALTER TABLE` (sessions' recurrence columns, and the
admins/students lockout columns) are their own follow-on revisions below,
so this history mirrors how the schema actually evolved.

Note: database/FaceBase.db (the legacy `people` table) is intentionally
NOT managed here — it has no ALTER TABLE history and app.py still creates
it directly; see db_migrations.py.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'admins',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('username', sa.Text, unique=True),
        sa.Column('password', sa.Text),
    )
    op.create_table(
        'students',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.Text),
        sa.Column('roll_no', sa.Text),
        sa.Column('branch', sa.Text),
        sa.Column('semester', sa.Text),
        sa.Column('password', sa.Text),
        sa.Column('photo_count', sa.Integer, server_default='0'),
    )
    op.create_table(
        'subjects',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.Text),
        sa.Column('code', sa.Text),
    )
    op.create_table(
        'sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('subject_id', sa.Integer, sa.ForeignKey('subjects.id')),
        sa.Column('title', sa.Text),
        sa.Column('date', sa.Text),
        sa.Column('time', sa.Text),
        sa.Column('active', sa.Integer, server_default='0'),
    )
    op.create_table(
        'attendance',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id')),
        sa.Column('session_id', sa.Integer, sa.ForeignKey('sessions.id')),
        sa.Column('status', sa.Text),
        sa.Column('timestamp', sa.Text),
        sa.Column('note', sa.Text),
    )
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('timestamp', sa.Text, nullable=False),
        sa.Column('actor_type', sa.Text, nullable=False),
        sa.Column('actor_name', sa.Text),
        sa.Column('action', sa.Text, nullable=False),
        sa.Column('target', sa.Text),
        sa.Column('details', sa.Text),
        sa.Column('ip_address', sa.Text),
    )
    op.create_index('idx_audit_log_timestamp', 'audit_log', ['timestamp'])

    op.create_table(
        'face_embeddings',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False),
        sa.Column('embedding', sa.LargeBinary, nullable=False),
        sa.Column('created_at', sa.Text),
    )
    op.create_index('idx_face_embeddings_student_id', 'face_embeddings', ['student_id'])


def downgrade():
    op.drop_index('idx_face_embeddings_student_id', table_name='face_embeddings')
    op.drop_table('face_embeddings')
    op.drop_index('idx_audit_log_timestamp', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('attendance')
    op.drop_table('sessions')
    op.drop_table('subjects')
    op.drop_table('students')
    op.drop_table('admins')
