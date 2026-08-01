"""
单元测试 — start_interview_stream SSE 事件序列（status → chunk → done）

运行：
  pytest tests/test_start_interview_stream.py -m "unit"
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.client import interview_service as mod


def _parse_sse_blocks(sse_events):
    """把 _sse 产出的多行字符串块解析成 (event, data_dict) 列表。"""
    parsed = []
    for block in sse_events:
        lines = block.strip().splitlines()
        event = lines[0].replace("event: ", "").strip()
        data_line = next((l for l in lines if l.startswith("data: ")), "")
        data = json.loads(data_line.replace("data: ", "", 1))
        parsed.append((event, data))
    return parsed


@pytest.mark.unit
class TestStartInterviewStream:
    @staticmethod
    def _make_db_mock():
        resume = SimpleNamespace(status="completed", parsed_content='{"skills": ["Python"]}')

        class _Scalar:
            def scalar_one_or_none(self):
                return resume

        db = AsyncMock()
        db.execute.return_value = _Scalar()
        # db.add 是同步调用，AsyncMock 会返回未 await 的协程 → 置为普通函数避免 RuntimeWarning
        db.add = lambda obj: None

        async def fake_refresh(obj):
            obj.id = 42

        db.refresh = fake_refresh
        return db

    async def test_stream_sequence_and_done_payload(self, monkeypatch):
        candidates = [
            {
                "id": 1, "question": "讲下 Python 的 GIL",
                "reference_answer": "GIL 是全局解释器锁...", "key_points": ["GIL"],
                "difficulty": "medium", "position_tag": "python_backend",
                "similarity": 0.9, "source": "from_bank",
            }
        ]

        async def fake_prepare(self, **kwargs):
            # 补在 InterviewService 类上 → 调用时被绑定 self
            return candidates

        async def fake_stream(**kwargs):
            yield ("token", '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "bank_id": 1')
            yield ("result", [{
                "question": "讲下 Python 的 GIL（微调）", "bank_id": 1,
                "reference_answer": "GIL 是全局解释器锁...", "source": "from_bank",
            }])

        from app.services.client.ai_service import ai_service
        monkeypatch.setattr(mod.InterviewService, "_prepare_questions", fake_prepare)
        monkeypatch.setattr(ai_service, "select_and_adapt_questions_stream", fake_stream)
        monkeypatch.setattr(mod.question_bank_service, "increment_use_count", AsyncMock())

        svc = mod.InterviewService()
        sse_events = [s async for s in svc.start_interview_stream(
            db=self._make_db_mock(),
            user_id=1,
            resume_id=1,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=1,
        )]
        parsed = _parse_sse_blocks(sse_events)

        assert [e for e, _ in parsed] == ["status", "chunk", "done"]
        assert parsed[0][1] == {"message": "正在检索题库..."}
        assert parsed[1][1]["content"] == '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "bank_id": 1'
        done = parsed[2][1]
        assert done["interview_id"] == 42
        assert done["first_question"] == "讲下 Python 的 GIL（微调）"
        assert done["question_index"] == 0
        assert done["total_questions"] == 1

    async def test_fallback_branch_yields_status_then_done(self, monkeypatch):
        # 题库为空 → 纯 AI 生成兜底（无 chunk），仅 status + done
        async def fake_prepare(self, **kwargs):
            # 补在 InterviewService 类上 → 调用时被绑定 self
            return []

        async def fake_generate(**kwargs):
            return [{"question": "Q1", "source": "ai_fallback", "bank_id": None}]

        from app.services.client.ai_service import ai_service
        monkeypatch.setattr(mod.InterviewService, "_prepare_questions", fake_prepare)
        monkeypatch.setattr(ai_service, "generate_questions", fake_generate)
        monkeypatch.setattr(mod.question_bank_service, "increment_use_count", AsyncMock())

        svc = mod.InterviewService()
        sse_events = [s async for s in svc.start_interview_stream(
            db=self._make_db_mock(),
            user_id=1,
            resume_id=1,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=1,
        )]
        parsed = _parse_sse_blocks(sse_events)

        assert [e for e, _ in parsed] == ["status", "done"]
        assert parsed[1][1]["first_question"] == "Q1"
