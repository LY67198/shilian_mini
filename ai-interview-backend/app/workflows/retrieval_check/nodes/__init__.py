"""检索自检 4 节点 — retrieve / check_sufficiency / rewrite_query / format_context

自检循环：retrieve → check_sufficiency → (insufficient + retry<2) rewrite_query → retrieve 再次
最多 2 次重试，最终 format_context 跨轮去重 + 按分排序产出 final_context。
"""
from app.workflows.retrieval_check.nodes.retrieve import retrieve_node
from app.workflows.retrieval_check.nodes.check_sufficiency import check_sufficiency_node
from app.workflows.retrieval_check.nodes.rewrite_query import rewrite_query_node
from app.workflows.retrieval_check.nodes.format_context import format_context_node

__all__ = [
    "retrieve_node",
    "check_sufficiency_node",
    "rewrite_query_node",
    "format_context_node",
]
