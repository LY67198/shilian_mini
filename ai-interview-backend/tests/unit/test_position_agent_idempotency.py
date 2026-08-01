"""start_mock_interview 幂等性单元测试"""
from __future__ import annotations

import pytest


class MockActiveInterview:
    """构造一个 in_progress 面试，模拟 get_active_by_position 命中。"""

    id = 42
    total_questions = 5
    questions_data = [{"question": "讲讲 Python 的 GIL", "difficulty": "medium"}]


@pytest.mark.unit
class TestGetActiveByPosition:
    """InterviewRepository.get_active_by_position — 查询进行中面试"""

    async def test_returns_active_matching_interview(self):
        from app.repositories.interview_repo import interview_repo

        active = MockActiveInterview()
        captured = {}

        class MockResult:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

        class MockSession:
            async def execute(self, stmt):
                captured["stmt"] = stmt
                return MockResult(active)

        result = await interview_repo.get_active_by_position(
            MockSession(), user_id=1, resume_id=2, target_position="Python 后端开发"
        )

        assert result is active
        assert result.id == 42
        # 过滤条件必须包含 user/resume/position + status=in_progress
        sql = str(captured["stmt"])
        assert "interviews.user_id" in sql
        assert "interviews.resume_id" in sql
        assert "interviews.target_position" in sql
        assert "interviews.status" in sql
        assert "in_progress" in captured["stmt"].compile().params.values()
        # 取最新一条（多份时返回 id 最大者）
        assert "DESC" in sql.upper()
        assert captured["stmt"].compile().params.get("param_1") == 1

    async def test_returns_none_when_no_match(self):
        from app.repositories.interview_repo import interview_repo

        class MockResult:
            def scalar_one_or_none(self):
                return None

        class MockSession:
            async def execute(self, stmt):
                return MockResult()

        result = await interview_repo.get_active_by_position(
            MockSession(), user_id=1, resume_id=2, target_position="Python 后端开发"
        )

        assert result is None


class MockResume:
    user_id = 1
    status = "completed"


class MockTemplate:
    position_tag = "python_backend"
    title = "Python 后端开发"
    is_active = True
    recommended_difficulty = "medium"
    recommended_question_count = 5


class MockSessionContext:
    """模拟 `async with get_session_local()() as db`。"""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MockDb:
    async def get(self, model, pk):
        return MockResume()


@pytest.mark.unit
class TestStartMockInterviewIdempotency:
    """start_mock_interview — 同岗位已有进行中面试则幂等返回"""

    def _patch_tool_deps(self, monkeypatch, *, active):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            "app.services.client.position_agent_tools.get_session_local",
            lambda: lambda: MockSessionContext(MockDb()),
        )
        monkeypatch.setattr(
            "app.services.client.position_agent_tools.position_template_service.get_by_tag",
            AsyncMock(return_value=MockTemplate()),
        )
        monkeypatch.setattr(
            "app.services.client.position_agent_tools.interview_repo.get_active_by_position",
            AsyncMock(return_value=active),
        )
        start_mock = AsyncMock()
        monkeypatch.setattr(
            "app.services.client.interview_service.interview_service.start_interview",
            start_mock,
        )
        return start_mock

    async def test_returns_existing_interview_without_creating(self, monkeypatch):
        from app.services.client.position_agent_tools import start_mock_interview

        start_mock = self._patch_tool_deps(monkeypatch, active=MockActiveInterview())

        result = await start_mock_interview.ainvoke({
            "resume_id": 1,
            "position_tag": "python_backend",
        })

        assert result["existing"] is True
        assert result["interview_id"] == 42
        assert result["position_tag"] == "python_backend"
        assert result["total_questions"] == 5
        assert result["first_question"] == "讲讲 Python 的 GIL"
        assert result["question_index"] == 0
        # 幂等路径不得触发新建面试
        start_mock.assert_not_called()

    async def test_creates_interview_when_no_active(self, monkeypatch):
        from app.services.client.position_agent_tools import start_mock_interview

        start_mock = self._patch_tool_deps(monkeypatch, active=None)
        start_mock.return_value = {
            "interview_id": 7,
            "first_question": "新问题",
            "question_index": 0,
            "total_questions": 5,
        }

        result = await start_mock_interview.ainvoke({
            "resume_id": 1,
            "position_tag": "python_backend",
        })

        assert "existing" not in result
        assert result["interview_id"] == 7
        assert result["position_tag"] == "python_backend"
        assert result["first_question"] == "新问题"
        start_mock.assert_awaited_once()
