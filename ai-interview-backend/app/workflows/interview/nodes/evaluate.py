"""evaluate node — prompt | llm.bind(json_object) astream 真流式评分并落库"""
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.json_utils import extract_json
from app.llm import get_chat_llm
from app.llm.prompts import load_prompt
from app.models.interview_message import InterviewMessage
from app.workflows.interview.state import InterviewState, ScoreResult

logger = logging.getLogger(__name__)


async def evaluate_node(state: InterviewState, config: RunnableConfig) -> dict:
    """Evaluate candidate answer and persist score to InterviewMessage.

    Uses prompt | llm.bind(response_format={"type": "json_object"}) + astream + extract_json
    for structured scoring.
    After evaluation, writes score/feedback/question_index back to the latest
    unscored candidate message so generate_report_node can find them via
    get_scored_messages().

    Args:
        state: Current InterviewState. Must contain current_question / answer /
            resume_context / chat_history; optional reference_answer / key_points /
            knowledge_context.
        config: RunnableConfig with configurable.db (AsyncSession).

    Returns:
        Dict with score / feedback for LangGraph state update.
    """
    # Build conversation history text（排除当前答案消息，避免与 {answer} 重复注入）
    chat_history = state.get("chat_history", [])
    if (
        chat_history
        and chat_history[-1].get("role") == "candidate"
        and chat_history[-1].get("content") == state.get("answer")
    ):
        chat_history = chat_history[:-1]
    history_text = ""
    for msg in chat_history[-6:]:
        role = "面试官" if msg.get("role") == "interviewer" else "候选人"
        history_text += f"{role}: {msg.get('content', '')}\n"

    # Reference material block
    reference_answer = state.get("reference_answer")
    key_points = state.get("key_points")
    ref_block = ""
    if reference_answer:
        ref_block += f"\n【参考答案要点（评分依据，不要直接读给候选人）】：\n{reference_answer}\n"
    if key_points:
        ref_block += f"\n【关键采分点】：{json.dumps(key_points, ensure_ascii=False)}\n"

    knowledge_context = state.get("knowledge_context", [])
    kb_block = ""
    if knowledge_context:
        kb_block = (
            "\n【相关知识库片段（评分参考，不要直接读给候选人）】：\n"
            + "\n---\n".join(knowledge_context)
            + "\n"
        )

    scoring_hint = (
        "评分时请对照【参考答案要点】与【相关知识库片段】，候选人答中要点越多分越高。\n"
        if (ref_block or kb_block)
        else ""
    )

    variables = {
        "question": state.get("current_question", ""),
        "answer": state.get("answer", ""),
        "resume_json": json.dumps(state.get("resume_context", {}), ensure_ascii=False),
        "history_text": history_text,
        "ref_block": ref_block,
        "kb_block": kb_block,
        "scoring_hint": scoring_hint,
    }

    try:
        prompt = load_prompt("evaluator_agent")
        llm = get_chat_llm(temperature=0.3, streaming=True)
        structured_llm = llm.bind(response_format={"type": "json_object"})
        chain = prompt | structured_llm

        chunks: list[str] = []
        async for chunk in chain.astream(variables):
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                chunks.append(text)

        parsed = extract_json("".join(chunks))
        result = ScoreResult(
            score=float(parsed.get("score", 5.0)),
            feedback=str(parsed.get("feedback", "")),
            follow_up=bool(parsed.get("follow_up", False)),
        )
    except Exception as e:
        logger.error(f"Structured scoring failed, returning fallback: {e}")
        return {"score": 5.0, "feedback": f"评分异常，已记录: {str(e)[:100]}"}

    # Persist score to the latest unscored candidate message
    db: AsyncSession = config["configurable"]["db"]
    try:
        stmt = (
            select(InterviewMessage)
            .where(
                InterviewMessage.interview_id == state["interview_id"],
                InterviewMessage.role == "candidate",
                InterviewMessage.score.is_(None),
            )
            .order_by(InterviewMessage.id.desc())
            .limit(1)
        )
        result_set = await db.execute(stmt)
        msg = result_set.scalar_one_or_none()
        if msg is not None:
            msg.score = result.score
            msg.feedback = result.feedback
            msg.question_index = state.get("current_index", 0)
            await db.commit()
            logger.debug(
                "Score persisted: interview=%d msg=%d score=%.1f",
                state["interview_id"],
                msg.id,
                result.score,
            )
        else:
            logger.warning(
                "No unscored candidate message found for interview=%d",
                state["interview_id"],
            )
    except Exception as e:
        logger.error("Failed to persist score to InterviewMessage: %s", e)
        return {"score": result.score, "feedback": result.feedback, "persist_failed": True}

    return {"score": result.score, "feedback": result.feedback}
