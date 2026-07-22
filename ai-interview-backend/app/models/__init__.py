"""SQLAlchemy ORM 模型聚合入口

集中导出所有 ORM 类（用户/管理员/简历/面试/题库/知识库/岗位模板等），
供 Alembic 迁移与业务代码 import。
"""

from .user import User
from .token import Token
from .admin import Admin
from .waiting_list import WaitingList
from .resume import Resume
from .interview import Interview
from .interview_message import InterviewMessage
from .knowledge import KnowledgeDocument, KnowledgeChunk
from .question_bank import QuestionBank
from .position_template import PositionTemplate

__all__ = [
    "User", "Token", "Admin", "WaitingList",
    "Resume", "Interview", "InterviewMessage",
    "KnowledgeDocument", "KnowledgeChunk", "QuestionBank",
    "PositionTemplate",
]