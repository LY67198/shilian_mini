"""/answer 端点越权注入修复 — 单元测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions.http_exceptions import NotFoundError
from app.repositories.interview_repo import interview_repo


class MockInterview:
    """最小 Interview 实例，仅含辅助函数校验用到的字段。"""

    def __init__(self, status: str = "in_progress"):
        self.status = status


@pytest.mark.unit
class TestAssertOwnedActive:
    """_assert_owned_active — 归属权 + 进行中状态校验"""

    async def test_not_found_raises_404(self):
        from app.api.client.v1.interview import _assert_owned_active

        with patch.object(
            interview_repo, "get_by_id_for_user", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(NotFoundError):
                await _assert_owned_active(AsyncMock(), user_id=1, interview_id=99)

    async def test_completed_raises_404(self):
        from app.api.client.v1.interview import _assert_owned_active

        with patch.object(
            interview_repo,
            "get_by_id_for_user",
            new=AsyncMock(return_value=MockInterview(status="completed")),
        ):
            with pytest.raises(NotFoundError):
                await _assert_owned_active(AsyncMock(), user_id=1, interview_id=99)

    async def test_in_progress_passes(self):
        from app.api.client.v1.interview import _assert_owned_active

        with patch.object(
            interview_repo,
            "get_by_id_for_user",
            new=AsyncMock(return_value=MockInterview(status="in_progress")),
        ):
            # 不抛异常即通过
            await _assert_owned_active(AsyncMock(), user_id=1, interview_id=99)
