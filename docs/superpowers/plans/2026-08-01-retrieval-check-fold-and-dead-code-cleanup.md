# retrieval_check 降级 + 死代码清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `app/workflows/retrieval_check/` 的 LangGraph StateGraph 折叠为纯 async while 循环（修复 HITL resume 后知识注入为空的门控 bug + debug_info 可观测），并清理 6 项死代码、修复 rerank 事件循环阻塞。

**Architecture:** Topic 2 保留 `RetrievalCheckService` 类与包路径，`check_and_retrieve()` 内部以普通 while 循环替代 `graph.ainvoke`，精确复刻 4 个兜底语义与 `max_retries=2` 边界（3 检索轮 + 2 重写）；`submit_answer` 每轮无条件构建 service。Topic 3 删除零调用者的死代码并同步改 `__init__.py` 导出，rerank 用 `asyncio.to_thread` 去阻塞。

**Tech Stack:** Python 3.14 / LangChain 1.3 / FastAPI / pytest 8.3 / SQLAlchemy async（测试在 `shilian-app` 容器内运行，见 CLAUDE.md「测试在容器内跑」）。

**设计依据**：`docs/superpowers/specs/2026-08-01-retrieval-check-fold-and-dead-code-cleanup-design.md`（Approved，dev `e66afc8`）

---

## 前置：容器与基线

- 若本地容器未起：`cd ai-interview-backend && docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
- 基线：`docker exec shilian-app pytest -m "unit"` 应 **35 passed**（现有基线）

---

### Task 1: 重写 `RetrievalCheckService` 为纯 while 循环（含 TDD）

**Files:**
- Create: `tests/unit/test_retrieval_check_service.py`
- Rewrite: `app/workflows/retrieval_check/service.py`
- Update: `app/workflows/retrieval_check/__init__.py`
- Delete: `app/workflows/retrieval_check/graph.py`、`app/workflows/retrieval_check/state.py`、`app/workflows/retrieval_check/nodes/`（5 个文件）、`tests/unit/test_retrieval_check_nodes.py`

- [ ] **Step 1: 写新测试文件**

创建 `tests/unit/test_retrieval_check_service.py`，覆盖 while 循环 7 个语义 + `_format_context` 去重排序：

```python
"""retrieval_check while-loop service unit tests"""
from __future__ import annotations

import pytest

from app.retrieval import SearchResult
from app.workflows.retrieval_check.service import (
    RetrievalCheckService,
    _format_context,
)


