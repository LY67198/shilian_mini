# P0 Critical Bug Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three P0 production bugs — report scoring factual error (P0-1), BM25 indices never built (P0-2), BM25 corpus-index/id mismatch (P0-2b).

**Architecture:** Four files changed, zero new files. P0-2b refactors BM25Index internal storage to carry PG primary keys alongside text, enabling RRF fusion. P0-2 wires build_bm25_indices into the FastAPI lifespan. P0-1 adds a DB write-back in evaluate_node so scores persist to InterviewMessage.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.x async, rank-bm25, FastAPI lifespan

---

## File Map

| File | Role | Change |
|------|------|--------|
| `app/retrieval/bm25.py` | BM25 keyword index | Store `(pg_id, text)` pairs; search returns PG ids |
| `app/retrieval/bm25_lifecycle.py` | Build & expose singleton indices | SELECT id column; pass `(id, text)` pairs to BM25Index |
| `app/route/route.py` | FastAPI lifespan | Call `build_bm25_indices()` at startup |
| `app/workflows/interview/nodes/evaluate.py` | Score candidate answers | Write score/feedback back to InterviewMessage |
| `tests/unit/test_retrieval.py` | Existing BM25/pipeline tests | Update `build()` calls to new `(id, text)` API |

---

### Task 1: Refactor BM25Index to store (pg_id, text) pairs

**Files:**
- Modify: `app/retrieval/bm25.py` (full file)

- [ ] **Step 1: Rewrite BM25Index with PG id support**

Replace the full file content:

```python
"""BM25 keyword index with character bigram tokenizer"""
from __future__ import annotations

from typing import Optional

from rank_bm25 import BM25Okapi


class BM25Index:
    """BM25 keyword search index using character bigram tokenization.

    Stores (pg_id, text) pairs so that search results carry PostgreSQL
    primary keys, enabling correct RRF fusion with vector search results.

    Usage::

        idx = BM25Index("my_collection")
        idx.build([(1, "text one"), (2, "text two")])
        results = idx.search("query text", top_k=20)
        # results: [(pg_id, score), ...]
    """

    def __init__(self, collection_name: str) -> None:
        """Initialize BM25 index instance.

        Args:
            collection_name: Collection name for logging / debugging.
        """
        self._collection_name = collection_name
        self._corpus: list[str] = []
        self._ids: list[int] = []
        self._index: Optional[BM25Okapi] = None

    def build(self, items: list[tuple[int, str]] | None) -> None:
        """Build BM25Okapi index from (pg_id, text) pairs.

        Args:
            items: List of (pg_id, text) pairs. None or empty list clears the index.
        """
        if not items:
            self._corpus = []
            self._ids = []
            self._index = None
            return

        self._ids = [item[0] for item in items]
        self._corpus = [item[1] for item in items]
        tokenized = [self._tokenize(t) for t in self._corpus]
        self._index = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """Search the index and return top_k (pg_id, score) pairs.

        Args:
            query: Raw query string.
            top_k: Maximum number of results.

        Returns:
            List of (pg_id, bm25_score) sorted by score descending.
            Empty list if the index is not built.
        """
        if self._index is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._index.get_scores(tokenized_query)

        # Pair (pg_id, score) and sort by score descending
        scored = [(self._ids[i], float(s)) for i, s in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]

    def get_text(self, pg_id: int) -> str:
        """Get original corpus text by PG primary key.

        Args:
            pg_id: PostgreSQL primary key of the document.

        Returns:
            Original text string, or empty string if pg_id is not found.
        """
        try:
            idx = self._ids.index(pg_id)
            return self._corpus[idx]
        except ValueError:
            return ""

    @property
    def corpus_size(self) -> int:
        """Return the number of documents in the corpus."""
        return len(self._corpus)

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into character bigrams.

        For text shorter than 2 characters, returns the characters as-is
        to avoid producing an empty token list that would break BM25Okapi.

        Args:
            text: Raw text to tokenize.

        Returns:
            List of character bigram tokens.
        """
        chars = list(text)
        if len(chars) < 2:
            return chars
        return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
```

- [ ] **Step 2: Commit**

