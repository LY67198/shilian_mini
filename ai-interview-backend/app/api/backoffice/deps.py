"""管理端 API 依赖注入 — OAuth2 Bearer 鉴权 + DB 会话

- oauth2_scheme: FastAPI OAuth2PasswordBearer（指向 /api/v1/backoffice/auth/login）
- get_current_admin: 校验 scope=backoffice 的 JWT，返回当前 Admin ORM 实例
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.core.security import verify_token
from app.models.admin import Admin
from app.core.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/backoffice/auth/login")


async def get_current_admin(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Admin:
    """获取当前登录管理员（scope=backoffice）

    校验流程：解析 token → 取 sub → 查 Admin → 校验 is_active。

    Args:
        token: HTTP Bearer JWT（scope=backoffice）。
        db: 异步数据库会话（FastAPI Depends 注入）。

    Returns:
        Admin: 已认证且 is_active=True 的管理员 ORM 实例。

    Raises:
        HTTPException 403: token 无效 / 管理员不存在 / 管理员已停用。
    """
    payload = verify_token(token, scope="backoffice")
    if not payload:
        raise HTTPException(
            status_code=403,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    admin_id = payload.get("sub")
    admin_query = select(Admin).where(Admin.id == int(admin_id))
    result = await db.execute(admin_query)
    admin = result.scalar_one_or_none()
    if admin is None or not admin.is_active:
        raise HTTPException(status_code=403, detail="Inactive admin")
    return admin
