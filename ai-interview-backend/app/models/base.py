"""SQLAlchemy ORM 公共基类

定义 BaseModel（__abstract__ = True），为所有业务表注入 created_at / updated_at 时间戳字段。
"""

from sqlalchemy import Column, Integer, func, TIMESTAMP
from sqlalchemy.sql import text
from app.db.base import Base


class BaseModel(Base):
    __abstract__ = True

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
