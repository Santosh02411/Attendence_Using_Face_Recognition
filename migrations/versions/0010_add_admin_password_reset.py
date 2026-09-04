"""Add admins.recovery_email and an admin_password_reset_tokens table.

admins.recovery_email is deliberately NOT settable through any HTTP
route in this application — see set_admin_recovery_email.py, a CLI
script (same shape as reset_admin_password.py) that is the only way to
set or change it. That's intentional: it keeps the address a reset link
goes to outside the reach of any web-app-level compromise (a stolen
admin session, a CSRF gap, an XSS bug) — the same reasoning
reset_admin_password.py itself already documents for why admin
password recovery stays a server-access-only operation.

admin_password_reset_tokens mirrors password_reset_tokens (see
0009_add_notifications_and_sso.py) but is a separate table rather than
a shared one with a nullable admin_id/student_id pair, so the two flows
(and their very different trust models — see config.py's
ADMIN_PASSWORD_RESET_ENABLED comment) can never accidentally cross.

Revision ID: 0010_add_admin_password_reset
Revises: 0009_add_notifications_and_sso
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0010_add_admin_password_reset'
down_revision = '0009_add_notifications_and_sso'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('admins') as batch_op:
        batch_op.add_column(sa.Column('recovery_email', sa.Text))

    op.create_table(
        'admin_password_reset_tokens',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('admin_id', sa.Integer, sa.ForeignKey('admins.id'), nullable=False),
        sa.Column('token_hash', sa.Text, nullable=False),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('expires_at', sa.Text, nullable=False),
        sa.Column('used_at', sa.Text),
        sa.Column('requested_ip', sa.Text),
    )
    op.create_index('idx_admin_password_reset_tokens_admin_id', 'admin_password_reset_tokens', ['admin_id'])
    op.create_index('idx_admin_password_reset_tokens_token_hash', 'admin_password_reset_tokens', ['token_hash'])


def downgrade():
    op.drop_index('idx_admin_password_reset_tokens_token_hash', table_name='admin_password_reset_tokens')
    op.drop_index('idx_admin_password_reset_tokens_admin_id', table_name='admin_password_reset_tokens')
    op.drop_table('admin_password_reset_tokens')

    with op.batch_alter_table('admins') as batch_op:
        batch_op.drop_column('recovery_email')
