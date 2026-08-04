"""
单元测试 — _prepare_questions 的 rerank 相关性阈值过滤

背景：题库未覆盖的岗位（医学等非 IT custom 岗位）经"放松回退"仍会从题库捞回不相关题
（rerank 相关性≈0.006），导致医学岗位面试出计算机题。修复：最终候选按
QUESTION_BANK_RERANK_MIN_SCORE 阈值过滤，低相关候选被剔除 → 调用方走纯 AI 生成兜底。

运行：
  pytest tests/unit/test_prepare_questions_threshold.py -m "unit"
"""
from types import SimpleNamespace

import pytest

import app.retrieval.pipeline as pipeline_mod
from app.retrieval import SearchResult
from app.services.client.interview_service import InterviewService


def _mk_result(qid: int, question: str, score: float, tag: str = "python_backend"):
    """构造一个带 position_tag 元数据的 SearchResult（simulate pipeline 输出）。"""
    return SearchResult(
        id=qid,
        content=question,
        score=score,
        metadata={
            "question": question,
            "reference_answer": "参考答案",
            "key_points": ["要点"],
            "difficulty": "medium",
            "position_tag": tag,
        },
        source="rerank",
    )


@pytest.mark.unit
class TestPrepareQuestionsThreshold:
    @staticmethod
    def _patch_pipeline(monkeypatch, results):
        """把 RetrievalPipeline.search 替换为固定返回，并让 BM25 索引非空走 pipeline 分支。"""

        class FakePipeline:
            def __init__(self, *args, **kwargs):
                pass

            async def search(self, query, filters=None):
                return results

        monkeypatch.setattr(pipeline_mod, "RetrievalPipeline", FakePipeline)
        monkeypatch.setattr(
            "app.retrieval.bm25_lifecycle.get_question_bank_bm25",
            lambda: SimpleNamespace(),  # truthy → 走 pipeline 分支
        )

    async def test_low_relevance_candidates_filtered_out(self, monkeypatch):
        """题库无对应岗位时：低相关（0.006）候选被剔除，仅保留高相关（0.9）候选。"""
        self._patch_pipeline(monkeypatch, [
            _mk_result(1, "Python 的 GIL 是什么？它对多线程程序有什么影响？", 0.9),
            _mk_result(2, "JVM 内存模型和 GC 算法", 0.006),
        ])
        svc = InterviewService()
        candidates = await svc._prepare_questions(
            db=None, parsed_resume={"skills": ["静脉采血", "ELISA"]},
            target_position="医学检验技术员", difficulty="medium", total_questions=1,
        )
        assert [c["id"] for c in candidates] == [1]
        assert candidates[0]["question"] == "Python 的 GIL 是什么？它对多线程程序有什么影响？"

    async def test_all_low_relevance_returns_empty(self, monkeypatch):
        """全部候选相关性≈0 → 返回空列表，调用方走纯 AI 生成兜底（Branch B）。"""
        self._patch_pipeline(monkeypatch, [
            _mk_result(1, "JVM 内存模型和 GC 算法", 0.006),
            _mk_result(2, "Vue Router 的路由懒加载是怎么实现的？", 0.006),
        ])
        svc = InterviewService()
        candidates = await svc._prepare_questions(
            db=None, parsed_resume={"skills": ["静脉采血", "ELISA"]},
            target_position="医学检验技术员", difficulty="medium", total_questions=2,
        )
        assert candidates == []

    async def test_relevant_candidates_kept(self, monkeypatch):
        """题库有对应岗位（IT）：高相关候选全部保留，不受阈值误伤。"""
        self._patch_pipeline(monkeypatch, [
            _mk_result(1, "Python 的 GIL 是什么？", 0.18),
            _mk_result(2, "asyncio 的事件循环如何工作？", 0.16),
            _mk_result(3, "装饰器的原理是什么？", 0.15),
        ])
        svc = InterviewService()
        candidates = await svc._prepare_questions(
            db=None, parsed_resume={"skills": ["Python", "FastAPI", "Redis"]},
            target_position="Python后端开发工程师", difficulty="medium", total_questions=2,
        )
        assert [c["id"] for c in candidates] == [1, 2, 3]
