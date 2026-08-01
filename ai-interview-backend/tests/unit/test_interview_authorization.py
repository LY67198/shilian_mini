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


@pytest.mark.unit
class TestAnswerEndpointAuthorization:
    """POST /interviews/{id}/answer — 越权请求返回 404 且不写库"""

    async def test_unauthorized_returns_404_no_write(self):
        import httpx
        from fastapi import FastAPI

        from app.api.client.deps import get_current_user
        from app.api.client.v1 import interview as interview_api
        from app.db.session import get_db
        from app.exceptions.http_exceptions import APIException
        from app.schemas.response import ApiResponse

        # 最小 app：只挂载被测路由 + APIException handler
        app = FastAPI()
        app.include_router(interview_api.router, prefix="/api/v1/interviews")

        @app.exception_handler(APIException)
        async def api_exception_handler(request, exc):
            return ApiResponse.failed(
                message=exc.detail,
                body_code=exc.code,
                http_code=exc.status_code,
                data=exc.data,
            )

        # 攻击者身份 + mock 会话
        attacker = AsyncMock()
        attacker.id = 1
        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_current_user] = lambda: attacker
        app.dependency_overrides[get_db] = override_get_db

        # 归属校验：interview_id=99 不属于 user 1 → get_by_id_for_user 返回 None
        with patch.object(
            interview_repo, "get_by_id_for_user", new=AsyncMock(return_value=None)
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/interviews/99/answer",
                    json={"answer": "越权注入消息"},
                )

        assert resp.status_code == 404
        # 越权时不应写入 InterviewMessage（不触发 db.add / db.commit）
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()
