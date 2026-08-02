"""Admin ORM 模型 — 后台管理员账号

字段：
- id / role / email / first_name / last_name / password / is_active
- 密码哈希/校验走 app.core.security.pwd_context 单例

约束：
- email 唯一索引
- role: SAEnum(UserRole, values_callable) 持久化枚举值 admin / superadmin（与迁移/API 比较一致）
"""

import enum

from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship

from app.core.security import pwd_context
from .base import BaseModel


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class Admin(BaseModel):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    role = Column(
        SAEnum(
            UserRole,
            name="userrole",
            create_type=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=UserRole.ADMIN,
        server_default="admin",
        nullable=False,
    )
    email = Column(String(255), unique=True, index=True, nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

    @staticmethod
    def get_password_hash(password: str) -> str:
        return pwd_context.hash(password)

    def verify_password(self, plain_password: str) -> bool:
        return pwd_context.verify(plain_password, self.password)
