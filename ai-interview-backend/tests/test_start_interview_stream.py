"""
单元测试 — start_interview_stream SSE 事件序列（status → chunk → done）

运行：
  pytest tests/test_start_interview_stream.py -m "unit"
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import Interview, InterviewMessage
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

    async def test_error_event_after_status_when_prepare_raises(self, monkeypatch):
        """_prepare_questions 抛异常 → status 后 yield error，前端不卡死。"""
        async def fake_prepare_raises(self, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod.InterviewService, "_prepare_questions", fake_prepare_raises)

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

        assert [e for e, _ in parsed] == ["status", "error"]
        assert parsed[0][1] == {"message": "正在检索题库..."}
        assert "message" in parsed[1][1]
        assert "boom" in parsed[1][1]["message"]

    async def test_validation_failure_yields_only_error_event(self):
        """简历不存在 → 不 yield status，只 yield error（message 含 简历不存在）。"""
        class _ScalarNone:
            def scalar_one_or_none(self):
                return None

        db = AsyncMock()
        db.execute.return_value = _ScalarNone()
        db.add = lambda obj: None

        svc = mod.InterviewService()
        sse_events = [s async for s in svc.start_interview_stream(
            db=db,
            user_id=1,
            resume_id=999,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=1,
        )]
        parsed = _parse_sse_blocks(sse_events)

        assert [e for e, _ in parsed] == ["error"]
        assert "简历不存在" in parsed[0][1]["message"]


@pytest.mark.unit
class TestStartInterviewFastCreate:
    """start_interview(generate_questions=False) — 快建：不产出题、不写首条消息"""

    @staticmethod
    def _make_db_mock():
        resume = SimpleNamespace(status="completed", parsed_content='{"skills": ["Python"]}')

        class _Scalar:
            def scalar_one_or_none(self):
                return resume

        db = AsyncMock()
        db.execute.return_value = _Scalar()
        # 捕获 db.add 的对象列表，供断言 Interview.questions_data / 不写 InterviewMessage
        db.added = []
        db.add = lambda obj: db.added.append(obj)

        async def fake_refresh(obj):
            obj.id = 99

        db.refresh = fake_refresh
        return db

    async def test_fast_create_skips_generation_and_message(self, monkeypatch):
        gen_mock = AsyncMock()
        monkeypatch.setattr(mod.InterviewService, "_generate_questions_with_rag", gen_mock)

        db = self._make_db_mock()
        svc = mod.InterviewService()
        result = await svc.start_interview(
            db=db,
            user_id=1,
            resume_id=1,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=3,
            generate_questions=False,
        )

        gen_mock.assert_not_awaited()
        assert result["interview_id"] == 99
        assert result["first_question"] is None
        assert result["question_index"] == 0
        assert result["total_questions"] == 3

        # 快建：只落库 1 条 Interview（题目为空、题号从 0 开始），不写任何 InterviewMessage
        interviews = [o for o in db.added if isinstance(o, Interview)]
        messages = [o for o in db.added if isinstance(o, InterviewMessage)]
        assert len(interviews) == 1
        assert len(messages) == 0
        interview = interviews[0]
        assert interview.questions_data == []
        assert interview.current_question_index == 0

    async def test_generate_by_default_runs_rag_and_writes_first_message(self, monkeypatch):
        gen_mock = AsyncMock(return_value=[{"question": "Q0", "bank_id": 1}])
        monkeypatch.setattr(mod.InterviewService, "_generate_questions_with_rag", gen_mock)

        inc_mock = AsyncMock()
        monkeypatch.setattr(mod.question_bank_service, "increment_use_count", inc_mock)

        db = self._make_db_mock()
        svc = mod.InterviewService()
        result = await svc.start_interview(
            db=db,
            user_id=1,
            resume_id=1,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=3,
        )

        gen_mock.assert_awaited_once()
        assert result["first_question"] == "Q0"

        # 默认路径：题库选中题目累加 use_count（bank_ids=[1]）
        inc_mock.assert_awaited_once_with(db, [1])

        # 默认路径：Interview 带题目，且写首条 InterviewMessage
        interviews = [o for o in db.added if isinstance(o, Interview)]
        messages = [o for o in db.added if isinstance(o, InterviewMessage)]
        assert len(interviews) == 1
        assert len(messages) == 1
        assert interviews[0].questions_data == [{"question": "Q0", "bank_id": 1}]
        assert messages[0].role == "interviewer"
        assert messages[0].content == "Q0"
        assert messages[0].question_index == 0
