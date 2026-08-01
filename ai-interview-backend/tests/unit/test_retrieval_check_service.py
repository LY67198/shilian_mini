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
