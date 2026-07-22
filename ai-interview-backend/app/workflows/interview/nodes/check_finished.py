"""check_finished node — 判断是否面试结束（纯函数，不调 DB/LLM/pgvector）"""
from __future__ import annotations

from app.workflows.interview.state import InterviewState


async def check_finished_node(state: InterviewState) -> dict:
    """比较 current_index 和 total_questions，判断是否结束面试（纯函数）。

    Args:
        state: 当前 InterviewState，需含 current_index / total_questions。

    Returns:
        写入 state 的字典，含 is_finished 布尔值。
    """
    current_index = state.get("current_index", 0)
    total_questions = state.get("total_questions", 0)
    is_finished = current_index + 1 >= total_questions
    return {"is_finished": is_finished}
