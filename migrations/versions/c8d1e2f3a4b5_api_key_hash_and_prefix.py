"""API keys: store SHA-256 hash, add key_prefix

Revision ID: c8d1e2f3a4b5
Revises: b7e3c9a2d5f1
Create Date: 2026-09-29

现有明文 key 会被就地哈希；第三方客户端继续发送原来的明文即可，
但管理端此后只能看到前缀，无法再取回明文。
"""
import hashlib

from alembic import op
import sqlalchemy as sa


revision = 'c8d1e2f3a4b5'
down_revision = 'b7e3c9a2d5f1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('api_keys', sa.Column('key_prefix', sa.String(length=16), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, key FROM api_keys")).fetchall()
    for row in rows:
        raw = row[1] or ''
        conn.execute(
            sa.text("UPDATE api_keys SET key = :hashed, key_prefix = :prefix WHERE id = :id"),
            {
                'hashed': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                'prefix': raw[:8],
                'id': row[0],
            },
        )


def downgrade():
    # 哈希不可逆，只能删掉前缀列；已哈希的 key 需要重新生成
    op.drop_column('api_keys', 'key_prefix')
