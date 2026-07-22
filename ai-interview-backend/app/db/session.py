"""数据库会话工具 — FastAPI Depends 注入 + 事务上下文管理器

- get_db: FastAPI Depends 注入的会话生成器
- transaction: 事务 ctx manager（自动 commit / rollback）
- async_session: 定时任务使用的会话上下文管理器
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from .base import get_session_local


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（FastAPI Depends 注入）

    Yields:
        AsyncSession 实例，请求结束后自动关闭。
    """
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def transaction(db: AsyncSession):
    """事务上下文管理器，自动提交或回滚

    Args:
        db: 已有的 AsyncSession

    Yields:
        同一个 AsyncSession；正常退出时 commit，异常时 rollback。
    """
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise


@asynccontextmanager
async def async_session():
    """为定时任务创建异步会话上下文管理器（不自动 commit）"""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
