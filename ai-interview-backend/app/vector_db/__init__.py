"""向量数据库层（pgvector，复用 Postgres）"""
from app.vector_db.client import health_check_vector

__all__ = ["health_check_vector"]
