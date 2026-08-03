"""
单元测试 — /interviews/start?stream=true 路由 SSE 分支

运行：
  pytest tests/test_start_stream_route.py -m "unit"
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.client.v1.interview import start_interview


@pytest.mark.unit
class TestStartStreamRoute:
    DATA = SimpleNamespace(
        resume_id=1, target_position="Python 后端",
        difficulty="medium", total_questions=3,
    )

    async def test_stream_true_returns_streaming_response(self):
        async def fake_gen(**kwargs):
            yield "event: done\ndata: {}\n\n"

        svc = SimpleNamespace(start_interview_stream=fake_gen)
        resp = await start_interview(
            data=self.DATA,
            stream=True,
            current_user=SimpleNamespace(id=1),
            db=AsyncMock(),
            interview_service=svc,
        )

        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"
        assert resp.headers["X-Accel-Buffering"] == "no"

        # SSE 数据原样转发：body_iterator 产出的字符串与 service generator 完全一致
        chunks = [s async for s in resp.body_iterator]
        assert chunks == ["event: done\ndata: {}\n\n"]

    async def test_stream_false_returns_api_response(self):
        svc = SimpleNamespace(start_interview=AsyncMock(return_value={"interview_id": 1}))
        resp = await start_interview(
            data=self.DATA,
            stream=False,
            current_user=SimpleNamespace(id=1),
            db=AsyncMock(),
            interview_service=svc,
        )

        # ApiResponse.success() 返回 JSONResponse（非 ApiResponse 实例）
        assert isinstance(resp, JSONResponse)
        assert not isinstance(resp, StreamingResponse)
        svc.start_interview.assert_awaited_once()


@pytest.mark.unit
class TestNextQuestionRoute:
    async def test_returns_streaming_response(self):
        from app.api.client.v1.interview import generate_next_question

        async def fake_gen(**kwargs):
            yield "event: status\ndata: {\"message\": \"正在检索题库...\"}\n\n"
            yield "event: done\ndata: {\"index\": 0, \"question\": \"Q0\"}\n\n"

        svc = SimpleNamespace(generate_next_question_stream=fake_gen)
        resp = await generate_next_question(
            interview_id=1,
            current_user=SimpleNamespace(id=1),
            db=AsyncMock(),
            interview_service=svc,
        )

        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"
        assert resp.headers["X-Accel-Buffering"] == "no"

        chunks = [s async for s in resp.body_iterator]
        assert len(chunks) == 2
        assert "question\": \"Q0" in chunks[1]
