"""客户端面试 API — 启动面试 / 提交回答（SSE 流式评分）/ 获取报告 / 消息 / 列表 / 删除

核心端点：
- POST /start — 创建面试会话
- POST /{id}/answer — 提交回答，支持 ?stream=true 走 SSE
- GET /{id}/report — 获取面试评估报告
- GET /{id}/messages — 获取全部对话消息
- GET "" — 当前用户的所有面试记录
- DELETE /{id} — 删除面试
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.client.deps import get_current_user
from app.db.session import get_db
from app.deps import get_interview_service
from app.exceptions.http_exceptions import NotFoundError
from app.models.user import User
from app.repositories.interview_repo import interview_repo
from app.schemas.client.interview import InterviewStart, AnswerSubmit
from app.schemas.response import ApiResponse
from app.services.client.interview_service import InterviewService
from app.workflows.interview.service import submit_answer as submit_answer_to_graph

router = APIRouter()


async def _assert_owned_active(
    db: AsyncSession, user_id: int, interview_id: int
) -> None:
    """校验面试归属权 + 进行中状态。

    Args:
        db: 数据库会话。
        user_id: 当前用户 ID。
        interview_id: 面试主键。

    Raises:
        NotFoundError: 面试不存在、不属于当前用户、或已 completed。
    """
    interview = await interview_repo.get_by_id_for_user(db, interview_id, user_id)
    if not interview or interview.status != "in_progress":
        raise NotFoundError(message="面试记录不存在")


@router.post("/start")
async def start_interview(
    data: InterviewStart,
    stream: bool = Query(default=False, description="是否使用 SSE 流式出题"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    """开始新的 AI 面试会话（?stream=true 走 SSE 流式出题）"""
    if stream:
        async def event_generator():
            async for sse_str in interview_service.start_interview_stream(
                db=db,
                user_id=current_user.id,
                resume_id=data.resume_id,
                target_position=data.target_position,
                difficulty=data.difficulty,
                total_questions=data.total_questions,
            ):
                yield sse_str

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    result = await interview_service.start_interview(
        db=db,
        user_id=current_user.id,
        resume_id=data.resume_id,
        target_position=data.target_position,
        difficulty=data.difficulty,
        total_questions=data.total_questions,
    )
    return ApiResponse.success(data=result)


@router.post("/{interview_id}/answer")
async def submit_answer(
    interview_id: int,
    data: AnswerSubmit,
    stream: bool = Query(default=False, description="是否使用 SSE 流式返回"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交当前题目的回答（?stream=true 启动 SSE 流式评分）"""
    # P1 修复：先校验归属权 + 进行中状态，再写库（此前越权消息会被持久化）
    await _assert_owned_active(db, current_user.id, interview_id)

    if not stream:
        async for result in submit_answer_to_graph(
            db=db,
            user_id=current_user.id,
            interview_id=interview_id,
            answer=data.answer,
            stream=False,
        ):
            return ApiResponse.success(data=result)

    async def event_generator():
        async for sse_str in submit_answer_to_graph(
            db=db,
            user_id=current_user.id,
            interview_id=interview_id,
            answer=data.answer,
            stream=True,
        ):
            yield sse_str

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{interview_id}/report")
async def get_report(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    """获取面试评估报告"""
    result = await interview_service.get_report(
        db=db,
        user_id=current_user.id,
        interview_id=interview_id,
    )
    return ApiResponse.success(data=result)


@router.get("/{interview_id}/messages")
async def get_messages(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    """获取面试的所有对话消息"""
    result = await interview_service.get_interview_messages(
        db=db,
        user_id=current_user.id,
        interview_id=interview_id,
    )
    return ApiResponse.success(data=result)


@router.get("")
async def get_interviews(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    """获取当前用户的所有面试记录"""
    result = await interview_service.get_interviews(
        db=db,
        user_id=current_user.id,
    )
    return ApiResponse.success(data=result)


@router.delete("/{interview_id}")
async def delete_interview(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    """删除面试记录"""
    result = await interview_service.delete_interview(
        db=db,
        user_id=current_user.id,
        interview_id=interview_id,
    )
    return ApiResponse.success(data=result)
