"""Resume ORM 模型 — 候选人简历

字段：
- id / user_id / file_path / original_filename / file_size
- parsed_content: ai_service.parse_resume 输出（JSON 字符串）
- parse_status: pending / processing / completed / failed

约束：
- user_id → user.id 外键，级联删除
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from .base import BaseModel


class Resume(BaseModel):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    file_url = Column(String(500), nullable=True)
    file_name = Column(String(255), nullable=True)
    parsed_content = Column(Text, nullable=True)  # AI 解析后的 JSON 结构化数据
    analysis = Column(Text, nullable=True)  # AI 分析 JSON（优劣势/建议）
    target_position = Column(String(255), nullable=True)
    status = Column(String(20), default="pending")  # pending/parsing/completed/failed

    # 关联关系
    user = relationship("User", backref="resumes")
    interviews = relationship("Interview", back_populates="resume")
