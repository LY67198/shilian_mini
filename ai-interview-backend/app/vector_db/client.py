"""向量库健康检查（pgvector：向量能力已并入 Postgres）

保留 health_check 供 /health 端点调用；pgvector 与主库同生命周期，
只要 Postgres 通即视为向量库可用。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def health_check_vector(session) -> bool:
    """确认 pgvector 扩展可用。"""
    try:
        from sqlalchemy import text
        r = await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        return r.first() is not None
    except Exception as e:
        logger.warning(f"pgvector 健康检查失败: {e}")
        return False
