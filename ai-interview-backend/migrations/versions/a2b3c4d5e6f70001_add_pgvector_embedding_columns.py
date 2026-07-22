"""add pgvector embedding columns

为 Milvus → pgvector 迁移做准备：
1. 启用 pgvector 扩展（pgvector/pgvector:pg16 镜像已内置）
2. 加 embedding 向量列（1024 维，与 DashScope text-embedding-v3 一致）
3. 建 HNSW 索引（L2 距离，与 DashScope 归一化向量语义一致）

距离度量：L2（与 spec 一致；DashScope embedding 已归一化，l2 distance 对归一化向量等价于 cosine）
HNSW 参数：m=16, ef_construction=200（沿用原 Milvus 配置）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f70001'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 启用 pgvector 扩展（pgvector/pgvector:pg16 镜像已内置）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. 加向量列
    op.add_column("knowledge_chunks", sa.Column("embedding", Vector(1024), nullable=True))
    op.add_column("question_bank", sa.Column("embedding", Vector(1024), nullable=True))

    # 3. 建 HNSW 索引（L2 距离，与 DashScope 归一化向量语义一致）
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_l2_ops) "
        "WITH (m = 16, ef_construction = 200)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_question_bank_embedding_hnsw "
        "ON question_bank USING hnsw (embedding vector_l2_ops) "
        "WITH (m = 16, ef_construction = 200)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_question_bank_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.drop_column("question_bank", "embedding")
    op.drop_column("knowledge_chunks", "embedding")
