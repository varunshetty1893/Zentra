"""add interview_date to applications

Revision ID: f1a2b3c4d5e6
Revises: 282a0255237c
Create Date: 2026-09-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = '282a0255237c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('interview_date', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_column('interview_date')
