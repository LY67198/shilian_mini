"""retrieve_knowledge node — delegates to RetrievalCheckService for hybrid RAG"""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app.workflows.interview.state import InterviewState

logger = logging.getLogger(__name__)


async def retrieve_knowledge_node(state: InterviewState, config: RunnableConfig) -> dict:
    """用当前题目检索知识库（hybrid pipeline + self-check loop）。

    委托 RetrievalCheckService 执行 vector + BM25 + RRF + rerank + 自检 query rewrite。
    服务不可用或异常时降级返回空 knowledge_context。

    Args:
        state: 当前 InterviewState，需含 current_question。
        config: RunnableConfig，可选含 retrieval_check_service。

    Returns:
        写入 state 的字典，含 knowledge_context（字符串列表）。
    """
    current_question = state.get("current_question", "")

    if not current_question:
        return {"knowledge_context": []}

    check_service = config["configurable"].get("retrieval_check_service")
    if check_service is None:
        logger.warning("RetrievalCheckService 不可用，回退到空 knowledge_context")
        return {"knowledge_context": []}

    try:
        result = await check_service.check_and_retrieve(
            query=current_question,
        )
        return {
            "knowledge_context": result.final_context,
            "retrieval_debug": result.debug_info,
        }
    except Exception as e:
        logger.warning(f"知识库 RAG 检索失败，跳过注入: {e}")
        return {"knowledge_context": []}
