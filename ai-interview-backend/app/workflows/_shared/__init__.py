"""LangGraph 工作流共享基础设施

Phase 1 引入，所有 workflow（interview / question_gen / evaluation）共用：
- `checkpointer`   — AsyncPostgresSaver 单例 + schema 初始化
- `state_base`     — 公共 TypedDict 字段（thread_id / retry_count / errors）
- `tracing`        — LangSmith 配置（默认 off）
- `format_exception` — ExceptionGroup 解包
"""
from app.workflows._shared.checkpointer import get_checkpointer, close_checkpointer
from app.workflows._shared.state_base import BaseWorkflowState
from app.workflows._shared.format_exception import flatten_exceptions, format_exception_chain
from app.workflows._shared.tracing import configure_langsmith, is_tracing_enabled

__all__ = [
    "get_checkpointer",
    "close_checkpointer",
    "BaseWorkflowState",
    "format_exception_chain",
    "flatten_exceptions",
    "configure_langsmith",
    "is_tracing_enabled",
]
