"""check_finished node — 判断是否面试结束（纯函数，不调 DB/LLM/pgvector）"""
from __future__ import annotations

from app.workflows.interview.state import InterviewState


async def check_finished_node(state: InterviewState) -> dict:
    """比较 current_index 和目标题数，判断是否结束面试（纯函数）。

    渐进模式按 total_questions 判断（面试题逐题生成，questions 长度不断增长）；
    预生成路径 total_questions == len(questions_data)，行为与旧逻辑一致。

    Args:
        state: 当前 InterviewState，需含 current_index / total_questions / questions。

    Returns:
        写入 state 的字典，含 is_finished 布尔值。
    """
    current_index = state.get("current_index", 0)
    total_questions = state.get("total_questions")
    if total_questions is None:
        total_questions = len(state.get("questions", []))
    is_finished = current_index + 1 >= total_questions
    return {"is_finished": is_finished}
