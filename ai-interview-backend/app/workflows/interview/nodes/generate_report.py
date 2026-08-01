"""generate_report node — 使用 prompt | llm.with_structured_output 生成报告"""
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import get_chat_llm
from app.llm.prompts import load_prompt
from app.repositories.interview_repo import interview_repo
from app.workflows.interview.state import InterviewState, ReportResult

logger = logging.getLogger(__name__)


async def generate_report_node(state: InterviewState, config: RunnableConfig) -> dict:
    """汇总所有评分，使用 prompt | llm.with_structured_output(ReportResult) 生成报告并写入 DB。

    Args:
        state: 当前 InterviewState，需含 interview_id / questions；可选 resume_context / target_position。
        config: RunnableConfig，configurable 中需含 db。

    Returns:
        写入 state 的字典，含 overall_score / report / all_scores / is_finished。
    """
    db: AsyncSession = config["configurable"]["db"]

    interview_id = state["interview_id"]
    questions = state["questions"]
    resume_context = state.get("resume_context", {})
    target_position = state.get("target_position", "")

    scored_msgs = await interview_repo.get_scored_messages(db, interview_id)

    qa_data = []
    all_scores = []
    msg_by_idx = {m.question_index: m for m in scored_msgs}

    for i, q in enumerate(questions):
        m = msg_by_idx.get(i)
        score_val = float(m.score) if m and m.score else 0.0
        answer_text = (
            m.content
            if m
            else (state.get("answer", "") if i == state.get("current_index", 0) else "未回答")
        )
        qa_data.append({
            "question": q["question"],
            "answer": answer_text,
            "score": score_val,
            "feedback": m.feedback if m and m.feedback else "",
        })
        all_scores.append(score_val)

    overall = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0

    qa_text = _build_qa_text(qa_data)

    try:
        prompt = load_prompt("report_agent")
        llm = get_chat_llm(temperature=0.5)
        structured_llm = llm.with_structured_output(ReportResult, method="json_mode")
        chain = prompt | structured_llm
        result: ReportResult = await chain.ainvoke({
            "resume_json": json.dumps(resume_context, ensure_ascii=False),
            "target_position": target_position,
            "qa_text": qa_text,
        })
        report = {
            "summary": result.summary,
            "strengths": result.strengths,
            "weaknesses": result.weaknesses,
            "suggestions": result.suggestions,
            "hire_recommendation": result.hire_recommendation,
        }
    except Exception as e:
        logger.error(f"报告生成失败: {e}")
        report = {
            "summary": "报告生成失败",
            "strengths": [],
            "weaknesses": [],
            "suggestions": [],
            "hire_recommendation": "",
        }

    report["question_scores"] = [
        {"question": qa["question"], "score": qa["score"], "feedback": qa["feedback"]}
        for qa in qa_data
    ]

    await interview_repo.update_result(db, interview_id, overall, report)
    await db.commit()

    return {
        "overall_score": overall,
        "report": report,
        "all_scores": qa_data,
        "is_finished": True,
    }


def _build_qa_text(qa_data: list[dict]) -> str:
    """将问答数据构建为格式化文本块，用于注入 prompt。

    Args:
        qa_data: 问答数据列表，每项包含 question / answer / score 字段。

    Returns:
        格式化的问答文本。
    """
    text = ""
    for item in qa_data:
        text += (
            f"问题：{item.get('question', '')}\n"
            f"回答：{item.get('answer', '未回答')}\n"
            f"得分：{item.get('score', 'N/A')}\n\n"
        )
    return text
