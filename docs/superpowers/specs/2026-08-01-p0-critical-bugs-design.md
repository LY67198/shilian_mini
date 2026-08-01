# P0 Critical Bug Fixes — Design Doc

**日期**：2026-08-01  
**状态**：Approved  
**范围**：仅修复 P0-1（报告评分事实性错误）、P0-2（BM25 索引未构建）、P0-2b（BM25 id 错位导致 RRF 融合失效）

---

## 背景

2026-08-01 架构评审（5 路并行代码审计）发现两个 P0 级生产 bug，直接影响面试报告数据正确性和 RAG 知识注入有效性。本文档记录修复设计。

## P0-1 — 报告评分事实性错误

### 根因

`evaluate_node`（`app/workflows/interview/nodes/evaluate.py`）通过 `with_structured_output(ScoreResult)` 计算了评分，但仅将 `{score, feedback}` 返回到 LangGraph state，**从不写回 `InterviewMessage` 表**。

`generate_report_node` → `interview_repo.get_scored_messages()` 查询条件是 `score IS NOT NULL`，但全仓库只有 `InterviewMessage` 的 **创建**（`service.py:41-48`），没有**更新** score 的代码。结果：`get_scored_messages()` 恒为空列表。

```python
# generate_report_node.py:39 — msg_by_idx 为空
msg_by_idx = {m.question_index: m for m in scored_msgs}  # scored_msgs = []

# generate_report_node.py:42-48 — 兜底逻辑
score_val = m.score if m else state.get("score", 0)  
# ↑ m 永远为 None（scored_msgs 为空），走 else 分支
# state.get("score") 是 LangGraph state 中 evaluate_node 返回的值
# 但 state 在每轮被覆盖 → 拿到的是**最后一题**的分数
answer_text = m.content if m else (state.get("answer", "") if i == state.get("current_index", 0) else "未回答")
# ↑ 只有 i == current_index 时能拿到 answer，其余全部 "未回答"
```

**影响**：面试报告中，每道题的分数都是最后一题的分数，除最后一题外所有答案显示为"未回答"。

### 修复

`evaluate_node` 评分成功后，找到该面试最新一条未评分的 candidate 消息，写入 `score`、`feedback`、`question_index`。

**文件**：`app/workflows/interview/nodes/evaluate.py`

**改动**：在 `chain.ainvoke(variables)` 成功后（第 73 行之后），添加：

```python
# 评分写回 InterviewMessage
from sqlalchemy import select
from app.models.interview_message import InterviewMessage

db: AsyncSession = config["configurable"]["db"]
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
```

**不改动** `service.py` 中 `question_index=-1` 的逻辑（创建消息时还不知道 `current_index`，需在 evaluate 阶段补写）。

---

## P0-2 — BM25 索引从未构建

### 根因

`build_bm25_indices()` 在 `app/retrieval/bm25_lifecycle.py` 中有完整实现，但全仓库 **零调用**。`lifespan()` 启动流程（`app/route/route.py:43-62`）未挂载此函数。

模块级单例 `knowledge_bm25` / `question_bank_bm25` 恒为 `None`，导致：

```python
# service.py:136-138
knowledge_bm25 = get_knowledge_bm25()  # → None
if not knowledge_bm25 or not session:
    return None  # ← RetrievalCheckService 从未创建
```

`RetrievalCheckService` 永远为 `None` → `retrieve_knowledge_node` 中知识库检索完全跳过 → 面试过程中的知识注入为空。

### 修复

在 `lifespan()` 启动阶段，创建临时 DB session，调用 `build_bm25_indices()`。

**文件**：`app/route/route.py`

**改动**：在 `setup_logging()` 之后、`yield` 之前，添加：

```python
# 构建 BM25 关键词索引（混合检索必需）
from app.db.base import get_session_local
from app.retrieval.bm25_lifecycle import build_bm25_indices

_session_local = get_session_local()
async with _session_local() as db:
    await build_bm25_indices(db)
logger.info("BM25 indices built successfully")
```

---

## P0-2b — BM25 语料下标 ≠ PG 主键

### 根因

`BM25Index.build()` 按列表顺序存储文本，`search()` 返回 `(corpus_index, score)` —— corpus_index 是 0, 1, 2... 的列表位置。但 `vector_search()` 返回的 `SearchResult.id` 是 PostgreSQL 主键（如 42, 57, 103）。

`rrf_fuse()` 按 `id` 字段融合同一条结果的多个来源得分。当 BM25 结果的 `id=0` 而 vector 结果的 `id=42` 时，它们永远不会被识别为同一文档的"both"命中，RRF 融合形同虚设。

```python
# pipeline.py:88-95 — BM25 结果以 corpus_index 为 id
bm25_results = [
    SearchResult(id=idx, ...)  # idx = 0, 1, 2... 不是 PG 主键
    for idx, score in bm25_raw
]
```

### 修复

`BM25Index` 内部同时存储 PG 主键和文本，`search()` 返回 PG 主键而非语料下标。

**文件 1**：`app/retrieval/bm25.py`

改动：
- `build()` 签名从 `build(texts: list[str])` 改为 `build(items: list[tuple[int, str]])`，内部维护 `_ids: list[int]`
- `search()` 返回 `(pg_id, score)` 而非 `(corpus_index, score)`
- `get_text()` 按 pg_id 查找

```python
class BM25Index:
    def __init__(self, collection_name: str) -> None:
        self._collection_name = collection_name
        self._corpus: list[str] = []
        self._ids: list[int] = []        # 新增：PG 主键列表
        self._index: Optional[BM25Okapi] = None

    def build(self, items: list[tuple[int, str]] | None) -> None:
        """items: list of (pg_id, text)"""
        if not items:
            self._corpus, self._ids, self._index = [], [], None
            return
        self._ids = [item[0] for item in items]
        self._corpus = [item[1] for item in items]
        tokenized = [self._tokenize(t) for t in self._corpus]
        self._index = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        if self._index is None:
            return []
        tokenized_query = self._tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        scored = [(self._ids[i], float(s)) for i, s in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def get_text(self, pg_id: int) -> str:
        try:
            idx = self._ids.index(pg_id)
            return self._corpus[idx]
        except ValueError:
            return ""
```

**文件 2**：`app/retrieval/bm25_lifecycle.py`

改动：`build_bm25_indices()` 传 `(id, text)` 对。

```python
# Knowledge index
result = await db_session.execute(
    select(KnowledgeChunk.id, KnowledgeChunk.content)
)
knowledge_items = [(row[0], row[1]) for row in result.fetchall() if row[1]]

# Question bank index
result = await db_session.execute(
    select(QuestionBank.id, QuestionBank.question, QuestionBank.reference_answer)
)
q_items = []
for qid, question, answer in result.fetchall():
    text = f"{question or ''} {answer or ''}".strip()
    if text:
        q_items.append((qid, text))
```

---

## 影响范围汇总

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `app/workflows/interview/nodes/evaluate.py` | +12 行 | 评分写回 InterviewMessage |
| `app/route/route.py` | +6 行 | lifespan 调用 build_bm25_indices |
| `app/retrieval/bm25.py` | ~15 行改动 | 存储 (pg_id, text) 对，search 返回 pg_id |
| `app/retrieval/bm25_lifecycle.py` | ~10 行改动 | SELECT id + 传 (id, text) 对 |

- **无新增文件**
- **无 API 变更**
- **无数据库迁移**
- **无测试 regression**（现有测试不依赖这些路径）
