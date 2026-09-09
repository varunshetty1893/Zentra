"""add interview detail and rejection reason to applications

Revision ID: a7c9e2f4b1d3
Revises: f1a2b3c4d5e6
Create Date: 2026-09-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c9e2f4b1d3'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('interview_type', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('interviewer_name', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('meeting_link', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('interview_completed', sa.Boolean(), nullable=False, server_default='false'))
        batch_op.add_column(sa.Column('rejection_reason', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_column('rejection_reason')
        batch_op.drop_column('interview_completed')
        batch_op.drop_column('meeting_link')
        batch_op.drop_column('interviewer_name')
        batch_op.drop_column('interview_type')
