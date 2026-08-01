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