```bash
git add app/retrieval/bm25.py
git commit -m "fix: BM25Index stores (pg_id, text) pairs, search returns PG ids

P0-2b: corpus index was used as SearchResult.id, which never matched
vector search PG primary keys in RRF fusion. Now search() returns PG ids
so the 'both' source in RRF correctly identifies documents found by both methods.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Update bm25_lifecycle to pass (id, text) pairs

**Files:**
- Modify: `app/retrieval/bm25_lifecycle.py:15-51`

- [ ] **Step 1: Update build_bm25_indices to SELECT id and pass (id, text) pairs**

Read the current file, then replace the function body (lines 24-51) with:

```python
async def build_bm25_indices(db_session) -> None:
    """Build two BM25 indices from PostgreSQL data (called at app startup).

    Loads KnowledgeChunk.(id, content) and QuestionBank.(id, question, reference_answer),
    builds global singletons with PG primary keys for correct RRF fusion.

    Args:
        db_session: AsyncSession provided by the startup caller.
    """
    global knowledge_bm25, question_bank_bm25

    from sqlalchemy import select
    from app.models.knowledge import KnowledgeChunk
    from app.models.question_bank import QuestionBank

    # Build knowledge index — SELECT id + content
    result = await db_session.execute(
        select(KnowledgeChunk.id, KnowledgeChunk.content)
    )
    knowledge_items = [(row[0], row[1]) for row in result.fetchall() if row[1]]
    knowledge_bm25 = BM25Index("knowledge_chunks")
    knowledge_bm25.build(knowledge_items)

    # Build question bank index — SELECT id + question + reference_answer
    result = await db_session.execute(
        select(QuestionBank.id, QuestionBank.question, QuestionBank.reference_answer)
    )
    q_items = []
    for qid, question, answer in result.fetchall():
        text = f"{question or ''} {answer or ''}".strip()
        if text:
            q_items.append((qid, text))
    question_bank_bm25 = BM25Index("question_bank")
    question_bank_bm25.build(q_items)

    logger.info(
        "BM25 indices built: knowledge=%d, question_bank=%d",
        knowledge_bm25.corpus_size,
        question_bank_bm25.corpus_size,
    )
```

- [ ] **Step 2: Commit**

```bash
git add app/retrieval/bm25_lifecycle.py
git commit -m "fix: build_bm25_indices passes (id, text) pairs to BM25Index

P0-2b companion: SELECTs the PG primary key alongside text content so
BM25 search results carry real database ids for RRF fusion.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Update existing tests for new BM25 API

**Files:**
- Modify: `tests/unit/test_retrieval.py:92-111` (TestBM25Index)
- Modify: `tests/unit/test_retrieval.py:200-239` (TestRetrievalPipeline)

- [ ] **Step 1: Update TestBM25Index tests**

Replace lines 92-111 with:

```python
@pytest.mark.unit
class TestBM25Index:
    def test_build_and_search(self):
        from app.retrieval.bm25 import BM25Index
        idx = BM25Index("test")
        idx.build([(10, "Python async programming guide"), (20, "Java concurrency patterns"), (30, "Python web framework Django")])
        results = idx.search("python async", top_k=2)
        assert len(results) == 2
        # results are (pg_id, score) — first result should be id=10 (best match)
        pg_ids = [r[0] for r in results]
        assert 10 in pg_ids

    def test_empty_corpus_returns_empty(self):
        from app.retrieval.bm25 import BM25Index
        idx = BM25Index("empty")
        assert idx.search("query", top_k=5) == []

    def test_build_none_clears_index(self):
        from app.retrieval.bm25 import BM25Index
        idx = BM25Index("test")
        idx.build([(1, "some text")])
        assert idx.corpus_size == 1
        idx.build(None)
        assert idx.corpus_size == 0
        assert idx.search("text") == []

    def test_get_text_by_pg_id(self):
        from app.retrieval.bm25 import BM25Index
        idx = BM25Index("test")
        idx.build([(42, "hello world"), (99, "goodbye")])
        assert idx.get_text(42) == "hello world"
        assert idx.get_text(99) == "goodbye"
        assert idx.get_text(999) == ""  # not found

    def test_tokenize_bigrams(self):
        from app.retrieval.bm25 import BM25Index
        idx = BM25Index("test")
        tokens = idx._tokenize("hello world")
        assert len(tokens) > 0
```

- [ ] **Step 2: Run BM25 tests to verify they fail with old code (or pass with new)**

```bash
docker exec shilian-app pytest tests/unit/test_retrieval.py::TestBM25Index -v
```

Expected after Task 1+2: PASS (5 tests)

- [ ] **Step 3: Update TestRetrievalPipeline — fix build() calls**

