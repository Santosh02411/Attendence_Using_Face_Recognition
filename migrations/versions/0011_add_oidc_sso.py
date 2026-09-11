"""Add students.oauth_oidc_sub for generic-OIDC SSO (Okta, Azure AD,
Keycloak, Auth0, etc.), alongside the existing oauth_google_sub from
0009_add_notifications_and_sso.py. A separate column rather than a
provider+sub pair on a shared column, matching that migration's own
reasoning: keeps each provider's link independent and avoids ever
mixing up which provider a given subject id came from.

Revision ID: 0011_add_oidc_sso
Revises: 0010_add_admin_password_reset
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0011_add_oidc_sso'
down_revision = '0010_add_admin_password_reset'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('students') as batch_op:
        batch_op.add_column(sa.Column('oauth_oidc_sub', sa.Text))
    op.create_index('idx_students_oauth_oidc_sub', 'students', ['oauth_oidc_sub'])


def downgrade():
    op.drop_index('idx_students_oauth_oidc_sub', table_name='students')
    with op.batch_alter_table('students') as batch_op:
        batch_op.drop_column('oauth_oidc_sub')
