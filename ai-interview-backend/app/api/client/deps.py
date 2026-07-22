"""客户端 API 依赖注入 — OAuth2 Bearer 鉴权 + DB 会话

- oauth2_scheme: FastAPI OAuth2PasswordBearer（指向 /api/v1/auth/login）
- get_current_user: 校验 scope=client 的 JWT，返回当前 User ORM 实例
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.config import settings
from app.db.session import get_db
from app.core.security import verify_token
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """获取当前登录用户（scope=client）

    Args:
        token: OAuth2 Bearer token（依赖 OAuth2PasswordBearer 自动提取）
        db: AsyncSession（依赖 get_db）

    Returns:
        当前登录的 User ORM 实例。

    Raises:
        HTTPException: 401/403 — token 无效 / 用户不存在 / 用户被禁用。
    """
    payload = verify_token(token, scope="client")
    if not payload:
        raise HTTPException(
            status_code=403,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    user_query = select(User).where(User.id == int(user_id))
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=403, detail="无效的认证凭据")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="用户已被禁用")
    return user
