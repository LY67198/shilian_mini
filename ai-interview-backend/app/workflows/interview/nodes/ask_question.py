"""ask_question node — 推进题目索引 + 存下一题消息到 DB

双模：
- 预生成模式（上传页）：questions_data 已含全部题，按索引取下一题。
- 渐进模式（岗位匹配入口）：questions_data 不足时，调用 ai_service.generate_next_question_stream
  现场生成下一题并落库；节点内 chain.astream 的 token 由 astream_to_sse 的
  on_chat_model_stream 事件拾取（metadata.langgraph_node == "ask_question" → question_chunk）。
"""
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import Resume
from app.repositories.interview_repo import interview_repo
from app.services.backoffice.question_bank_service import question_bank_service
from app.services.client.ai_service import ai_service
from app.services.client.interview_service import interview_service
from app.workflows.interview.state import InterviewState

logger = logging.getLogger(__name__)


async def ask_question_node(state: InterviewState, config: RunnableConfig) -> dict:
    """推进 current_index 并把下一题作为 InterviewMessage 落库（用于 SSE 流式推送首条事件）。

    Args:
        state: 当前 InterviewState，需含 interview_id / user_id / current_index / questions。
        config: RunnableConfig，configurable 中需含 db (AsyncSession)。

    Returns:
        写入 state 的字典，含 current_index / current_question / next_question / index。

    Raises:
        RuntimeError: 面试记录不存在、题目索引越界或下一题生成失败。
    """
    db: AsyncSession = config["configurable"]["db"]

    interview_id = state["interview_id"]
    user_id = state["user_id"]
    current_index = state["current_index"]
    next_index = current_index + 1

    interview = await interview_repo.get_by_id_for_user(db, interview_id, user_id)
    if not interview:
        raise RuntimeError(f"面试记录不存在: id={interview_id}")
    questions = interview.questions_data or []

    if next_index < len(questions):
        # ── 预生成模式（上传页，现状保留）──
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

    # ── 渐进模式（岗位匹配入口）：现场生成下一题 ──
    # 越界保护：渐进生成也不应超过目标题数（check_finished 已保证 next_index < total，
    # 此处防御性兜底，避免在写 DB 前抛出）
    if next_index >= (interview.total_questions or 0):
        raise RuntimeError(
            f"题目索引越界: next_index={next_index}, total={interview.total_questions}"
        )

    resume = await db.get(Resume, interview.resume_id)
    try:
        parsed_resume = json.loads(resume.parsed_content) if resume and resume.parsed_content else {}
    except json.JSONDecodeError:
        parsed_resume = {}

    candidates = await interview_service._prepare_questions(
        db=db,
        parsed_resume=parsed_resume,
        target_position=interview.target_position,
        difficulty=interview.difficulty or "medium",
        total_questions=interview.total_questions,
    )
    used_bank_ids = [q.get("bank_id") for q in questions if q.get("bank_id")]

    question = None
    async for kind, payload in ai_service.generate_next_question_stream(
        candidates=candidates,
        parsed_resume=parsed_resume,
        target_position=interview.target_position or "",
        difficulty=interview.difficulty or "medium",
        current_index=next_index,
        total_questions=interview.total_questions,
        used_bank_ids=used_bank_ids,
        chat_history=state.get("chat_history", []),
    ):
        if kind == "result":
            question = payload

    if question is None or not question.get("question"):
        raise RuntimeError("下一题生成失败")

    question["index"] = next_index
    questions = list(interview.questions_data or [])
    questions.append(question)
    interview.questions_data = questions
    interview.current_question_index = next_index
    await interview_repo.create_message(
        db, interview_id, role="interviewer",
        content=question["question"], question_index=next_index,
    )
    bank_ids = [question["bank_id"]] if question.get("bank_id") else []
    if bank_ids:
        await question_bank_service.increment_use_count(db, bank_ids)
    await db.commit()

    return {
        "current_index": next_index,
        "current_question": question["question"],
        "next_question": question["question"],
        "index": next_index,
    }