@pytest.mark.unit
class TestSelfCheckRetrieve:
    """check_and_retrieve — while-loop self-check retrieval"""

    async def test_empty_results_loops_to_max_retries(self, monkeypatch):
        """空结果 → too_few 短路 → 触发 rewrite → 恰好 3 轮 + 2 重写"""
        class EmptyPipeline:
            async def search(self, query, filters=None):
                return []

        async def fake_rewrite(original_query, retry_reason, history):
            return original_query + " rewritten"

        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._rewrite_query", fake_rewrite
        )

        service = RetrievalCheckService(pipeline=EmptyPipeline(), max_retries=2)
        result = await service.check_and_retrieve("python GIL")

        assert result.final_context == []
        assert result.debug_info["total_retrieval_rounds"] == 3
        assert result.debug_info["retry_count"] == 2
        assert result.debug_info["rounds"][1]["query"] != "python GIL"

    async def test_llm_check_failure_falls_back_to_sufficient(self, monkeypatch):
        """LLM check 失败 → (True,'ok') 不阻断，1 轮即结束"""
        class MockPipeline:
            async def search(self, query, filters=None):
                return [SearchResult(id=1, content="relevant", score=0.9, source="both")]

        async def fake_check(query, results):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._check_sufficiency", fake_check
        )

        service = RetrievalCheckService(pipeline=MockPipeline(), max_retries=2)
        result = await service.check_and_retrieve("python GIL")

        assert result.final_context == ["relevant"]
        assert result.debug_info["total_retrieval_rounds"] == 1
        assert result.debug_info["retry_count"] == 0

    async def test_rewrite_failure_keeps_original_query(self, monkeypatch):
        """rewrite 失败 → 保原文 query + retry_count 自增"""
        class MockPipeline:
            async def search(self, query, filters=None):
                return [SearchResult(id=1, content="relevant", score=0.9, source="both")]

        async def fake_check(query, results):
            return False, "low_relevance"

        async def fake_rewrite(original_query, retry_reason, history):
            raise RuntimeError("rewrite API unavailable")

        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._check_sufficiency", fake_check
        )
        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._rewrite_query", fake_rewrite
        )

        service = RetrievalCheckService(pipeline=MockPipeline(), max_retries=1)
        result = await service.check_and_retrieve("python GIL")

        assert result.debug_info["total_retrieval_rounds"] == 2
        assert result.debug_info["retry_count"] == 1
        assert result.debug_info["rounds"][1]["query"] == "python GIL"

    async def test_max_retries_boundary_exactly_3_rounds_2_rewrites(self, monkeypatch):
        """check 恒 insufficient → 恰好 3 检索轮 + 2 重写"""
        class MockPipeline:
            async def search(self, query, filters=None):
                return [SearchResult(id=1, content="relevant", score=0.9, source="both")]

        async def fake_check(query, results):
            return False, "low_relevance"

        async def fake_rewrite(original_query, retry_reason, history):
            return original_query + "-r"

        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._check_sufficiency", fake_check
        )
        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._rewrite_query", fake_rewrite
        )

        service = RetrievalCheckService(pipeline=MockPipeline(), max_retries=2)
        result = await service.check_and_retrieve("python GIL")

        assert result.debug_info["total_retrieval_rounds"] == 3
        assert result.debug_info["retry_count"] == 2
        assert len(result.final_context) == 1  # 跨轮去重后仍 1 条
        assert result.final_context == ["relevant"]

    async def test_pipeline_exception_absorbed_per_round(self, monkeypatch):
        """pipeline.search 异常被按轮吸收 → 空结果，不抛、无 error 键"""
        class BrokenPipeline:
            async def search(self, query, filters=None):
                raise RuntimeError("DB down")

        async def fake_rewrite(original_query, retry_reason, history):
            return original_query + " rewritten"

        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._rewrite_query", fake_rewrite
        )

        service = RetrievalCheckService(pipeline=BrokenPipeline(), max_retries=1)
        result = await service.check_and_retrieve("python GIL")

        assert result.final_context == []
        assert result.debug_info["total_retrieval_rounds"] == 2
        assert "error" not in result.debug_info

    async def test_unexpected_loop_error_returns_error_debug_info(self, monkeypatch):
        """format 阶段意外异常 → 外层兜底 debug_info.error"""
        class MockPipeline:
            async def search(self, query, filters=None):
                return [SearchResult(id=1, content="relevant", score=0.9, source="both")]

        async def fake_check(query, results):
            return True, "ok"

        def fake_format(query, history, retry_count):
            raise RuntimeError("format bug")

        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._check_sufficiency", fake_check
        )
        monkeypatch.setattr(
            "app.workflows.retrieval_check.service._format_context", fake_format
        )

        service = RetrievalCheckService(pipeline=MockPipeline(), max_retries=2)
        result = await service.check_and_retrieve("python GIL")

        assert result.final_context == []
        assert result.debug_info["error"] == "format bug"


@pytest.mark.unit
class TestFormatContext:
    """_format_context — 跨轮去重 + score 降序 + debug_info"""

    def test_deduplicates_by_content_prefix(self):
        final_context, debug_info = _format_context(
            query="test",
            history=[
                {
                    "round": 0, "query": "test",
                    "results": [
                        {"content": "document A content", "score": 0.9},
                        {"content": "document B content", "score": 0.7},
                    ],
                },
                {
                    "round": 1, "query": "test rewritten",
                    "results": [
                        {"content": "document A content", "score": 0.85},
                        {"content": "document C content", "score": 0.8},
                    ],
                },
            ],
            retry_count=1,
        )
        assert len(final_context) == 3
        assert "document A content" in final_context
        assert "document B content" in final_context
        assert "document C content" in final_context
        # score 降序：A(0.9) > C(0.8) > B(0.7)
        assert final_context.index("document A content") == 0
        assert final_context.index("document C content") == 1
        assert final_context.index("document B content") == 2
        assert debug_info["total_unique_results"] == 3
        assert debug_info["total_retrieval_rounds"] == 2
        assert debug_info["retry_count"] == 1
        assert debug_info["final_query"] == "test"
        assert len(debug_info["rounds"]) == 2
