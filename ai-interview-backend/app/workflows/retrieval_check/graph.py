"""retrieval_check StateGraph — self-check loop with max 2 retries"""
from __future__ import annotations

import logging
from typing import Optional

from langgraph.graph import END, START, StateGraph

from app.workflows.retrieval_check.nodes import (
    check_sufficiency_node,
    format_context_node,
    retrieve_node,
    rewrite_query_node,
)
from app.workflows.retrieval_check.state import RetrievalCheckState

logger = logging.getLogger(__name__)


def build_retrieval_check_graph() -> StateGraph:
    """构建检索自检 StateGraph（不编译）。

    流程：
        START → retrieve → check_sufficiency
                              ├── sufficient → format_context → END
                              └── insufficient → rewrite_query → retrieve
                                                    （最多 2 次重试，然后走 format_context）

    Returns:
        未编译的 StateGraph 构造器，供上层 compile。
    """
    builder = StateGraph(RetrievalCheckState)

    builder.add_node("retrieve", retrieve_node)
    builder.add_node("check_sufficiency", check_sufficiency_node)
    builder.add_node("rewrite_query", rewrite_query_node)
    builder.add_node("format_context", format_context_node)

    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "check_sufficiency")

    builder.add_conditional_edges(
        "check_sufficiency",
        _route_after_check,
        {
            "sufficient": "format_context",
            "insufficient": "rewrite_query",
        },
    )

    builder.add_edge("rewrite_query", "retrieve")  # loop back
    builder.add_edge("format_context", END)

    return builder


def _route_after_check(state: RetrievalCheckState) -> str:
    """条件边路由：sufficient → format, insufficient → rewrite。

    重试次数由 state.custom.max_retries 控制，默认 2；超过则强制走 sufficient 路径。

    Args:
        state: 当前自检循环状态。

    Returns:
        "sufficient" 或 "insufficient" 作为条件边的 key。
    """
    if state.get("is_sufficient"):
        return "sufficient"

    max_retries = (state.get("custom") or {}).get("max_retries", 2)
    if state.get("retry_count", 0) >= max_retries:
        logger.info("Max retries reached, proceeding with current results")
        return "sufficient"  # force format_context path

    return "insufficient"
