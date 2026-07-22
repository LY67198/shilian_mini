"""retrieve node — call RetrievalPipeline.search()"""
from __future__ import annotations

import logging

from app.retrieval import SearchResult
from app.workflows.retrieval_check.state import RetrievalCheckState

logger = logging.getLogger(__name__)


async def retrieve_node(state: RetrievalCheckState) -> dict:
    """运行检索管线（pipeline 实例通过 state.custom 注入，规避序列化问题）。

    Args:
        state: 当前自检状态，需含 custom.pipeline；可选 current_query / query / retrieval_filters。

    Returns:
        写入 state 的字典，含 retrieval_results（最近一轮结果）和 retrieval_history（累积历史）。
    """
    custom = state.get("custom") or {}
    pipeline = custom.get("pipeline")
    if pipeline is None:
        logger.error("No pipeline in state.custom — cannot run retrieval")
        return {"retrieval_results": [], "retrieval_history": []}

    query = state.get("current_query") or state.get("query", "")
    if not query:
        return {"retrieval_results": [], "retrieval_history": []}

    try:
        results: list[SearchResult] = await pipeline.search(
            query=query,
            filters=custom.get("retrieval_filters"),
        )
    except Exception as e:
        logger.warning(f"Retrieval failed: {e}")
        results = []

    result_dicts = [
        {"id": r.id, "content": r.content, "score": r.score,
         "source": r.source, "metadata": r.metadata}
        for r in results
    ]

    history: list[dict] = list(state.get("retrieval_history", []))
    history.append({
        "round": state.get("retry_count", 0),
        "query": query,
        "results": result_dicts,
    })

    return {
        "retrieval_results": result_dicts,
        "retrieval_history": history,
    }
