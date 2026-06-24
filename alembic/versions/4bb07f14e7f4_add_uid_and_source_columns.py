"""Add uid and source columns

Revision ID: 4bb07f14e7f4
Revises: 3a45b3d1ba9a
Create Date: 2026-06-24 19:01:11.429294

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4bb07f14e7f4'
down_revision = '3a45b3d1ba9a'
branch_labels = None
depends_on = None


def upgrade():
    # feed
    op.add_column('feed', sa.Column('source', sa.String(), server_default='hacker_news', nullable=False))
    op.drop_index('feed_id_index', table_name='feed')
    op.create_index('feed_id_index', 'feed', ['id', 'source'])

    # post
    op.add_column('post', sa.Column('uid', sa.String(), nullable=True))
    op.add_column('post', sa.Column('source', sa.String(), server_default='hacker_news', nullable=False))
    op.drop_index('post_index', table_name='post')
    op.create_index('post_index', 'post', ['id', 'uid', 'source', 'username'])

    # comment
    op.add_column('comment', sa.Column('uid', sa.String(), nullable=True))
    op.drop_index('comment_index', table_name='comment')
    op.create_index('comment_index', 'comment', ['id', 'uid', 'level', 'parent_comment', 'post_id', 'total_word_count', 'username'])


def downgrade():
    # comment
    op.drop_index('comment_index', table_name='comment')
    op.create_index('comment_index', 'comment', ['id', 'level', 'parent_comment', 'post_id', 'total_word_count', 'username'])
    op.drop_column('comment', 'uid')

    # post
    op.drop_index('post_index', table_name='post')
    op.create_index('post_index', 'post', ['id', 'username'])
    op.drop_column('post', 'source')
    op.drop_column('post', 'uid')

    # feed
    op.drop_index('feed_id_index', table_name='feed')
    op.create_index('feed_id_index', 'feed', ['id'])
    op.drop_column('feed', 'source')
