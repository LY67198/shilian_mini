"""ask_question node — 推进题目索引 + 存下一题消息到 DB"""
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_repo import interview_repo
from app.workflows.interview.state import InterviewState

logger = logging.getLogger(__name__)


async def ask_question_node(state: InterviewState, config: RunnableConfig) -> dict:
    """推进 current_index 并把下一题作为 InterviewMessage 落库（用于 SSE 流式推送首条事件）。

    Args:
        state: 当前 InterviewState，需含 interview_id / current_index / questions。
        config: RunnableConfig，configurable 中需含 db (AsyncSession)。

    Returns:
        写入 state 的字典，含 current_index / current_question / next_question / index。

    Raises:
        RuntimeError: 题目索引越界。
    """
    db: AsyncSession = config["configurable"]["db"]

    interview_id = state["interview_id"]
    current_index = state["current_index"]
    questions = state["questions"]
    next_index = current_index + 1

    if next_index >= len(questions):
        raise RuntimeError(
            f"题目索引越界: next_index={next_index}, total={len(questions)}"
        )

    await interview_repo.update_question_index(db, interview_id, next_index)

    next_question = questions[next_index]["question"]
    await interview_repo.create_message(
        db, interview_id, role="interviewer",
        content=next_question, question_index=next_index,
    )
    await db.commit()

    return {
        "current_index": next_index,
        "current_question": next_question,
        "next_question": next_question,
        "index": next_index,
    }
