"""题库服务（pgvector 版）

业务字段与 embedding 均存 PostgreSQL。
- 写策略：PG insert → 拿 auto-increment id → 写回 embedding；失败回滚 PG
- 读策略：直接调 pgvector search
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question_bank import QuestionBank
from app.llm.embedding import embed_text, embed_texts
from app.vector_db.collections import question_bank as question_vdb

logger = logging.getLogger(__name__)


def _build_embedding_text(question: str, reference_answer: Optional[str]) -> str:
    """构造用于向量化的文本：题面 + 参考答案"""
    parts = [question.strip()]
    if reference_answer and reference_answer.strip():
        parts.append(f"参考答案：{reference_answer.strip()}")
    return "\n\n".join(parts)


class QuestionBankService:
    """题库服务，提供题目的 CRUD、向量化、批量索引重建和语义检索功能（PG + pgvector 同表）。"""

    # ── CRUD ────────────────────────────────────────────────────────────

    async def create(self, db: AsyncSession, data: dict) -> QuestionBank:
        """新增题目：PG 拿 id → 写回 embedding；embedding 失败回滚 PG"""
        emb_text = _build_embedding_text(data["question"], data.get("reference_answer"))

        # 1. PG insert（先不 embed，拿到 id 再 embed，避免无效 embedding 浪费）
        q = QuestionBank(
            category=data["category"],
            position_tag=data["position_tag"],
            difficulty=data["difficulty"],
            question=data["question"],
            reference_answer=data.get("reference_answer"),
            key_points=data.get("key_points"),
            tags=data.get("tags"),
            source=data.get("source", "manual"),
            created_by=data.get("created_by"),
            embedding_text=emb_text,
        )
        db.add(q)
        await db.commit()
        await db.refresh(q)

        # 2. Embed + pgvector 写回
        try:
            embedding = await embed_text(emb_text)
            await question_vdb.insert_questions(db, [{"id": q.id, "embedding": embedding}])
            logger.info(f"题目 {q.id} 已创建并向量化（PG + pgvector）")
        except Exception as e:
            # embedding 失败 → 回滚 PG
            await db.delete(q)
            await db.commit()
            logger.error(f"题目创建 embedding 失败，已回滚 PG: {e}")
            raise
        return q

    async def get_by_id(self, db: AsyncSession, question_id: int) -> Optional[QuestionBank]:
        """根据主键获取题目。

        Args:
            db: 数据库会话。
            question_id: 题目 ID。

        Returns:
            QuestionBank 实例，不存在则返回 None。
        """
        return await db.get(QuestionBank, question_id)

    async def update(self, db: AsyncSession, question_id: int, data: dict) -> Optional[QuestionBank]:
        """更新题目；若改题面/答案则重新向量化（同步写 PG + pgvector）"""
        q = await db.get(QuestionBank, question_id)
        if not q:
            return None

        need_reembed = False
        for field in ("question", "reference_answer"):
            if field in data and data[field] != getattr(q, field):
                need_reembed = True

        for field, val in data.items():
            if hasattr(q, field) and field != "id":
                setattr(q, field, val)

        await db.commit()
        await db.refresh(q)

        if need_reembed:
            emb_text = _build_embedding_text(q.question, q.reference_answer)
            q.embedding_text = emb_text
            await db.commit()
            # pgvector：清空 + 重写（同 id）
            await question_vdb.delete_question(db, q.id)
            embedding = await embed_text(emb_text)
            await question_vdb.insert_questions(db, [{"id": q.id, "embedding": embedding}])
            logger.info(f"题目 {q.id} 已更新并重新向量化")
        return q

    async def delete(self, db: AsyncSession, question_id: int) -> bool:
        """删除：先清空 embedding 再删 PG 行"""
        try:
            await question_vdb.delete_question(db, question_id)
        except Exception as e:
            logger.warning(f"pgvector 删除 question_id={question_id} 失败（继续删 PG 行）: {e}")

        result = await db.execute(
            delete(QuestionBank).where(QuestionBank.id == question_id)
        )
        await db.commit()
        return result.rowcount > 0

    async def toggle(self, db: AsyncSession, question_id: int, is_active: bool) -> bool:
        """启用/禁用（PG only，pgvector 端按 is_active 过滤由调用方决定）"""
        result = await db.execute(
            update(QuestionBank)
            .where(QuestionBank.id == question_id)
            .values(is_active=is_active)
        )
        await db.commit()
        return result.rowcount > 0

    async def get_list(
        self,
        db: AsyncSession,
        page: int = 1,
        size: int = 20,
        category: Optional[str] = None,
        position_tag: Optional[str] = None,
        difficulty: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> dict:
        """分页查询（仅 PG，不涉及向量）"""
        stmt = select(QuestionBank)
        if category:
            stmt = stmt.where(QuestionBank.category == category)
        if position_tag:
            stmt = stmt.where(QuestionBank.position_tag.contains(position_tag))
        if difficulty:
            stmt = stmt.where(QuestionBank.difficulty == difficulty)
        if is_active is not None:
            stmt = stmt.where(QuestionBank.is_active == is_active)
        if search:
            stmt = stmt.where(
                or_(
                    QuestionBank.question.ilike(f"%{search}%"),
                    QuestionBank.reference_answer.ilike(f"%{search}%"),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(QuestionBank.created_at.desc()).offset((page - 1) * size).limit(size)
        items = (await db.execute(stmt)).scalars().all()
        return {"items": items, "total": total, "page": page, "size": size}

    # ── 向量化 ──────────────────────────────────────────────────────────

    async def embed_question(self, db: AsyncSession, question_id: int) -> bool:
        """单题重新向量化：更新 PG embedding_text 并重新写入 pgvector 向量。

        Args:
            db: 数据库会话。
            question_id: 题目 ID。

        Returns:
            成功返回 True，题目不存在返回 False。
        """
        q = await db.get(QuestionBank, question_id)
        if not q:
            return False
        emb_text = _build_embedding_text(q.question, q.reference_answer)
        q.embedding_text = emb_text
        await db.commit()

        embedding = await embed_text(emb_text)
        await question_vdb.delete_question(db, q.id)
        await question_vdb.insert_questions(db, [{"id": q.id, "embedding": embedding}])
        return True

    def batch_embed_sync(self, question_ids: List[int]) -> dict:
        """同步批量向量化（Celery 调用）"""
        import asyncio
        from app.db.base import get_session_local

        async def _run():
            async with get_session_local()() as db:
                stmt = select(QuestionBank).where(QuestionBank.id.in_(question_ids))
                rows = (await db.execute(stmt)).scalars().all()

                texts = [_build_embedding_text(q.question, q.reference_answer) for q in rows]
                embeddings = await embed_texts(texts)

                # 更新 PG embedding_text
                for q, txt in zip(rows, texts):
                    q.embedding_text = txt
                await db.commit()

                # pgvector 批量写回 embedding
                items = [
                    {"id": q.id, "embedding": emb}
                    for q, emb in zip(rows, embeddings)
                ]
                await question_vdb.insert_questions(db, items)
                return len(rows)

        count = asyncio.run(_run())
        logger.info(f"批量向量化完成：{count} 题")
        return {"embedded": count}

    def reindex_all_sync(self) -> dict:
        """全量重建所有题目的 embedding（升级向量模型时使用）。

        按每批 50 题分组，逐批调用 batch_embed_sync 完成向量重建。

        Returns:
            包含 total_reindexed 计数的字典。
        """
        import asyncio
        from app.db.base import get_session_local

        async def _run():
            async with get_session_local()() as db:
                stmt = select(QuestionBank)
                rows = (await db.execute(stmt)).scalars().all()
                ids = [q.id for q in rows]

            batch_size = 50
            total = 0
            for i in range(0, len(ids), batch_size):
                batch_ids = ids[i: i + batch_size]
                result = self.batch_embed_sync(batch_ids)
                total += result["embedded"]
            return total

        total = asyncio.run(_run())
        return {"total_reindexed": total}

    # ── 检索 ─────────────────────────────────────────────────────────────

    async def retrieve_questions(
        self,
        query: str,
        db: AsyncSession = None,  # noqa 保留参数仅为向后兼容
        k: int = 20,
        position_tag: Optional[str] = None,
        difficulty: Optional[str] = None,
        min_score: float = 0.7,
    ) -> list[dict]:
        """语义检索（pgvector 端执行，支持 position_tag / difficulty 过滤）"""
        if db is None:
            raise ValueError("retrieve_questions requires a non-None db session")
        query_vec = await embed_text(query)
        return await question_vdb.search(
            db,
            query_vec,
            top_k=k,
            position_tag=position_tag,
            difficulty=difficulty,
            min_score=min_score,
        )

    async def increment_use_count(self, db: AsyncSession, question_ids: List[int]) -> None:
        """面试选题后增加使用次数（PG only）"""
        if not question_ids:
            return
        await db.execute(
            update(QuestionBank)
            .where(QuestionBank.id.in_(question_ids))
            .values(use_count=QuestionBank.use_count + 1)
        )
        await db.commit()

    async def get_stats(self, db: AsyncSession) -> dict:
        """题库统计（PG only — pgvector 端所有有效记录都有 embedding）"""
        by_category = (
            await db.execute(
                select(QuestionBank.category, func.count().label("cnt"))
                .group_by(QuestionBank.category)
            )
        ).all()
        by_difficulty = (
            await db.execute(
                select(QuestionBank.difficulty, func.count().label("cnt"))
                .group_by(QuestionBank.difficulty)
            )
        ).all()
        top_used = (
            await db.execute(
                select(QuestionBank.id, QuestionBank.question, QuestionBank.use_count)
                .order_by(QuestionBank.use_count.desc())
                .limit(10)
            )
        ).all()
        total = (await db.execute(select(func.count()).select_from(QuestionBank))).scalar_one()

        return {
            "total": total,
            # 注：所有有效记录都有 embedding，不再单独统计
            "embedded": total,
            "by_category": {row[0]: row[1] for row in by_category},
            "by_difficulty": {row[0]: row[1] for row in by_difficulty},
            "top_used": [{"id": r[0], "question": r[1][:50], "use_count": r[2]} for r in top_used],
        }


question_bank_service = QuestionBankService()
