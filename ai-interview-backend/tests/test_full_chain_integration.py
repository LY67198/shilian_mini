"""RAG + Agent 全真链路集成测试（需真实 API + 种子数据）

运行：
  docker exec -e RUN_FULL_CHAIN=1 shilian-app \
    pytest tests/test_full_chain_integration.py -v

注意：消耗真实 token（embedding + rerank + DeepSeek LLM）。
"""
import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("RUN_FULL_CHAIN"),
        reason="需 RUN_FULL_CHAIN=1 才运行全真链路测试",
    ),
]


@pytest.fixture(autouse=True)
async def _ensure_bm25_indices():
    """确保 BM25 全局单例已构建。

    BM25 索引由 app lifespan 构建，但 pytest 进程不执行 lifespan，
    因此测试前需手动构建（只读 DB，无 API 消耗；已构建则跳过）。
    """
    from app.retrieval.bm25_lifecycle import (
        build_bm25_indices,
        get_knowledge_bm25,
        get_question_bank_bm25,
    )

    if get_knowledge_bm25() is None or get_question_bank_bm25() is None:
        from app.db.base import get_session_local

        async with get_session_local()() as session:
            await build_bm25_indices(session)
    yield


@pytest.mark.asyncio
class TestRagFullPipeline:
    """RetrievalPipeline.search 完整混合检索链路（vector + BM25 + RRF + rerank）"""

    async def test_knowledge_hybrid_search(self):
        from app.db.base import get_session_local
        from app.retrieval.bm25_lifecycle import get_knowledge_bm25
        from app.retrieval.pipeline import RetrievalPipeline

        assert get_knowledge_bm25() is not None, "BM25 索引未构建（lifespan 未执行？）"
        async with get_session_local()() as session:
            pipeline = RetrievalPipeline(
                session=session,
                collection="knowledge_chunks",
                bm25_index=get_knowledge_bm25(),
                final_top_k=5,
                enable_rerank=True,
            )
            results = await pipeline.search("Python GIL 是什么")
            assert results, "知识库检索不应为空"
            assert len(results) <= 5
            for r in results:
                assert r.id > 0
                assert r.content
                assert r.source in {"vector", "bm25", "both"}
                assert r.score > 0
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)

    async def test_question_bank_hybrid_search_with_filters(self):
        from app.db.base import get_session_local
        from app.retrieval.bm25_lifecycle import get_question_bank_bm25
        from app.retrieval.pipeline import RetrievalPipeline

        assert get_question_bank_bm25() is not None
        async with get_session_local()() as session:
            pipeline = RetrievalPipeline(
                session=session,
                collection="question_bank",
                bm25_index=get_question_bank_bm25(),
                final_top_k=8,
                enable_rerank=True,
            )
            results = await pipeline.search(
                "Python 异步编程",
                filters={"position_tag": "python_backend", "difficulty": "medium", "min_score": 0.7},
            )
            assert results, "题库检索不应为空"
            for r in results:
                assert r.metadata, "题库结果应携带 metadata（reference_answer 等）"


@pytest.mark.asyncio
class TestRetrievalCheckLoop:
    """RetrievalCheckService.check_and_retrieve 自检循环（sufficiency + rewrite）"""

    async def test_check_and_retrieve_returns_context(self):
        from app.db.base import get_session_local
        from app.retrieval.bm25_lifecycle import get_knowledge_bm25
        from app.retrieval.pipeline import RetrievalPipeline
        from app.workflows.retrieval_check.service import RetrievalCheckService

        async with get_session_local()() as session:
            pipeline = RetrievalPipeline(
                session=session,
                collection="knowledge_chunks",
                bm25_index=get_knowledge_bm25(),
                final_top_k=5,
                enable_rerank=True,
            )
            service = RetrievalCheckService(pipeline, max_retries=1)
            result = await service.check_and_retrieve("什么是 Python GIL？")
            assert result.final_context, "final_context 不应为空"
            assert result.debug_info
            assert "rounds" in result.debug_info
            assert result.debug_info["total_retrieval_rounds"] >= 1


@pytest.mark.asyncio
class TestPositionAgentFullChain:
    """PositionAgentService.run_agent 驱动 5 工具全链（含副作用清理）"""

    RESUME_ID = 11  # 已完成简历，解析内容最完整

    async def test_agent_full_chain(self):
        from sqlalchemy import text

        from app.db.base import get_session_local
        from app.services.client.position_agent_service import position_agent_service

        created_interview_ids: list[int] = []
        try:
            response = await position_agent_service.run_agent(
                resume_id=self.RESUME_ID,
                target_direction="Python 后端",
            )
            result = response.get("result", {})
            assert "error" not in result, f"Agent 报错: {result.get('error')}"

            assert "interview_result" in result, "最终输出应含 interview_result"
            ir = result["interview_result"]
            assert ir.get("interview_id"), "interview_result 应含 interview_id"
            created_interview_ids.append(ir["interview_id"])
            assert ir.get("first_question"), "interview_result 应含 first_question"
            assert ir.get("total_questions", 0) > 0

            steps = response.get("intermediate_steps", [])
            tool_names = [s["tool"] for s in steps]
            for expected in ["get_parsed_resume", "build_candidate_profile", "match_positions"]:
                assert expected in tool_names, f"Agent 应调用 {expected}"
        finally:
            if created_interview_ids:
                async with get_session_local()() as session:
                    for iid in created_interview_ids:
                        await session.execute(
                            text("DELETE FROM interview_messages WHERE interview_id = :iid"),
                            {"iid": iid},
                        )
                        await session.execute(
                            text("DELETE FROM interviews WHERE id = :iid"),
                            {"iid": iid},
                        )
                    await session.commit()
