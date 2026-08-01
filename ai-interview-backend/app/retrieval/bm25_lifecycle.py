"""BM25 index lifecycle — build on startup, provide singletons"""
from __future__ import annotations

import logging

from app.retrieval.bm25 import BM25Index

logger = logging.getLogger(__name__)

# Module-level singletons, built during startup
knowledge_bm25: BM25Index | None = None
question_bank_bm25: BM25Index | None = None


async def build_bm25_indices(db_session) -> None:
    """从 PostgreSQL 数据构建两个 BM25 索引（应用启动时调用）。

    会加载 KnowledgeChunk(id, content) 和 QuestionBank(id, question, reference_answer)，
    以 (pg_id, text) 对构建全局单例，使 BM25 搜索结果携带真实的数据库主键。

    Args:
        db_session: 启动时传入的 AsyncSession。
    """
    global knowledge_bm25, question_bank_bm25

    from sqlalchemy import select
    from app.models.knowledge import KnowledgeChunk
    from app.models.question_bank import QuestionBank

    # Build knowledge index — SELECT id + content
    result = await db_session.execute(
        select(KnowledgeChunk.id, KnowledgeChunk.content)
    )
    knowledge_items = [(row[0], row[1]) for row in result.fetchall() if row[1]]
    knowledge_bm25 = BM25Index("knowledge_chunks")
    knowledge_bm25.build(knowledge_items)

    # Build question bank index — SELECT id + question + full metadata
    result = await db_session.execute(
        select(
            QuestionBank.id,
            QuestionBank.question,
            QuestionBank.reference_answer,
            QuestionBank.key_points,
            QuestionBank.difficulty,
            QuestionBank.position_tag,
        )
    )
    q_items = []
    for qid, question, answer, key_points, difficulty, position_tag in result.fetchall():
        text = f"{question or ''} {answer or ''}".strip()
        if text:
            q_items.append((qid, text, {
                "question": question,
                "reference_answer": answer,
                "key_points": key_points,
                "difficulty": difficulty,
                "position_tag": position_tag,
            }))
    question_bank_bm25 = BM25Index("question_bank")
    question_bank_bm25.build(q_items)

    logger.info(
        "BM25 indices built: knowledge=%d, question_bank=%d",
        knowledge_bm25.corpus_size,
        question_bank_bm25.corpus_size,
    )


def get_knowledge_bm25() -> BM25Index | None:
    """获取知识库 BM25 索引单例。

    Returns:
        已构建的知识库 BM25Index 实例，若尚未构建则返回 None。
    """
    return knowledge_bm25


def get_question_bank_bm25() -> BM25Index | None:
    """获取题库 BM25 索引单例。

    Returns:
        已构建的题库 BM25Index 实例，若尚未构建则返回 None。
    """
    return question_bank_bm25