```

- [ ] **Step 2: 运行新测试，确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_retrieval_check_service.py -v`
Expected: FAIL — `ImportError: cannot import name '_check_sufficiency' from 'app.workflows.retrieval_check.service'`（旧 service.py 尚无模块级辅助函数）。

- [ ] **Step 3: 重写 `service.py`**

整体覆盖 `app/workflows/retrieval_check/service.py`：

```python
"""RetrievalCheckService — self-check retrieval loop (plain async while-loop)

替代原 StateGraph 编排（graph.py/state.py/nodes 已删）：
retrieve → check → (insufficient + retry<max) rewrite → retrieve …
最多 max_retries 次重写，最终 format_context 跨轮去重 + 按分排序产出 final_context。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.llm.client import get_chat_llm
from app.llm.prompts import load_prompt
from app.retrieval import SearchResult
from app.retrieval.pipeline import RetrievalPipeline

logger = logging.getLogger(__name__)


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
    result: SufficiencyResult = await chain.ainvoke({
        "query": query,
        "result_count": str(len(results)),
        "retrieved_content": retrieved_content,
    })
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
    response = await chain.ainvoke({
        "query": original_query,
        "reason": retry_reason,
        "retrieved_content": retrieved_content,
    })
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
```

- [ ] **Step 4: 运行新测试，确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_retrieval_check_service.py -v`
Expected: PASS — 7 个用例全绿（2 个 TestSelfCheckRetrieve 的 format 用例 + 5 个循环用例 + 1 个 format 用例）。

- [ ] **Step 5: 删除 LangGraph 脚手架 + 旧测试，更新 `__init__.py`**

```bash
cd ai-interview-backend
rm app/workflows/retrieval_check/graph.py
rm app/workflows/retrieval_check/state.py
rm -r app/workflows/retrieval_check/nodes/
rm tests/unit/test_retrieval_check_nodes.py
```

`app/workflows/retrieval_check/__init__.py` 整体覆盖为：

```python
"""Retrieval self-check loop — plain async while-loop

retrieve → check_sufficiency → (insufficient + retry<max) rewrite → retrieve
最多 max_retries 次重写，最终 format_context 跨轮去重 + 按分排序产出 final_context。
实现见 service.py（原 graph.py/state.py/nodes 已折叠）。
"""
```

- [ ] **Step 6: 跑全量单元测试，确认无残留引用**

Run: `docker exec shilian-app pytest -m "unit" -q`
Expected: PASS — 原 35 个中 test_retrieval_check_nodes.py 的 5 个被新文件 7 个取代，净 +2 → 37 个全绿。

- [ ] **Step 7: 提交**

```bash
git add ai-interview-backend/app/workflows/retrieval_check/ ai-interview-backend/tests/unit/
git commit -m "refactor: fold retrieval_check StateGraph into async while-loop

Keep RetrievalCheckService class + RetrievalCheckResult contract. Delete
graph.py/state.py/nodes (~180 lines). Lock loop semantics with 7 new tests
(too_few short-circuit, LLM/rewrite failure fallbacks, max_retries=2 boundary,
per-round pipeline error absorption, format dedupe).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 修复 is_first_call 门控 + debug_info 存入 InterviewState

**Files:**
- Modify: `app/workflows/interview/service.py`
- Modify: `app/workflows/interview/state.py`
- Modify: `app/workflows/interview/nodes/retrieve_knowledge.py`
- Test: `tests/unit/test_interview_nodes.py`（追加 TestRetrieveKnowledgeNode）

- [ ] **Step 1: 追加 `retrieve_knowledge_node` 测试**

在 `tests/unit/test_interview_nodes.py` 末尾追加：

