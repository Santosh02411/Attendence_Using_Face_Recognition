"""Add face-enrollment metadata, attendance confidence, and app_settings.

Three related additions for the face-recognition/biometric feature set:

- face_embeddings gains image_filename (links an embedding row back to
  its saved photo on disk, enabling per-photo removal rather than only
  wiping a student's whole face data), quality_score (a 0-100 heuristic
  from the same sharpness/exposure signals as assess_image_quality() —
  see _compute_quality_score() in app.py, used for enrollment status and
  the re-enrollment reminder), and model_version (which embedding model
  produced this vector — see EMBEDDER_MODEL_VERSION in config.py; lets
  an admin see who still has embeddings from a retired model after a
  model change).
- attendance gains confidence (the match similarity score, 0-1, behind
  a Present/Late mark) for recognition-confidence reporting.
- app_settings is a small generic key/value table backing runtime
  threshold overrides from /admin/recognition-settings (see
  get_effective_setting() in app.py) — without it, changing a match
  threshold would require editing the environment and restarting.

Revision ID: 0007_add_face_enrollment_metadata
Revises: 0006_add_session_lifecycle_columns
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0007_add_face_enrollment_metadata'
down_revision = '0006_add_session_lifecycle_columns'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('face_embeddings') as batch_op:
        batch_op.add_column(sa.Column('image_filename', sa.Text))
        batch_op.add_column(sa.Column('quality_score', sa.Float))
        batch_op.add_column(sa.Column('model_version', sa.Text))

    with op.batch_alter_table('attendance') as batch_op:
        batch_op.add_column(sa.Column('confidence', sa.Float))

    op.create_table(
        'app_settings',
        sa.Column('key', sa.Text, primary_key=True),
        sa.Column('value', sa.Text, nullable=False),
        sa.Column('updated_at', sa.Text, nullable=False),
        sa.Column('updated_by', sa.Text),
    )


def downgrade():
    op.drop_table('app_settings')

    with op.batch_alter_table('attendance') as batch_op:
        batch_op.drop_column('confidence')

    with op.batch_alter_table('face_embeddings') as batch_op:
        batch_op.drop_column('model_version')
        batch_op.drop_column('quality_score')
        batch_op.drop_column('image_filename')