Replace `bm25.build(["doc one", "doc two", ...])` with `bm25.build([(1, "doc one"), (2, "doc two"), ...])` in two places:

**test_pipeline_calls_stages_in_order** (line 206):
```python
bm25.build([(1, "doc one"), (2, "doc two"), (3, "doc three")])
```

**test_pipeline_without_rerank** (line 231):
```python
bm25.build([(1, "doc one"), (2, "doc two")])
```

- [ ] **Step 4: Run all retrieval tests**

```bash
docker exec shilian-app pytest tests/unit/test_retrieval.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_retrieval.py
git commit -m "test: update BM25/pipeline tests for (pg_id, text) API

P0-2b companion: adapt build() calls and add tests for get_text(),
build(None) clearing, and search returning PG ids.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Wire build_bm25_indices into FastAPI lifespan

**Files:**
- Modify: `app/route/route.py:43-62`

- [ ] **Step 1: Add BM25 build call in lifespan**

Replace the lifespan function (lines 42-62) with:

```python
@asynccontextmanager
async def lifespan(application: FastAPI):
    """FastAPI application lifecycle hook.

    Startup: initialize logging, build BM25 keyword indices.
    Shutdown: close logging (master process only), release DB engine,
    Redis connections, and email thread pool.
    """
    # ── Startup ──
    setup_logging()
    logger.info("Application starting up")

    # Build BM25 keyword indices for hybrid RAG retrieval
    try:
        from app.db.base import get_session_local
        from app.retrieval.bm25_lifecycle import build_bm25_indices

        _session_local = get_session_local()
        async with _session_local() as db:
            await build_bm25_indices(db)
        logger.info("BM25 indices built successfully")
    except Exception:
        logger.warning("BM25 index build failed — hybrid retrieval will be vector-only", exc_info=True)

    yield  # ── Application running ──

    # ── Shutdown ──
    if is_master_process():
        shutdown_logging()
    await close_db_engine()
    await redis_client.close()
    thread_pool_service.shutdown()
    logger.info("Application shutting down")
```

- [ ] **Step 2: Verify the import chain doesn't break**

```bash
docker exec shilian-app python -c "from app.route.route import lifespan; print('lifespan imported OK')"
```

Expected: `lifespan imported OK`

- [ ] **Step 3: Commit**

```bash
git add app/route/route.py
git commit -m "fix: build BM25 indices in FastAPI lifespan startup

P0-2: build_bm25_indices() existed but was never called. Now invoked
at app startup with a temporary DB session. Wrapped in try/except so
a BM25 build failure does not prevent app startup — hybrid retrieval
gracefully degrades to vector-only.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Write score/feedback back to InterviewMessage in evaluate_node

**Files:**
- Modify: `app/workflows/interview/nodes/evaluate.py:16-77`

- [ ] **Step 1: Add score write-back logic after successful evaluation**

Read the current file, then replace the full file content with:

```python
"""evaluate node — use prompt | llm.with_structured_output to score and persist to DB"""
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import get_chat_llm
from app.llm.prompts import load_prompt
from app.models.interview_message import InterviewMessage
from app.workflows.interview.state import InterviewState, ScoreResult

logger = logging.getLogger(__name__)


async def evaluate_node(state: InterviewState, config: RunnableConfig) -> dict:
    """Evaluate candidate answer and persist score to InterviewMessage.

    Uses prompt | llm.with_structured_output(ScoreResult) for structured scoring.
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
    # Build conversation history text
    history_text = ""
    chat_history = state.get("chat_history", [])
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
        llm = get_chat_llm(temperature=0.3)
        structured_llm = llm.with_structured_output(ScoreResult, method="json_mode")
        chain = prompt | structured_llm
        result: ScoreResult = await chain.ainvoke(variables)
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

    return {"score": result.score, "feedback": result.feedback}
```

- [ ] **Step 2: Commit**

