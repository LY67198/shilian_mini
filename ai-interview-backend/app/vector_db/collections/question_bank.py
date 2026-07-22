"""question_bank 向量检索（pgvector 实现）

- id 即 question_bank.id
- 支持 position_tag（模糊）/ difficulty（精确）过滤 + L2 向量排序
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question_bank import QuestionBank
from app.vector_db.index import l2_distance_to_similarity

logger = logging.getLogger(__name__)


async def insert_questions(session: AsyncSession, items: List[Dict[str, Any]]) -> List[int]:
    """批量写回 embedding。items: [{"id": int, "embedding": [float]}...]。"""
    ids: List[int] = []
    for it in items:
        await session.execute(
            update(QuestionBank).where(QuestionBank.id == it["id"]).values(embedding=it["embedding"])
        )
        ids.append(it["id"])
    await session.commit()
    logger.info(f"pgvector 写回 {len(ids)} 条 question embedding")
    return ids


async def search(
    session: AsyncSession,
    query_vector: List[float],
    top_k: int = 20,
    position_tag: Optional[str] = None,
    difficulty: Optional[str] = None,
    min_score: float = 0.7,
) -> List[Dict[str, Any]]:
    """向量检索 + 过滤，返回标准化的 dict 结构。"""
    distance = QuestionBank.embedding.l2_distance(query_vector).label("distance")
    stmt = select(QuestionBank, distance).where(
        QuestionBank.embedding.isnot(None), QuestionBank.is_active.is_(True)
    )
    if position_tag:
        stmt = stmt.where(QuestionBank.position_tag.ilike(f"%{position_tag}%"))
    if difficulty:
        stmt = stmt.where(QuestionBank.difficulty == difficulty)
    stmt = stmt.order_by(distance).limit(top_k)

    rows = (await session.execute(stmt)).all()
    out: List[Dict[str, Any]] = []
    for q, dist in rows:
        score = l2_distance_to_similarity(float(dist))
        if score < min_score:
            continue
        out.append({
            "id": q.id,
            "category": q.category,
            "position_tag": q.position_tag,
            "difficulty": q.difficulty,
            "question": q.question,
            "reference_answer": q.reference_answer,
            "key_points": q.key_points,
            "tags": q.tags,
            "similarity": score,
            "source": "from_bank",
        })
    return out


async def delete_question(session: AsyncSession, question_id: int) -> int:
    """清空单条题目的 embedding。"""
    result = await session.execute(
        update(QuestionBank).where(QuestionBank.id == question_id).values(embedding=None)
    )
    await session.commit()
    return result.rowcount or 0
