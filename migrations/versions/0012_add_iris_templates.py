"""Add iris_templates — storage for the biometric-scaffolding interface
in biometric.py. See that module's docstring before assuming this means
working iris authentication: there is no real capture hardware or
vendor SDK behind this yet, only the storage shape a future real
integration would use, plus a non-biometric mock provider for
exercising the plumbing in tests and the admin diagnostic page.

One row per student (a student re-enrolling replaces their row rather
than accumulating history — unlike face photos, there's no "average
quality across several samples" concept here, since there's exactly
one synthetic template per (student, provider) pair in the mock
implementation).

Revision ID: 0012_add_iris_templates
Revises: 0011_add_oidc_sso
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0012_add_iris_templates'
down_revision = '0011_add_oidc_sso'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'iris_templates',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('student_id', sa.Integer, sa.ForeignKey('students.id'), nullable=False, unique=True),
        sa.Column('template', sa.LargeBinary, nullable=False),
        sa.Column('provider_name', sa.Text, nullable=False),
        sa.Column('enrolled_at', sa.Text, nullable=False),
    )
    op.create_index('idx_iris_templates_student_id', 'iris_templates', ['student_id'])


def downgrade():
    op.drop_index('idx_iris_templates_student_id', table_name='iris_templates')
    op.drop_table('iris_templates')