```bash
git add app/workflows/interview/nodes/evaluate.py
git commit -m "fix: persist score/feedback to InterviewMessage in evaluate_node

P0-1: evaluate_node computed scores but never wrote them back to the
database. get_scored_messages() (filtering score IS NOT NULL) always
returned empty, causing the report to show the last question's score
for every question and '未回答' for all but the last answer.

Now evaluate_node updates the latest unscored candidate message with
score, feedback, and question_index after each evaluation.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Update evaluate_node tests for DB write-back

**Files:**
- Modify: `tests/unit/test_interview_nodes.py:75-133`

- [ ] **Step 1: Update test_returns_score_and_feedback to mock DB session**

Replace lines 79-108 with:

```python
    async def test_returns_score_and_feedback(self):
        """evaluate_node returns score + feedback from structured output"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.evaluate import evaluate_node
        from app.workflows.interview.state import ScoreResult

        state = {
            "current_question": "请介绍 Python 的 GIL",
            "answer": "GIL 是全局解释器锁...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "user_id": 42,
        }

        chain = AsyncMock()
        chain.ainvoke.return_value = ScoreResult(score=7.5, feedback="回答良好")
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        # Mock DB: return an unscored candidate message
        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.with_structured_output.return_value = object()
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 7.5
        assert result["feedback"] == "回答良好"
        # Verify score was written to the message
        assert mock_msg.score == 7.5
        assert mock_msg.feedback == "回答良好"
        mock_db.commit.assert_called_once()

    async def test_persists_score_to_db_message(self):
        """evaluate_node updates the latest unscored candidate message with score+feedback+question_index"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.evaluate import evaluate_node
        from app.workflows.interview.state import ScoreResult

        state = {
            "current_question": "What is dependency injection?",
            "answer": "DI is a pattern where...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 5,
            "current_index": 2,
        }

        chain = AsyncMock()
        chain.ainvoke.return_value = ScoreResult(score=9.0, feedback="Excellent")
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.with_structured_output.return_value = object()
            await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert mock_msg.score == 9.0
        assert mock_msg.feedback == "Excellent"
        assert mock_msg.question_index == 2
        mock_db.commit.assert_called_once()
```

- [ ] **Step 2: Update test_fallback_on_llm_failure to not require DB write**

Replace lines 111-133 with:

```python
    async def test_fallback_on_llm_failure(self):
        """LLM failure returns fallback score=5.0, does not attempt DB write"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "Q",
            "answer": "A",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "user_id": 42,
        }

        mock_db = AsyncMock()

        with patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 5.0
        assert "评分异常" in result["feedback"]
        # DB must NOT be called when LLM fails (early return before DB write)
        mock_db.execute.assert_not_called()
```

- [ ] **Step 3: Run evaluate_node tests**

```bash
docker exec shilian-app pytest tests/unit/test_interview_nodes.py::TestEvaluateNode -v
```

Expected: 4 tests PASS (or 3 if `test_persists_score_to_db_message` is new)

- [ ] **Step 4: Run full unit test suite to check for regressions**

```bash
docker exec shilian-app pytest -m unit -v
```

Expected: All unit tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_interview_nodes.py
git commit -m "test: update evaluate_node tests for DB score write-back

P0-1 companion: verify that evaluate_node writes score, feedback, and
question_index to the latest unscored InterviewMessage, and that DB is
not touched on LLM failure.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Final verification — full test suite

- [ ] **Step 1: Run full unit test suite**

```bash
docker exec shilian-app pytest -m unit -v
```

Expected: All unit tests PASS (no regressions).

- [ ] **Step 2: Smoke test — verify BM25 indices build in real container**

```bash
docker exec shilian-app python -c "
import asyncio
from app.db.base import get_session_local
from app.retrieval.bm25_lifecycle import build_bm25_indices, get_knowledge_bm25, get_question_bank_bm25

async def test():
    session_local = get_session_local()
    async with session_local() as db:
        await build_bm25_indices(db)
    kb = get_knowledge_bm25()
    qb = get_question_bank_bm25()
    print(f'Knowledge BM25 corpus: {kb.corpus_size if kb else 0}')
    print(f'Question bank BM25 corpus: {qb.corpus_size if qb else 0}')
    assert kb is not None and kb.corpus_size > 0, 'Knowledge BM25 not built!'
    assert qb is not None and qb.corpus_size > 0, 'Question bank BM25 not built!'
    # Verify search returns PG ids (not corpus indices)
    results = qb.search('Python', top_k=3)
    print(f'Search results (pg_id, score): {results}')
    assert len(results) > 0
    assert all(isinstance(r[0], int) and r[0] > 0 for r in results), 'IDs should be PG keys > 0'

asyncio.run(test())
print('All checks passed!')
```

Expected: Knowledge and question bank corpora > 0, search returns PG primary keys > 0.

- [ ] **Step 3: Commit any final adjustments**

```bash
git status
# If clean: echo "All done"
```
