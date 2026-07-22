"""面试 StateGraph 6 节点 — fetch_context / retrieve_knowledge / evaluate / check_finished / ask_question / generate_report

节点纯函数：接收 state dict，调用 repo / agent / retrieval，返回部分 state 字段更新。
HITL 由 graph.interrupt_after=["ask_question"] 暂停；resume 后从 ask_question → fetch_context 重走流水线。
"""
from app.workflows.interview.nodes.fetch_context import fetch_context_node
from app.workflows.interview.nodes.retrieve_knowledge import retrieve_knowledge_node
from app.workflows.interview.nodes.evaluate import evaluate_node
from app.workflows.interview.nodes.check_finished import check_finished_node
from app.workflows.interview.nodes.ask_question import ask_question_node
from app.workflows.interview.nodes.generate_report import generate_report_node

__all__ = [
    "fetch_context_node",
    "retrieve_knowledge_node",
    "evaluate_node",
    "check_finished_node",
    "ask_question_node",
    "generate_report_node",
]
