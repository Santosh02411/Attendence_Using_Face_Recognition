"""Add ml_model_state — storage for the trained logistic-regression
model in ml_predictions.py. Single-row table (id is always 1): each
training run replaces the previous model outright rather than
versioning history, since there's exactly one "current" model this
deployment uses at a time and no rollback/A-B mechanism today.

See ml_predictions.py's module docstring before assuming a row here
means a validated, production-grade predictor — the stored JSON
includes whatever accuracy/precision/recall metrics that training run
produced (or null if the sample was too small for a held-out test
set), and the admin page is expected to show those numbers plainly
rather than just a "model trained" checkmark.

Revision ID: 0013_add_ml_model_state
Revises: 0012_add_iris_templates
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0013_add_ml_model_state'
down_revision = '0012_add_iris_templates'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ml_model_state',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('model_json', sa.Text, nullable=False),
        sa.Column('trained_at', sa.Text, nullable=False),
        sa.Column('trained_by', sa.Text),
    )


def downgrade():
    op.drop_table('ml_model_state')
