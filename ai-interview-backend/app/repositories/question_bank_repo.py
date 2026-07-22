"""QuestionBank Repository — 题库数据访问层"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question_bank import QuestionBank
from app.repositories.base import BaseRepository


class QuestionBankRepository(BaseRepository[QuestionBank]):
    model = QuestionBank

    async def search_by_position(
        self,
        db: AsyncSession,
        position_tag: str,
        difficulty: Optional[str] = None,
        limit: int = 10,
    ) -> List[QuestionBank]:
        """按岗位标签 + 难度（可选）搜索活跃题目（按 use_count 升序，优先冷门题）。

    Args:
        db: 数据库会话。
        position_tag: 岗位标签。
        difficulty: 可选的难度等级。
        limit: 返回数量上限。

    Returns:
        活跃的 QuestionBank 列表。
    """
        stmt = select(QuestionBank).where(
            QuestionBank.is_active == True,
            QuestionBank.position_tag == position_tag,
        )
        if difficulty:
            stmt = stmt.where(QuestionBank.difficulty == difficulty)
        stmt = stmt.order_by(QuestionBank.use_count.asc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_position(
        self,
        db: AsyncSession,
        position_tag: str,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[QuestionBank], int]:
        """分页列出某一岗位的活跃题目（按 id 倒序）。

    Args:
        db: 数据库会话。
        position_tag: 岗位标签。
        page: 页码，从 1 开始。
        page_size: 每页大小。

    Returns:
        (题目列表, 总数) 元组。
    """
        base = select(QuestionBank).where(
            QuestionBank.is_active == True,
            QuestionBank.position_tag == position_tag,
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = await db.execute(count_stmt)
        total_count = int(total.scalar_one())

        stmt = (
            base
            .order_by(QuestionBank.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total_count

    async def increment_use_count(self, db: AsyncSession, question_id: int) -> None:
        """增加题目使用次数（题目不存在则静默跳过）。

    Args:
        db: 数据库会话。
        question_id: 题目主键。
    """
        q = await self.get_by_id(db, question_id)
        if q:
            q.use_count = (q.use_count or 0) + 1
            await db.flush()


question_bank_repo = QuestionBankRepository()
