"""RetrievalCheckService — self-check retrieval loop (plain async while-loop)

替代原 StateGraph 编排（graph.py/state.py/nodes 已删）：
retrieve → check → (insufficient + retry<max) rewrite → retrieve …
最多 max_retries 次重写，最终 format_context 跨轮去重 + 按分排序产出 final_context。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.llm.client import get_chat_llm
from app.llm.prompts import load_prompt
from app.retrieval import SearchResult
from app.retrieval.pipeline import RetrievalPipeline

logger = logging.getLogger(__name__)

# 自检 LLM 单次调用的应用层超时（秒）。
# 实测 DeepSeek 偶发 "200 OK 后 response body 永久不发"，且 httpx read timeout 未触发，
# 导致 check_and_retrieve 永久挂起 → 整个 SSE 面试流无输出、前端"一直思考"。
# 用 asyncio.wait_for 兜底，超时抛 TimeoutError → 被 check_and_retrieve 的 except 捕获走 fallback。
_LLM_CALL_TIMEOUT_SECONDS: float = 60


@dataclass
class RetrievalCheckResult:
    """Output from self-check retrieval."""
    final_context: list[str] = field(default_factory=list)
    debug_info: dict[str, Any] = field(default_factory=dict)


class SufficiencyResult(BaseModel):
    """Structured output for retrieval sufficiency check."""
    sufficient: bool = Field(description="Whether context is sufficient")
    reason: str = Field(description="low_relevance | too_few | off_topic | ok")


async def _check_sufficiency(query: str, results: list[dict]) -> tuple[bool, str]:
    """评估当前召回结果是否足以回答 query。

    Args:
        query: 当前检索 query（可能是重写后的）。
        results: 最近一轮检索结果 dict 列表。

    Returns:
        (sufficient, reason) 二元组。
    """
    retrieved_content = "\n\n---\n".join(
        f"[{i + 1}] (score={r.get('score', 0):.2f}) {r.get('content', '')[:500]}"
        for i, r in enumerate(results[:10])
    )
    prompt = load_prompt("retrieval_check_sufficiency")
    llm = get_chat_llm(temperature=0.3).with_structured_output(
        SufficiencyResult, method="json_mode"
    )
    chain = prompt | llm
    result: SufficiencyResult = await asyncio.wait_for(
        chain.ainvoke({
            "query": query,
            "result_count": str(len(results)),
            "retrieved_content": retrieved_content,
        }),
        timeout=_LLM_CALL_TIMEOUT_SECONDS,
    )
    return result.sufficient, result.reason


async def _rewrite_query(original_query: str, retry_reason: str, history: list[dict]) -> str:
    """根据 retry_reason 和历史召回结果用 LLM 重写 query。

    Args:
        original_query: 原始 query。
        retry_reason: 上轮 check 的 reason。
        history: 全部历史轮次 [{round, query, results}]。

    Returns:
        重写后的 query 字符串。
    """
    retrieved_content = ""
    for h in history:
        for r in h.get("results", [])[:5]:
            retrieved_content += f"- {r.get('content', '')[:200]}\n"
    if not retrieved_content:
        retrieved_content = "(无)"

    prompt = load_prompt("retrieval_rewrite_query")
    llm = get_chat_llm(temperature=0.7)
    chain = prompt | llm
    response = await asyncio.wait_for(
        chain.ainvoke({
            "query": original_query,
            "reason": retry_reason,
            "retrieved_content": retrieved_content,
        }),
        timeout=_LLM_CALL_TIMEOUT_SECONDS,
    )
    return response.content.strip() if hasattr(response, "content") else str(response).strip()


def _format_context(
    query: str, history: list[dict], retry_count: int
) -> tuple[list[str], dict[str, Any]]:
    """将所有轮次检索结果去重、按相关性排序，编译最终 context 与 debug info。

    Args:
        query: 原始 query。
        history: 全部轮次 [{round, query, results}]。
        retry_count: 最终重写次数。

    Returns:
        (final_context, debug_info) 二元组。
    """
    seen: set[str] = set()
    all_results: list[dict] = []
    for h in history:
        for r in h.get("results", []):
            key = r.get("content", "")[:100]
            if key not in seen:
                seen.add(key)
                all_results.append(r)

    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    final_context = [r.get("content", "") for r in all_results if r.get("content")]

    debug_info = {
        "rounds": [
            {
                "query": h.get("query", ""),
                "result_count": len(h.get("results", [])),
                "top_scores": [r.get("score", 0) for r in h.get("results", [])[:3]],
            }
            for h in history
        ],
        "final_query": query,
        "total_retrieval_rounds": len(history),
        "total_unique_results": len(all_results),
        "retry_count": retry_count,
    }
    return final_context, debug_info


class RetrievalCheckService:
    """自检检索服务（纯 async while 循环）。

    Usage:
        service = RetrievalCheckService(pipeline, max_retries=2)
        result = await service.check_and_retrieve("Python GIL explained")
        context_strings = result.final_context
    """

    def __init__(self, pipeline: RetrievalPipeline, max_retries: int = 2):
        """初始化自检检索服务。

        Args:
            pipeline: 混合检索管线实例（vector + BM25 + RRF + rerank）。
            max_retries: 最大重试次数，超过后强制返回当前结果。
        """
        self.pipeline = pipeline
        self.max_retries = max_retries

    async def check_and_retrieve(
        self,
        query: str,
        max_retries: int | None = None,
        retrieval_filters: dict | None = None,
    ) -> RetrievalCheckResult:
        """运行带自检循环的混合检索。

        Args:
            query: 检索 query（面试题文本）。
            max_retries: 覆盖默认最大重试次数。
            retrieval_filters: 传给 pipeline 的可选过滤。

        Returns:
            RetrievalCheckResult，含 final_context 与 debug_info。
        """
        max_retries = max_retries if max_retries is not None else self.max_retries
        current_query = query
        retry_count = 0
        history: list[dict] = []

        try:
            while True:
                # ① retrieve：异常 → 空结果不抛（与原 retrieve_node 一致）
                try:
                    results: list[SearchResult] = await self.pipeline.search(
                        query=current_query, filters=retrieval_filters
                    )
                except Exception as e:
                    logger.warning(f"Retrieval failed: {e}")
                    results = []
                result_dicts = [
                    {
                        "id": r.id, "content": r.content, "score": r.score,
                        "source": r.source, "metadata": r.metadata,
                    }
                    for r in results
                ]
                history.append({
                    "round": retry_count,
                    "query": current_query,
                    "results": result_dicts,
                })

                # ② check：空结果短路 too_few（不调 LLM）；LLM 失败 → (True, 'ok') 不阻断
                if not result_dicts:
                    is_sufficient, retry_reason = False, "too_few"
                else:
                    try:
                        is_sufficient, retry_reason = await _check_sufficiency(
                            current_query, result_dicts
                        )
                    except Exception as e:
                        logger.warning(f"Sufficiency check failed: {e}")
                        is_sufficient, retry_reason = True, "ok"

                # ③ 决策：sufficient 或 retry_count >= max_retries → 结束
                if is_sufficient or retry_count >= max_retries:
                    break

                # ④ rewrite：失败保原文 query；retry_count 自增后回到 ①
                try:
                    current_query = await _rewrite_query(query, retry_reason, history)
                except Exception as e:
                    logger.warning(f"Query rewrite failed: {e}")
                    current_query = query
                retry_count += 1

            final_context, debug_info = _format_context(query, history, retry_count)
            return RetrievalCheckResult(final_context=final_context, debug_info=debug_info)
        except Exception as e:
            logger.error(f"Self-check retrieval failed: {e}")
            return RetrievalCheckResult(
                final_context=[],
                debug_info={"error": str(e)},
            )