```python
@pytest.mark.unit
class TestRetrieveKnowledgeNode:
    """retrieve_knowledge_node — 委托 RetrievalCheckService，产出 knowledge_context + retrieval_debug"""

    async def test_returns_context_and_debug_info(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from app.workflows.interview.nodes.retrieve_knowledge import retrieve_knowledge_node

        fake_service = SimpleNamespace(
            check_and_retrieve=AsyncMock(return_value=SimpleNamespace(
                final_context=["知识片段1"],
                debug_info={"total_retrieval_rounds": 2, "retry_count": 1},
            ))
        )
        state = {"current_question": "什么是 GIL?"}
        config = {"configurable": {"retrieval_check_service": fake_service}}

        result = await retrieve_knowledge_node(state, config)
        assert result["knowledge_context"] == ["知识片段1"]
        assert result["retrieval_debug"] == {"total_retrieval_rounds": 2, "retry_count": 1}

    async def test_service_missing_returns_empty(self):
        from app.workflows.interview.nodes.retrieve_knowledge import retrieve_knowledge_node

        state = {"current_question": "什么是 GIL?"}
        config = {"configurable": {}}

        result = await retrieve_knowledge_node(state, config)
        assert result == {"knowledge_context": []}
```

- [ ] **Step 2: 运行新测试，确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_interview_nodes.py::TestRetrieveKnowledgeNode -v`
Expected: FAIL — `test_returns_context_and_debug_info` 断言 `result["retrieval_debug"]` 得 KeyError（node 尚不返回该字段）；`test_service_missing_returns_empty` 应通过。

- [ ] **Step 3: `state.py` 新增 `retrieval_debug` 字段**

`app/workflows/interview/state.py` 中"知识库"段（第 52-53 行）改为：

```python
    # ── 知识库（retrieve_knowledge 写入）──
    knowledge_context: list[str]
    retrieval_debug: Optional[dict]   # 自检检索 debug_info（轮次/重写 query/召回数）
```

- [ ] **Step 4: `retrieve_knowledge_node` 返回 debug_info**

`app/workflows/interview/nodes/retrieve_knowledge.py` 中 try 块（第 36-42 行）改为：

```python
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
```

- [ ] **Step 5: `service.py` 去掉 is_first_call 门控，无条件构建**

`app/workflows/interview/service.py` 中 config 组装（第 60-68 行）改为：

```python
    config = {
        "configurable": {
            "thread_id": f"interview-{interview_id}",
            "db": db,
            "retrieval_check_service": _build_retrieval_check_service(db),
        },
    }
```

`_build_retrieval_check_service`（第 127-152 行）整体改为：

```python
def _build_retrieval_check_service(session: AsyncSession):
    """Wire up RetrievalCheckService for hybrid RAG (Phase 3).

    每轮无条件构建：BM25 为 lifespan 预构建单例、pipeline 构造纯属性赋值，
    开销可忽略；每轮使用当前请求的新 session 更正确。修复原 is_first_call
    门控 bug —— HITL resume 后后续题目不再注入知识。
    """
    knowledge_bm25 = get_knowledge_bm25()
    if not knowledge_bm25 or not session:
        return None

    knowledge_pipeline = RetrievalPipeline(
        session=session,
        collection="knowledge_chunks",
        bm25_index=knowledge_bm25,
        vector_top_k=settings.VECTOR_TOP_K,
        bm25_top_k=settings.BM25_TOP_K,
        final_top_k=settings.KNOWLEDGE_TOP_K,
        enable_rerank=True,
    )
    return RetrievalCheckService(
        pipeline=knowledge_pipeline,
        max_retries=settings.SELF_CHECK_MAX_RETRIES,
    )
