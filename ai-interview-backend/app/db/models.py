"""SQLAlchemy 共享 Base — 所有 ORM 模型继承此类（已迁移到 app.models.base.BaseModel）

历史：早期声明式基类；Phase 1 后改为 app/models/base.py:BaseModel 含 TimestampMixin。
保留此文件以防 Alembic 迁移脚本引用 Base。
"""
from sqlalchemy.ext.declarative import declarative_base

# SQLAlchemy 基类，所有模型继承此类
Base = declarative_base()