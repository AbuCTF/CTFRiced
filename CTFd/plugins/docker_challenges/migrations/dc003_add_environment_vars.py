"""Add environment_vars column to docker_challenge table

Revision ID: dc003_add_environment_vars
Revises: dc002_multi_image_support
Create Date: 2025-11-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = 'dc003_add_environment_vars'
down_revision = 'dc002_multi_image_support'
branch_labels = None
depends_on = None


def upgrade(op=None):
    # Add environment_vars column to docker_challenge table
    try:
        op.add_column('docker_challenge', sa.Column('environment_vars', sa.Text(), nullable=True))
        print("Added environment_vars column to docker_challenge table")
    except Exception as e:
        # Column might already exist, that's okay
        print(f"Note: Could not add environment_vars column (may already exist): {e}")


def downgrade(op=None):
    # Remove environment_vars column
    try:
        op.drop_column('docker_challenge', 'environment_vars')
        print("Removed environment_vars column from docker_challenge table")
    except Exception as e:
        print(f"Note: Could not remove environment_vars column: {e}")