```

> 注意：`submit_answer` 中的 `is_first_call` 变量（`checkpointer.aget` 判断）**保留**，它仍用于区分 `ainvoke(initial_state)` 与 `ainvoke(Command(resume=...))`。

- [ ] **Step 6: 运行新增测试 + 全量单元测试**

Run: `docker exec shilian-app pytest tests/unit/test_interview_nodes.py -q`
Expected: PASS — 原 TestCheckFinished/TestScoreResult/TestInterviewState/TestEvaluateNode/TestExtractJson + 新增 2 个全部绿。

Run: `docker exec shilian-app pytest -m "unit" -q`
Expected: PASS — 全量绿。

- [ ] **Step 7: 提交**

```bash
git add ai-interview-backend/app/workflows/interview/ ai-interview-backend/tests/unit/test_interview_nodes.py
git commit -m "fix: build RetrievalCheckService per submit_answer + surface debug_info

Remove is_first_call gate (P1) so every HITL resume round injects knowledge.
Store self-check debug_info in InterviewState.retrieval_debug for observability.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 死代码清理 — _shared + 孤儿 prompt + embedding sync 变体

**Files:**
- Modify: `app/workflows/_shared/__init__.py`
- Delete: `app/workflows/_shared/llm.py`、`app/workflows/_shared/tools.py`
- Delete: `app/prompts/evaluate_answer.yaml`、`app/prompts/generate_report.yaml`、`app/prompts/question_agent.yaml`
- Modify: `app/llm/__init__.py`、`app/llm/embedding.py`

- [ ] **Step 1: 改 `_shared/__init__.py`（先改导出，再删文件，避免中间态 ImportError）**

`app/workflows/_shared/__init__.py` 整体覆盖为：

```python
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
```

- [ ] **Step 2: 删除两个空壳文件**

```bash
cd ai-interview-backend
rm app/workflows/_shared/llm.py app/workflows/_shared/tools.py
```

- [ ] **Step 3: 删除 3 个孤儿 prompt**

```bash
cd ai-interview-backend
rm app/prompts/evaluate_answer.yaml app/prompts/generate_report.yaml app/prompts/question_agent.yaml
```

- [ ] **Step 4: 改 `llm/__init__.py`（先改导出）**

`app/llm/__init__.py` 中 embedding import 段（第 11-16 行）改为：

```python
from app.llm.embedding import (
    embed_text,
    embed_texts,
)
```

`__all__`（第 25-26 行）删除 `"embed_text_sync",` 与 `"embed_texts_sync",` 两行。

- [ ] **Step 5: `embedding.py` 删除两个 sync 函数**

`app/llm/embedding.py` 删除 `embed_text_sync`（第 77-90 行）与 `embed_texts_sync`（第 93-113 行，含 `aembed_documents` 未 await 笔误）两个函数。保留 `get_embeddings` / `embed_text` / `embed_texts`。

- [ ] **Step 6: 验证全量单元测试 + grep 零残留**

Run: `docker exec shilian-app pytest -m "unit" -q`
Expected: PASS — 全量绿（现有测试均 mock 覆盖，无改动预期）。

```bash
cd ai-interview-backend
grep -rn "get_workflow_llm\|get_default_tools\|DEFAULT_TOOLS\|embed_text_sync\|embed_texts_sync" app/ tests/ | grep -v __pycache__ || echo "CLEAN"
grep -rln "evaluate_answer.yaml\|generate_report.yaml\|question_agent.yaml" app/ tests/ | grep -v __pycache__ || echo "CLEAN"
```
Expected: 两条都输出 `CLEAN`（__pycache__ 的 .pyc 可忽略）。

- [ ] **Step 7: import 冒烟（删导出后无 ImportError）**

Run: `docker exec shilian-app python -c "from app.workflows._shared import get_checkpointer; from app.llm import embed_text, load_prompt; print('import OK')"`
Expected: 输出 `import OK`。

- [ ] **Step 8: 提交**

```bash
git add ai-interview-backend/app/workflows/_shared/ ai-interview-backend/app/prompts/ ai-interview-backend/app/llm/
git commit -m "refactor: delete dead code — workflow_llm/tools, orphan prompts, sync embeddings

get_workflow_llm, get_default_tools/DEFAULT_TOOLS, embed_text_sync(s),
evaluate_answer/generate_report/question_agent.yaml all zero-callers.
Sync __init__.py exports first to avoid mid-refactor ImportError.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: rerank.py 事件循环阻塞修复

**Files:**
- Modify: `app/retrieval/rerank.py`
- Test: `tests/unit/test_retrieval.py`（现有 TestRerank 回归）

- [ ] **Step 1: 加 `import asyncio` + 卸线程**

`app/retrieval/rerank.py` 头部（第 3-5 行）改为：

```python
from __future__ import annotations

