"""岗位模板服务 — CRUD + 与题库 / 知识库关联查询

岗位模板是题库 RAG 的 position_tag 上层抽象，定义岗位名称、考察重点、技能清单。
"""
import logging
from sqlalchemy import select, delete, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position_template import PositionTemplate

logger = logging.getLogger(__name__)


class PositionTemplateService:
    """岗位模板服务 — 增删改查、启用/禁用和按标签查询，供 Agent 和前后台使用。"""

    async def get_list(
        self,
        db: AsyncSession,
        page: int = 1,
        size: int = 20,
        category: str | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> dict:
        """分页查询岗位模板列表，支持分类、启用状态和关键词筛选。

    Args:
        db: 数据库会话。
        page: 页码，从 1 开始。
        size: 每页大小。
        category: 可选的分类过滤。
        is_active: 可选的启用状态过滤。
        search: 可选的关键词，按 title 或 position_tag 模糊匹配。

    Returns:
        包含 items / total / page / size 的字典。
    """
        stmt = select(PositionTemplate)
        if category:
            stmt = stmt.where(PositionTemplate.category == category)
        if is_active is not None:
            stmt = stmt.where(PositionTemplate.is_active == is_active)
        if search:
            stmt = stmt.where(
                or_(
                    PositionTemplate.title.ilike(f"%{search}%"),
                    PositionTemplate.position_tag.ilike(f"%{search}%"),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(
            PositionTemplate.sort_order.asc(),
            PositionTemplate.id.asc(),
        ).offset((page - 1) * size).limit(size)
        items = (await db.execute(stmt)).scalars().all()
        return {"items": items, "total": total, "page": page, "size": size}

    async def get_by_id(self, db: AsyncSession, template_id: int) -> PositionTemplate | None:
        """根据岗位模板主键查询单条记录。

    Args:
        db: 数据库会话。
        template_id: 模板主键 ID。

    Returns:
        PositionTemplate 实例或 None。
    """
        return await db.get(PositionTemplate, template_id)

    async def get_by_tag(self, db: AsyncSession, position_tag: str) -> PositionTemplate | None:
        """根据岗位标签查询岗位模板，Agent 和前后台都会用到。

    Args:
        db: 数据库会话。
        position_tag: 岗位标签字符串。

    Returns:
        PositionTemplate 实例或 None。
    """
        result = await db.execute(
            select(PositionTemplate).where(PositionTemplate.position_tag == position_tag)
        )
        return result.scalar_one_or_none()

    async def get_active_list(self, db: AsyncSession) -> list[PositionTemplate]:
        """供 Agent 工具使用：获取所有启用中的岗位模板。

    Args:
        db: 数据库会话。

    Returns:
        按 sort_order 升序排列的启用模板列表。
    """
        result = await db.execute(
            select(PositionTemplate)
            .where(PositionTemplate.is_active == True)
            .order_by(PositionTemplate.sort_order.asc())
        )
        return result.scalars().all()

    async def create(self, db: AsyncSession, data: dict) -> PositionTemplate:
        """创建岗位模板，并在写入前校验 position_tag 是否重复。

    Args:
        db: 数据库会话。
        data: PositionTemplate 字段字典，必须含 position_tag。

    Returns:
        新创建的 PositionTemplate 实例。

    Raises:
        ValueError: 同一 position_tag 已存在。
    """
        existing = await self.get_by_tag(db, data["position_tag"])
        if existing:
            raise ValueError(f"岗位标签 {data['position_tag']} 已存在")

        t = PositionTemplate(**data)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        logger.info(f"岗位模板 {t.id} ({t.position_tag}) 已创建")
        return t

    async def update(self, db: AsyncSession, template_id: int, data: dict) -> PositionTemplate | None:
        """更新岗位模板的可变字段，返回更新后的记录。

    Args:
        db: 数据库会话。
        template_id: 模板主键。
        data: 待更新的字段字典，仅更新 model 上存在的字段。

    Returns:
        更新后的 PositionTemplate 实例；不存在则返回 None。
    """
        t = await db.get(PositionTemplate, template_id)
        if not t:
            return None
        for field, val in data.items():
            if hasattr(t, field):
                setattr(t, field, val)
        await db.commit()
        await db.refresh(t)
        return t

    async def delete(self, db: AsyncSession, template_id: int) -> bool:
        """删除岗位模板，成功则返回 True。

    Args:
        db: 数据库会话。
        template_id: 模板主键。

    Returns:
        是否删除成功（至少影响一行）。
    """
        result = await db.execute(
            delete(PositionTemplate).where(PositionTemplate.id == template_id)
        )
        await db.commit()
        return result.rowcount > 0

    async def toggle(self, db: AsyncSession, template_id: int, is_active: bool) -> bool:
        """启用或禁用岗位模板，用于后台管理控制模板是否参与匹配。

    Args:
        db: 数据库会话。
        template_id: 模板主键。
        is_active: 目标启用状态。

    Returns:
        是否更新成功。
    """
        result = await db.execute(
            update(PositionTemplate)
            .where(PositionTemplate.id == template_id)
            .values(is_active=is_active)
        )
        await db.commit()
        return result.rowcount > 0


position_template_service = PositionTemplateService()
