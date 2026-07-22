"""向量相似度换算（pgvector L2 → 0-1 similarity）

DashScope text-embedding-v3 输出已归一化，L2 distance 与 cosine 等价：
  cos_sim = 1 - L2_distance² / 2 ；这里用线性近似 1 - d/√2。
"""
from __future__ import annotations

import math

EMBEDDING_DIM = 1024


def l2_distance_to_similarity(distance: float) -> float:
    """L2 distance → 0-1 similarity（前提：embedding 已归一化）。

    - distance=0  → 1.0（完全匹配）
    - distance=√2 → 0.0（正交）
    """
    return round(1.0 - distance / math.sqrt(2), 4)