import asyncio
import logging
```

`cross_encoder_rerank` 中 `TextReRank.call`（第 38-44 行）改为：

```python
    try:
        resp = await asyncio.to_thread(
            TextReRank.call,
            model=model,
            query=query,
            documents=documents,
            top_n=top_k,
        )
    except Exception:
        logger.warning(
            "DashScope rerank API failed, falling back to original order",
            exc_info=True,
        )
        return candidates[:top_k]
```

> 行为等价（`asyncio.to_thread` 支持 kwargs；线程内异常传播回 await 点被 except 接住），仅消除单 worker 2GB 部署上的事件循环阻塞（对比 pipeline.py:85 BM25 已卸线程）。

- [ ] **Step 2: 运行 rerank 回归测试**

Run: `docker exec shilian-app pytest tests/unit/test_retrieval.py -q`
Expected: PASS — TestRerank 的 `test_reranks_by_api_results`（mock 正常返回）与 `test_graceful_fallback_on_api_error`（mock 抛异常 → 兜底）均绿。

- [ ] **Step 3: 提交**

```bash
git add ai-interview-backend/app/retrieval/rerank.py
git commit -m "fix: offload DashScope rerank to thread — unblock event loop

TextReRank.call is sync SDK; running it directly in async cross_encoder_rerank
blocks the loop (single-worker 2GB deploy). await asyncio.to_thread(...).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 全量验证 + 文档同步

**Files:**
- Modify: `CLAUDE.md`（待办段标记 Topic 2/3 完成）

- [ ] **Step 1: 全量单元测试**

Run: `docker exec shilian-app pytest -m "unit" -q`
Expected: PASS — 全量绿（37（Task 1 后）+ 2（Task 2 新增）= 39 个用例）。

- [ ] **Step 2: import 冒烟（关键模块链）**

Run: `docker exec shilian-app python -c "import app.workflows.interview.graph; import app.workflows.retrieval_check.service; import app.retrieval.pipeline; import app.llm; print('graph + retrieval_check + pipeline + llm import OK')"`
Expected: 输出 `graph + retrieval_check + pipeline + llm import OK`。

- [ ] **Step 3: 集合检查（pytest 收集全模块 import）**

Run: `docker exec shilian-app pytest --collect-only -q | tail -5`
Expected: 无 ImportError / collection error，正常列出测试。

- [ ] **Step 4: 更新 CLAUDE.md**

在「待办 — 2026-08-01 头脑风暴产出」段的 Topic 2 / Topic 3 条目与「审计核实问题清单」中相关 P1/P2 条目，标记 `[x]` 并注明本 spec / plan 引用；在「已完成」段新增一行本次改造摘要。

- [ ] **Step 5: 提交**

```bash
git add -f CLAUDE.md
git commit -m "docs: CLAUDE.md marks Topic 2/3 done — retrieval_check fold + dead code cleanup

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 自审记录

- **Spec 覆盖**：Topic 2 全部覆盖（2.2 服务重写 → Task 1、2.4 门控修复 → Task 2 Step 5、2.5 debug_info → Task 2 Step 3-4）；Topic 3 六项全覆盖（Task 3 Step 1-5 + Task 4）；测试 7+2 用例（Task 1 Step 1 + Task 2 Step 1）；grep/import/全量验证（Task 3 Step 6-7 + Task 5）。
- **Placeholder 扫描**：所有代码步骤含完整代码，无 TBD/TODO。
- **类型一致性**：`_check_sufficiency` / `_rewrite_query` / `_format_context` 为 `service.py` 模块级函数，测试 monkeypatch 路径与实现一致；`RetrievalCheckResult` / `check_and_retrieve` 契约未变；`_build_retrieval_check_service` 从 2 参变 1 参，唯一调用点 Task 2 Step 5 已同步。
