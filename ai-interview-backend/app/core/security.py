"""核心安全工具 — JWT token 与密码哈希

- JWT（jose）：HS256，包含 jti / sub / scope / exp
- Token 哈希：SHA-256（无长度限制），存 DB 用 hmac.compare_digest 恒定时间比较
- 密码哈希：bcrypt（passlib CryptContext 单例）
"""
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, UTC
from typing import Optional, Dict

from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(
    subject: str,
    scope: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """创建访问令牌（access token）

    Args:
        subject: 令牌主体（一般是用户 ID / 管理员 ID）
        scope: 令牌作用域（client / backoffice）
        expires_delta: 自定义过期时间；默认使用 settings.ACCESS_TOKEN_EXPIRE_MINUTES

    Returns:
        编码后的 JWT 字符串
    """
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "scope": scope,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    """创建刷新令牌（refresh token，scope="refresh"，默认 7 天）"""
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=7)

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "jti": str(uuid.uuid4()),
        "scope": "refresh",
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_token(token: str, scope: str = None) -> Optional[Dict]:
    """验证 JWT 令牌

    Args:
        token: 待验证的 JWT 字符串
        scope: 可选作用域校验，传入后会校验 payload["scope"] 是否一致

    Returns:
        解码后的 payload dict；校验失败返回 None
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        if scope and payload.get("scope") != scope:
            return None
        return payload
    except JWTError:
        return None


# ── Token 哈希（SHA-256，无长度限制）──


def hash_token(token: str) -> str:
    """对 JWT token 进行 SHA-256 哈希"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token_hash(plain_token: str, hashed_token: str) -> bool:
    """验证 token 哈希（恒定时间比较）"""
    return hmac.compare_digest(
        hashlib.sha256(plain_token.encode("utf-8")).hexdigest(),
        hashed_token,
    )


# ── 密码哈希（bcrypt）──


def get_password_hash(password: str) -> str:
    """密码 bcrypt 哈希"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)
