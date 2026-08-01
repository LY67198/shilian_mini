"""Retrieval self-check loop — plain async while-loop

retrieve → check_sufficiency → (insufficient + retry<max) rewrite → retrieve
最多 max_retries 次重写，最终 format_context 跨轮去重 + 按分排序产出 final_context。
实现见 service.py（原 graph.py/state.py/nodes 已折叠）。
"""
