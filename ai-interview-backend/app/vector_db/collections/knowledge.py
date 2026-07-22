"""knowledge_chunks 向量检索（pgvector 实现）

embedding 存 Postgres 的 vector 列，与业务元数据同表 co-location。
- id 即 knowledge_chunks.id
- 距离度量 L2（<-> 运算符），DashScope 归一化向量下等价 cosine
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk
from app.vector_db.index import l2_distance_to_similarity

logger = logging.getLogger(__name__)


async def upsert_embedding(session: AsyncSession, chunk_id: int, embedding: List[float]) -> None:
    """把 embedding 写回已存在的 knowledge_chunks 行。"""
    await session.execute(
        update(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id).values(embedding=embedding)
    )
    await session.commit()


async def insert_chunks(session: AsyncSession, items: List[Dict[str, Any]]) -> List[int]:
    """批量写回 embedding。items: [{"id": int, "embedding": [float]}...]，返回成功 id 列表。"""
    ids: List[int] = []
    for it in items:
        await session.execute(
            update(KnowledgeChunk).where(KnowledgeChunk.id == it["id"]).values(embedding=it["embedding"])
        )
        ids.append(it["id"])
    await session.commit()
    logger.info(f"pgvector 写回 {len(ids)} 条 knowledge embedding")
    return ids


async def search(
    session: AsyncSession,
    query_vector: List[float],
    top_k: int = 4,
    document_ids: Optional[List[int]] = None,
    min_score: float = 0.0,
) -> List[Dict[str, Any]]:
    """向量检索（L2），返回与原 Milvus 版一致的 dict 结构。"""
    distance = KnowledgeChunk.embedding.l2_distance(query_vector).label("distance")
    stmt = select(KnowledgeChunk, distance).where(KnowledgeChunk.embedding.isnot(None))
    if document_ids:
        stmt = stmt.where(KnowledgeChunk.document_id.in_(document_ids))
    stmt = stmt.order_by(distance).limit(top_k)

    rows = (await session.execute(stmt)).all()
    out: List[Dict[str, Any]] = []
    for chunk, dist in rows:
        score = l2_distance_to_similarity(float(dist))
        if score < min_score:
            continue
        out.append({
            "id": chunk.id,
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            "metadata": chunk.metadata_ or {},
            "similarity": score,
        })
    return out


async def delete_by_document(session: AsyncSession, document_id: int) -> int:
    """清空该文档下所有 chunk 的 embedding（行本身由 PG 级联删除管理）。"""
    result = await session.execute(
        update(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id).values(embedding=None)
    )
    await session.commit()
    return result.rowcount or 0


async def get_by_ids(session: AsyncSession, ids: List[int]) -> List[Dict[str, Any]]:
    """按主键批量取 chunk 元数据。"""
    if not ids:
        return []
    rows = (await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))).scalars().all()
    return [{
        "id": c.id,
        "document_id": c.document_id,
        "chunk_index": c.chunk_index,
        "content": c.content,
        "content_hash": c.content_hash,
        "metadata": c.metadata_ or {},
    } for c in rows]
