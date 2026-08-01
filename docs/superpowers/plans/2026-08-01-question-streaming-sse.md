# 出题 SSE 流式化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

> **✅ 状态（2026-08-01）：Task 1-7 + 收尾 4 项全部完成并提交本地 dev。** 最终整体 code review **Ready to merge**，`pytest -m "unit"` 89 passed、前端 `npm run build` 通过。唯一待办是"手动端到端验证"（需真实 DeepSeek token + 已完成简历，见下文）。

**Goal:** 把 `/interviews/start` 出题链路改为 SSE 流式输出（status → chunk token 逐字流出 → done），前端实时增量解析 JSON 让题目逐条浮现，消除 16s 假进度条等待。

**Architecture:** 后端 `select_and_adapt_questions` 由 `chain.ainvoke` 改出流式孪生 `select_and_adapt_questions_stream`（`chain.astream` 逐 token），`interview_service` 拆分 `_prepare_questions`（RAG 检索，流式/非流式共用）并新增 `start_interview_stream` async generator；路由 `/start` 加 `?stream=true` 走 `StreamingResponse`。前端复用 `submitAnswerStream` 的 SSE 消费循环，chunk 走 `tryParseQuestions` 增量 `JSON.parse` 渲染题目。非流式路径完全保留，position_agent 入口 B 零回归。

**Tech Stack:** FastAPI StreamingResponse / sse-starlette、LangChain `chain.astream`、Vue 3 fetch + ReadableStream。

**Spec:** `docs/superpowers/specs/2026-08-01-question-streaming-sse-design.md`

---

## 文件结构

| 文件 | 职责 | 操作 |
|---|---|---|
| `app/common/json_utils.py` | 新增 `try_parse_partial_array`（增量 JSON 数组解析，前端算法后端等价版） | Modify |
| `app/services/client/ai_service.py` | 新增 `select_and_adapt_questions_stream`（流式选题孪生） | Modify |
| `app/services/client/interview_service.py` | 抽取 `_prepare_questions`；新增 `start_interview_stream` | Modify |
| `app/api/client/v1/interview.py` | `/start` 加 `?stream=true` SSE 分支 | Modify |
| `tests/test_json_partial_array.py` | `try_parse_partial_array` 单测 | Create |
| `tests/test_ai_service_unit.py` | `select_and_adapt_questions_stream` 单测（新增测试类） | Modify |
| `tests/test_start_interview_stream.py` | `start_interview_stream` 事件序列单测 | Create |
| `tests/test_start_stream_route.py` | 路由 `?stream=true` 分支单测 | Create |
| `ai-interview-frontend/src/api/interview.js` | 新增 `startInterviewStream` + `tryParseQuestions` | Modify |
| `ai-interview-frontend/src/views/ResumeUpload.vue` | `handleStart` 改流式 + 题目浮现区 | Modify |

**测试运行**（后端在容器内）：
- 单测：`docker exec shilian-app pytest tests/<file>.py -m "unit" -v`
- 回归：`docker exec shilian-app pytest -m "unit"`
- 前端语法验证：`cd ai-interview-frontend && npm run build`

**提交规则**：全部 commit 到本地 `dev`，**不 push**（遵循仓库分支策略）。

---

### Task 1: `try_parse_partial_array` 增量解析（后端 util）

**Files:**
- Modify: `app/common/json_utils.py`
- Test: `tests/test_json_partial_array.py`（Create）

- [x] **Step 1: 读文件确认 `import json` 已存在**

Run: `head -20 app/common/json_utils.py`
Expected: 顶部已有 `import json`（`extract_json` 依赖它）。若没有，补上。

- [x] **Step 2: 写失败测试**

Create `tests/test_json_partial_array.py`:

```python
"""
单元测试 — try_parse_partial_array 增量 JSON 数组解析（SSE 流式出题前端逻辑的后端等价验证）

运行：
  pytest tests/test_json_partial_array.py -m "unit"
"""
import pytest
from app.common.json_utils import try_parse_partial_array


@pytest.mark.unit
class TestTryParsePartialArray:
    def test_full_array(self):
        assert try_parse_partial_array('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]

    def test_partial_array_with_complete_elements(self):
        assert try_parse_partial_array('[{"a": 1}, {"a": 2}') == [{"a": 1}, {"a": 2}]

    def test_partial_array_with_incomplete_last_element(self):
        assert try_parse_partial_array('[{"a": 1}, {"a": 2') is None

    def test_empty_returns_none(self):
        assert try_parse_partial_array("") is None

    def test_non_array_returns_none(self):
        assert try_parse_partial_array('{"a": 1}') is None

    def test_partial_non_array_returns_none(self):
        assert try_parse_partial_array('{"a": 1') is None
```

- [x] **Step 3: 跑测试确认失败**

Run: `docker exec shilian-app pytest tests/test_json_partial_array.py -v`
Expected: FAIL — `ImportError: cannot import name 'try_parse_partial_array'`

- [x] **Step 4: 实现**

Append to `app/common/json_utils.py`:

```python
def try_parse_partial_array(text: str) -> list | None:
    """增量解析 JSON 数组：能解出多少就解出多少（SSE 流式出题用）。

    规则：
    - 空 / 空白 → None
    - 完整数组 → 完整解析（校验是 list）
    - 缺右括号但已有元素完整 → 补右括号提前解出已完整对象
    - 末尾元素不完整 / 非数组 → None
    """
    trimmed = text.strip()
    if not trimmed:
        return None
    try:
        parsed = json.loads(trimmed)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        pass
    if trimmed.endswith("]"):
        return None
    try:
        parsed = json.loads(trimmed + "]")
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        return None
```

- [x] **Step 5: 跑测试确认通过**

Run: `docker exec shilian-app pytest tests/test_json_partial_array.py -v`
Expected: 6 passed

- [x] **Step 6: 回归 + 提交**

Run: `docker exec shilian-app pytest tests/test_json_partial_array.py tests/test_ai_service_unit.py -m "unit"`
Expected: 全绿

```bash
git add app/common/json_utils.py tests/test_json_partial_array.py
git commit -m "feat: 增量 JSON 数组解析 try_parse_partial_array（出题流式前端算法后端等价验证）"
```

---

### Task 2: `select_and_adapt_questions_stream` 流式选题

**Files:**
- Modify: `app/services/client/ai_service.py`
- Modify: `tests/test_ai_service_unit.py`

- [x] **Step 1: 写失败测试**

Append to `tests/test_ai_service_unit.py` (after `TestSelectAndAdaptQuestions`):

```python
@pytest.mark.unit
class TestSelectAndAdaptQuestionsStream:
    """select_and_adapt_questions_stream — chain.astream 逐 token 产出的流式选题孪生"""

    @staticmethod
    def _patch_llm_stream(monkeypatch, chunks):
        from unittest.mock import AsyncMock

        from app.services.client import ai_service as mod

        chain = AsyncMock()

        async def fake_astream(**kwargs):
            for c in chunks:
                yield type("C", (), {"content": c})()

        chain.astream = fake_astream
        mock_prompt = type("P", (), {"__or__": lambda self, other: chain})()
        monkeypatch.setattr(mod, "load_prompt", lambda name: mock_prompt)
        monkeypatch.setattr(mod, "get_chat_llm", lambda **kw: object())
        return chain

    async def test_yields_tokens_then_merged_result(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        chunks = [
            '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "category": "technical", "bank_id": 1, "source": "from_bank"},',
            ' {"index": 1, "question": "asyncio 事件循环原理", "category": "technical", "bank_id": 2, "source": "from_bank"}]',
        ]
        self._patch_llm_stream(monkeypatch, chunks)

        events = []
        async for kind, payload in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={"skills": ["Python", "asyncio"]},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        ):
            events.append((kind, payload))

        kinds = [k for k, _ in events]
        assert kinds == ["token", "token", "result"]
        questions = events[-1][1]
        assert len(questions) == 2
        assert questions[0]["bank_id"] == 1
        # 参考答案来自题库候选而非 LLM 输出
        assert questions[0]["reference_answer"] == "GIL 是全局解释器锁..."
        assert questions[1]["reference_answer"] == "事件循环基于协程..."

    async def test_falls_back_when_aggregated_text_is_garbage(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        self._patch_llm_stream(monkeypatch, ["这是", "乱码"])

        result = None
        async for kind, payload in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        ):
            if kind == "result":
                result = payload

        assert result is not None
        assert len(result) == 2  # 回退候选前 N 题
        assert result[0]["bank_id"] == 1

    async def test_llm_error_still_yields_result_with_fallback(self, monkeypatch):
        """astream 中途抛异常 → 仍 yield ("result", ...)，回退候选前 N 题（不 NameError）。"""
        from unittest.mock import AsyncMock

        from app.services.client import ai_service as mod
        from app.services.client.ai_service import ai_service

        async def broken_astream(**kwargs):
            yield type("C", (), {"content": '[{"index": 0, "question": "Q", "bank_id": 1}'})()
            raise RuntimeError("connection error")

        chain = AsyncMock()
        chain.astream = broken_astream
        mock_prompt = type("P", (), {"__or__": lambda self, other: chain})()
        monkeypatch.setattr(mod, "load_prompt", lambda name: mock_prompt)
        monkeypatch.setattr(mod, "get_chat_llm", lambda **kw: object())

        result = None
        async for kind, payload in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        ):
            if kind == "result":
                result = payload

        assert result is not None
        assert len(result) == 2  # 异常后仍回退候选前 N 题
        assert result[0]["bank_id"] == 1
```

- [x] **Step 2: 跑测试确认失败**

Run: `docker exec shilian-app pytest tests/test_ai_service_unit.py::TestSelectAndAdaptQuestionsStream -v`
Expected: FAIL — `AttributeError: ... has no attribute 'select_and_adapt_questions_stream'`

- [x] **Step 3: 实现**

Add to `app/services/client/ai_service.py`, immediately after `select_and_adapt_questions` method:

```python
    async def select_and_adapt_questions_stream(
        self,
        candidates: list,
        parsed_resume: dict,
        target_position: str,
        difficulty: str,
        target_n: int,
    ):
        """v2 选题的流式孪生：chain.astream 逐 token 产出 + 最终合并结果。

        Yields:
            ("token", str) 逐 token 增量；
            最后 ("result", list) 完整题目列表（含题库参考答案）。
        输入构造、解析、补齐逻辑与 select_and_adapt_questions 完全一致，仅 ainvoke → astream。
        流式中途异常时仍 yield ("result", ...)，由 _merge_selected_questions 回退候选前 N 题。
        """
        is_intern = any(kw in target_position for kw in ["实习", "intern", "Intern"])
        intern_hint = (
            "候选人为实习岗位，优先选择基础类、项目类问题。"
            if is_intern
            else ""
        )

        slim_candidates = [
            {"id": c.get("id"), "question": c.get("question")}
            for c in candidates
        ]

        prompt = load_prompt("question_select")
        llm = get_chat_llm(temperature=0.3)
        chain = prompt | llm

        chunks: list[str] = []
        try:
            async for chunk in chain.astream({
                "target_position": target_position,
                "difficulty": difficulty,
                "intern_hint": intern_hint,
                "candidate_count": len(candidates),
                "target_n": target_n,
                "resume_json": json.dumps(parsed_resume, ensure_ascii=False),
                "candidates_json": json.dumps(slim_candidates, ensure_ascii=False),
            }):
                text = chunk.content if hasattr(chunk, "content") else str(chunk)
                if text:
                    chunks.append(text)
                    yield ("token", text)
        except Exception as e:
            logger.warning(f"选题流式中断，回退候选前 N 题: {e}")

        content = "".join(chunks)
        selected = self._as_question_list(self._extract_json(content))
        yield ("result", self._merge_selected_questions(selected, candidates, target_n))
```

> 若 `ai_service.py` 顶部还没有 `logger`，在实现前补：`logger = logging.getLogger(__name__)`（并确认 `import logging` 已存在）。

- [x] **Step 4: 跑测试确认通过**

Run: `docker exec shilian-app pytest tests/test_ai_service_unit.py::TestSelectAndAdaptQuestionsStream -v`
Expected: 2 passed

- [x] **Step 5: 回归 + 提交**

Run: `docker exec shilian-app pytest tests/test_ai_service_unit.py -m "unit"`
Expected: 全绿

```bash
git add app/services/client/ai_service.py tests/test_ai_service_unit.py
git commit -m "feat: select_and_adapt_questions_stream 流式选题（chain.astream 逐 token）"
```

---

### Task 3: 抽取 `_prepare_questions`（RAG 检索，流式/非流式共用）

**Files:**
- Modify: `app/services/client/interview_service.py`

纯重构（行为不变），用回归测试保护。

- [x] **Step 1: 跑回归基线**

Run: `docker exec shilian-app pytest -m "unit"`
Expected: 全绿（基线）

- [x] **Step 2: 抽取 `_prepare_questions` + 重构 `_generate_questions_with_rag`**

在 `InterviewService` 内新增 `_prepare_questions`，把原 `_generate_questions_with_rag`（`interview_service.py:72-157`）的 RAG 检索段整体移入，返回 `candidates`：

```python
    async def _prepare_questions(
        self,
        db: AsyncSession,
        parsed_resume: dict,
        target_position: str,
        difficulty: str,
        total_questions: int,
    ) -> list:
        """RAG 混合检索，返回候选题目 dict 列表（可能为空）。

        供流式 start_interview_stream 与非流式 _generate_questions_with_rag 共用。
        分支选择（select / seed / generate）由调用方按 len(candidates) 判定。
        """
        from app.retrieval.bm25_lifecycle import get_question_bank_bm25
        from app.retrieval.pipeline import RetrievalPipeline

        query = _build_retrieval_query(target_position, parsed_resume)
        recall_k = total_questions * settings.QUESTION_BANK_RECALL_FACTOR

        try:
            bm25_index = get_question_bank_bm25()

            if bm25_index:
                pipeline = RetrievalPipeline(
                    session=db,
                    collection="question_bank",
                    bm25_index=bm25_index,
                    vector_top_k=settings.VECTOR_TOP_K,
                    bm25_top_k=settings.BM25_TOP_K,
                    final_top_k=recall_k,
                    enable_rerank=True,
                )
                results = await pipeline.search(
                    query=query,
                    filters={
                        "position_tag": target_position,
                        "difficulty": difficulty,
                        "min_score": settings.QUESTION_BANK_MIN_SCORE,
                    },
                )
                candidates = []
                for r in results:
                    candidates.append({
                        "id": r.id,
                        "question": r.metadata.get("question", r.content),
                        "reference_answer": r.metadata.get("reference_answer", ""),
                        "key_points": r.metadata.get("key_points", []),
                        "difficulty": r.metadata.get("difficulty", difficulty),
                        "position_tag": r.metadata.get("position_tag", target_position),
                        "similarity": r.score,
                        "source": "from_bank",
                    })

                if len(candidates) < total_questions:
                    relaxed_results = await pipeline.search(
                        query=query,
                        filters={
                            "difficulty": difficulty,
                            "min_score": settings.QUESTION_BANK_MIN_SCORE,
                        },
                    )
                    seen = {c["id"] for c in candidates}
                    for r in relaxed_results:
                        if r.id not in seen:
                            candidates.append({
                                "id": r.id,
                                "question": r.metadata.get("question", r.content),
                                "reference_answer": r.metadata.get("reference_answer", ""),
                                "key_points": r.metadata.get("key_points", []),
                                "difficulty": r.metadata.get("difficulty", difficulty),
                                "position_tag": r.metadata.get("position_tag", target_position),
                                "similarity": r.score,
                                "source": "from_bank",
                            })
            else:
                candidates = await question_bank_service.retrieve_questions(
                    query=query,
                    db=db,
                    k=recall_k,
                    position_tag=target_position,
                    difficulty=difficulty,
                    min_score=settings.QUESTION_BANK_MIN_SCORE,
                )
                if len(candidates) < total_questions:
                    relaxed = await question_bank_service.retrieve_questions(
                        query=query,
                        db=db,
                        k=recall_k,
                        position_tag=None,
                        difficulty=difficulty,
                        min_score=settings.QUESTION_BANK_MIN_SCORE,
                    )
                    seen = {c["id"] for c in candidates}
                    for c in relaxed:
                        if c["id"] not in seen:
                            candidates.append(c)
        except Exception as e:
            logger.error(f"[RAG出题] Hybrid retrieval failed, falling back to pure AI: {e}")
            candidates = []

        return candidates
```

把 `_generate_questions_with_rag` 的检索段（`interview_service.py:69-157`）替换为调用，保留分支判定（`:159-192` 原样）：

```python
    async def _generate_questions_with_rag(
        self,
        db: AsyncSession,
        parsed_resume: dict,
        target_position: str,
        difficulty: str,
        total_questions: int,
    ) -> list:
        """Phase 3 RAG 出题：使用 RetrievalPipeline（vector + BM25 + RRF + rerank）。

        优先级：hybrid recall → AI select → AI seed → pure AI generate。
        """
        candidates = await self._prepare_questions(
            db=db,
            parsed_resume=parsed_resume,
            target_position=target_position,
            difficulty=difficulty,
            total_questions=total_questions,
        )

        cnt = len(candidates)
        logger.info(f"[RAG出题] Hybrid recall: {cnt} questions, target: {total_questions}")

        if cnt >= total_questions:
            logger.info(f"[RAG出题] 走【题库充分】分支")
            questions = await ai_service.select_and_adapt_questions(
                candidates=candidates,
                parsed_resume=parsed_resume,
                target_position=target_position,
                difficulty=difficulty,
                target_n=total_questions,
            )
        elif cnt > 0:
            logger.info(f"[RAG出题] 走【AI 兜底补全】分支（题库 {cnt} 题 + AI 补 {total_questions - cnt} 题）")
            questions = await ai_service.generate_with_seeds(
                seed_questions=candidates,
                parsed_resume=parsed_resume,
                target_position=target_position,
                difficulty=difficulty,
                target_n=total_questions,
            )
        else:
            logger.warning(f"[RAG出题] 题库为空，走【纯 AI 生成】兜底分支")
            questions = await ai_service.generate_questions(
                parsed_resume=parsed_resume,
                target_position=target_position,
                difficulty=difficulty,
                count=total_questions,
            )
            for q in questions:
                q.setdefault("source", "ai_fallback")
                q.setdefault("bank_id", None)

        return questions
```

- [x] **Step 3: 回归确认**

Run: `docker exec shilian-app pytest -m "unit"`
Expected: 全绿（与基线一致）

- [x] **Step 4: 提交**

```bash
git add app/services/client/interview_service.py
git commit -m "refactor: interview_service 抽取 _prepare_questions（RAG 检索流式/非流式共用）"
```

---

### Task 4: `start_interview_stream`（SSE async generator）

**Files:**
- Modify: `app/services/client/interview_service.py`
- Test: `tests/test_start_interview_stream.py`（Create）

- [x] **Step 1: 写失败测试**

Create `tests/test_start_interview_stream.py`:

```python
"""
单元测试 — start_interview_stream SSE 事件序列（status → chunk → done）

运行：
  pytest tests/test_start_interview_stream.py -m "unit"
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.client import interview_service as mod


def _parse_sse_blocks(sse_events):
    """把 _sse 产出的多行字符串块解析成 (event, data_dict) 列表。"""
    parsed = []
    for block in sse_events:
        lines = block.strip().splitlines()
        event = lines[0].replace("event: ", "").strip()
        data_line = next((l for l in lines if l.startswith("data: ")), "")
        data = json.loads(data_line.replace("data: ", "", 1))
        parsed.append((event, data))
    return parsed


@pytest.mark.unit
class TestStartInterviewStream:
    @staticmethod
    def _make_db_mock():
        resume = SimpleNamespace(status="completed", parsed_content='{"skills": ["Python"]}')

        class _Scalar:
            def scalar_one_or_none(self):
                return resume

        db = AsyncMock()
        db.execute.return_value = _Scalar()

        async def fake_refresh(obj):
            obj.id = 42

        db.refresh = fake_refresh
        return db

    async def test_stream_sequence_and_done_payload(self, monkeypatch):
        candidates = [
            {
                "id": 1, "question": "讲下 Python 的 GIL",
                "reference_answer": "GIL 是全局解释器锁...", "key_points": ["GIL"],
                "difficulty": "medium", "position_tag": "python_backend",
                "similarity": 0.9, "source": "from_bank",
            }
        ]

        async def fake_prepare(**kwargs):
            return candidates

        async def fake_stream(**kwargs):
            yield ("token", '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "bank_id": 1')
            yield ("result", [{
                "question": "讲下 Python 的 GIL（微调）", "bank_id": 1,
                "reference_answer": "GIL 是全局解释器锁...", "source": "from_bank",
            }])

        from app.services.client.ai_service import ai_service
        monkeypatch.setattr(mod.InterviewService, "_prepare_questions", fake_prepare)
        monkeypatch.setattr(ai_service, "select_and_adapt_questions_stream", fake_stream)
        monkeypatch.setattr(mod.question_bank_service, "increment_use_count", AsyncMock())

        svc = mod.InterviewService()
        sse_events = [s async for s in svc.start_interview_stream(
            db=self._make_db_mock(),
            user_id=1,
            resume_id=1,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=1,
        )]
        parsed = _parse_sse_blocks(sse_events)

        assert [e for e, _ in parsed] == ["status", "chunk", "done"]
        assert parsed[0][1] == {"message": "正在检索题库..."}
        assert parsed[1][1]["content"] == '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "bank_id": 1'
        done = parsed[2][1]
        assert done["interview_id"] == 42
        assert done["first_question"] == "讲下 Python 的 GIL（微调）"
        assert done["question_index"] == 0
        assert done["total_questions"] == 1

    async def test_fallback_branch_yields_status_then_done(self, monkeypatch):
        # 题库为空 → 纯 AI 生成兜底（无 chunk），仅 status + done
        async def fake_prepare(**kwargs):
            return []

        async def fake_generate(**kwargs):
            return [{"question": "Q1", "source": "ai_fallback", "bank_id": None}]

        from app.services.client.ai_service import ai_service
        monkeypatch.setattr(mod.InterviewService, "_prepare_questions", fake_prepare)
        monkeypatch.setattr(ai_service, "generate_questions", fake_generate)
        monkeypatch.setattr(mod.question_bank_service, "increment_use_count", AsyncMock())

        svc = mod.InterviewService()
        sse_events = [s async for s in svc.start_interview_stream(
            db=self._make_db_mock(),
            user_id=1,
            resume_id=1,
            target_position="Python 后端",
            difficulty="medium",
            total_questions=1,
        )]
        parsed = _parse_sse_blocks(sse_events)

        assert [e for e, _ in parsed] == ["status", "done"]
        assert parsed[1][1]["first_question"] == "Q1"
```

- [x] **Step 2: 跑测试确认失败**

Run: `docker exec shilian-app pytest tests/test_start_interview_stream.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'start_interview_stream'`

- [x] **Step 3: 实现**

Add to `InterviewService` in `app/services/client/interview_service.py`, after `start_interview`:

```python
    async def start_interview_stream(
        self,
        db: AsyncSession,
        user_id: int,
        resume_id: int,
        target_position: str,
        difficulty: str,
        total_questions: int,
    ):
        """流式启动面试：SSE 产出 status → chunk* → done。

        Yields:
            SSE 字符串（_sse 编码）。复用 start_interview 的校验与落库逻辑，
            仅把"选题 LLM 调用"替换为 ai_service.select_and_adapt_questions_stream 逐 token。
        """
        from app.workflows._shared.sse import _sse

        # 简历归属 + 解析状态校验（与 start_interview 一致）
        query = select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id
        )
        result = await db.execute(query)
        resume = result.scalar_one_or_none()

        if not resume:
            raise NotFoundError(message="简历不存在")
        if resume.status != "completed":
            raise ValidationError(message="简历尚未解析完成")

        try:
            parsed_resume = json.loads(resume.parsed_content)
        except json.JSONDecodeError:
            logger.error(f"简历 parsed_content 不是有效 JSON: resume_id={resume.id}")
            raise ValidationError(message="简历数据异常，请重新上传")

        # 1. 状态：检索中
        yield _sse("status", {"message": "正在检索题库..."})

        # 2. RAG 检索（流式/非流式共用）
        candidates = await self._prepare_questions(
            db=db,
            parsed_resume=parsed_resume,
            target_position=target_position,
            difficulty=difficulty,
            total_questions=total_questions,
        )
        cnt = len(candidates)
        logger.info(f"[RAG出题] Hybrid recall: {cnt} questions, target: {total_questions}")

        # 3. 选题：主分支流式，兜底分支非流式
        if cnt >= total_questions:
            logger.info(f"[RAG出题] 走【题库充分】分支（流式）")
            async for kind, payload in ai_service.select_and_adapt_questions_stream(
                candidates=candidates,
                parsed_resume=parsed_resume,
                target_position=target_position,
                difficulty=difficulty,
                target_n=total_questions,
            ):
                if kind == "token":
                    yield _sse("chunk", {"content": payload})
                else:
                    questions = payload
        elif cnt > 0:
            logger.info(f"[RAG出题] 走【AI 兜底补全】分支（题库 {cnt} 题 + AI 补 {total_questions - cnt} 题）")
            questions = await ai_service.generate_with_seeds(
                seed_questions=candidates,
                parsed_resume=parsed_resume,
                target_position=target_position,
                difficulty=difficulty,
                target_n=total_questions,
            )
        else:
            logger.warning(f"[RAG出题] 题库为空，走【纯 AI 生成】兜底分支")
            questions = await ai_service.generate_questions(
                parsed_resume=parsed_resume,
                target_position=target_position,
                difficulty=difficulty,
                count=total_questions,
            )
            for q in questions:
                q.setdefault("source", "ai_fallback")
                q.setdefault("bank_id", None)

        # 4. 落库（与 start_interview 一致）
        bank_ids = [q.get("bank_id") for q in questions if q.get("bank_id")]
        if bank_ids:
            await question_bank_service.increment_use_count(db, bank_ids)

        interview = Interview(
            user_id=user_id,
            resume_id=resume_id,
            target_position=target_position,
            difficulty=difficulty,
            total_questions=total_questions,
            current_question_index=0,
            questions_data=questions,
            status="in_progress"
        )
        db.add(interview)
        await db.commit()
        await db.refresh(interview)

        first_question = questions[0]["question"]
        msg = InterviewMessage(
            interview_id=interview.id,
            role="interviewer",
            content=first_question,
            question_index=0
        )
        db.add(msg)
        await db.commit()

        # 5. 完成：携带 interview_id 等（与 start_interview 返回一致）
        yield _sse("done", {
            "interview_id": interview.id,
            "first_question": first_question,
            "question_index": 0,
            "total_questions": total_questions,
        })
```

- [x] **Step 4: 跑测试确认通过**

Run: `docker exec shilian-app pytest tests/test_start_interview_stream.py -v`
Expected: 2 passed

- [x] **Step 5: 回归 + 提交**

Run: `docker exec shilian-app pytest -m "unit"`
Expected: 全绿

```bash
git add app/services/client/interview_service.py tests/test_start_interview_stream.py
git commit -m "feat: start_interview_stream SSE 出题（status → chunk → done）"
```

---

### Task 5: 路由 `POST /start?stream=true`

**Files:**
- Modify: `app/api/client/v1/interview.py`
- Test: `tests/test_start_stream_route.py`（Create）

- [x] **Step 1: 写失败测试**

Create `tests/test_start_stream_route.py`:

```python
"""
单元测试 — /interviews/start?stream=true 路由 SSE 分支

运行：
  pytest tests/test_start_stream_route.py -m "unit"
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import StreamingResponse

from app.api.client.v1.interview import start_interview
from app.schemas.response import ApiResponse


@pytest.mark.unit
class TestStartStreamRoute:
    DATA = SimpleNamespace(
        resume_id=1, target_position="Python 后端",
        difficulty="medium", total_questions=3,
    )

    async def test_stream_true_returns_streaming_response(self):
        async def fake_gen(**kwargs):
            yield "event: done\ndata: {}\n\n"

        svc = SimpleNamespace(start_interview_stream=fake_gen)
        resp = await start_interview(
            data=self.DATA,
            stream=True,
            current_user=SimpleNamespace(id=1),
            db=AsyncMock(),
            interview_service=svc,
        )

        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"
        assert resp.headers["X-Accel-Buffering"] == "no"

    async def test_stream_false_returns_api_response(self):
        svc = SimpleNamespace(start_interview=AsyncMock(return_value={"interview_id": 1}))
        resp = await start_interview(
            data=self.DATA,
            stream=False,
            current_user=SimpleNamespace(id=1),
            db=AsyncMock(),
            interview_service=svc,
        )

        assert isinstance(resp, ApiResponse)
        svc.start_interview.assert_awaited_once()
```

- [x] **Step 2: 跑测试确认失败**

Run: `docker exec shilian-app pytest tests/test_start_stream_route.py -v`
Expected: FAIL — `TypeError: start_interview() got an unexpected keyword argument 'stream'`

- [x] **Step 3: 实现**

Modify `app/api/client/v1/interview.py` `start_interview` (lines 48-64):

```python
@router.post("/start")
async def start_interview(
    data: InterviewStart,
    stream: bool = Query(default=False, description="是否使用 SSE 流式出题"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    """开始新的 AI 面试会话（?stream=true 走 SSE 流式出题）"""
    if stream:
        async def event_generator():
            async for sse_str in interview_service.start_interview_stream(
                db=db,
                user_id=current_user.id,
                resume_id=data.resume_id,
                target_position=data.target_position,
                difficulty=data.difficulty,
                total_questions=data.total_questions,
            ):
                yield sse_str

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    result = await interview_service.start_interview(
        db=db,
        user_id=current_user.id,
        resume_id=data.resume_id,
        target_position=data.target_position,
        difficulty=data.difficulty,
        total_questions=data.total_questions,
    )
    return ApiResponse.success(data=result)
```

- [x] **Step 4: 跑测试确认通过**

Run: `docker exec shilian-app pytest tests/test_start_stream_route.py -v`
Expected: 2 passed

- [x] **Step 5: 回归 + 提交**

Run: `docker exec shilian-app pytest -m "unit"`
Expected: 全绿

```bash
git add app/api/client/v1/interview.py tests/test_start_stream_route.py
git commit -m "feat: /interviews/start?stream=true 路由 SSE 分支"
```

---

### Task 6: 前端 `startInterviewStream` + `tryParseQuestions`

**Files:**
- Modify: `ai-interview-frontend/src/api/interview.js`

前端无测试框架，用 `npm run build` 做语法验证。

- [x] **Step 1: 实现**

Append to `ai-interview-frontend/src/api/interview.js`:

```js
export async function startInterviewStream(data, onStatus, onQuestion, onDone, signal) {
  const authStore = (await import('../stores/auth')).useAuthStore()
  const response = await fetch('/api/v1/interviews/start?stream=true', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authStore.token}`
    },
    body: JSON.stringify(data),
    signal
  })

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let lineBuffer = ''      // SSE 行拆分缓冲
  let jsonBuffer = ''      // JSON 增量累积缓冲（LLM 输出原文）
  let currentEvent = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      lineBuffer += decoder.decode(value, { stream: true })

      const lines = lineBuffer.split('\n')
      lineBuffer = lines.pop() || ''

      for (const line of lines) {
        if (line === '') {
          currentEvent = ''
          continue
        }
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            switch (currentEvent) {
              case 'status':
                onStatus(data.message)
                break
              case 'chunk':
                jsonBuffer += data.content
                const parsed = tryParseQuestions(jsonBuffer)
                if (parsed && parsed.length) onQuestion(parsed)
                break
              case 'error':
                throw new Error(data.message || data.error || 'Unknown error')
              case 'done':
                onDone(data)
                break
            }
          } catch (e) {
            if (e.message !== 'Unexpected end of JSON input') throw e
          }
        }
      }
    }
  } finally {
    try { reader.releaseLock() } catch (_) {}
  }
}

function tryParseQuestions(buffer) {
  const trimmed = buffer.trim()
  if (!trimmed) return null
  try {
    const arr = JSON.parse(trimmed)
    return Array.isArray(arr) ? arr : null
  } catch (_) {}
  if (trimmed.endsWith(']')) return null
  try {
    const arr = JSON.parse(trimmed + ']')   // 补右括号提前解出已完整对象
    return Array.isArray(arr) ? arr : null
  } catch (_) {
    return null                              // 末尾对象未闭合 → 保留上次结果
  }
}
```

- [x] **Step 2: build 验证**

Run: `cd ai-interview-frontend && npm run build`
Expected: 构建成功，无语法错误

- [x] **Step 3: 提交**

```bash
git add ai-interview-frontend/src/api/interview.js
git commit -m "feat(frontend): startInterviewStream 流式出题消费 + tryParseQuestions 增量解析"
```

---

### Task 7: 前端 `ResumeUpload.vue` 流式化 + 题目浮现

**Files:**
- Modify: `ai-interview-frontend/src/views/ResumeUpload.vue`

- [x] **Step 1: import 增加 `startInterviewStream`**

Modify line 146:
```js
import { startInterview, startInterviewStream } from '../api/interview'
```

- [x] **Step 2: template 增加题目浮现区**

In the `step === 'starting'` block (`<div v-if="step === 'starting'" class="parsing-stage">`, after the progress bar / tip text, add):

```html
        <div v-if="streamedQuestions.length" class="streamed-questions">
          <div v-for="(q, i) in streamedQuestions" :key="i" class="streamed-question">
            <span class="q-index">{{ i + 1 }}</span>
            <span class="q-text">{{ q.question }}</span>
          </div>
        </div>
```

- [x] **Step 3: script 增加 `streamedQuestions` ref 并改造 `handleStart`**

Add near `const startingTitle`:
```js
const streamedQuestions = ref([])
```

Replace `handleStart` (lines 339-365):
```js
async function handleStart() {
  error.value = ''
  starting.value = true
  step.value = 'starting'
  streamedQuestions.value = []
  startStartingAnimation()

  try {
    await startInterviewStream(
      {
        resume_id: resumeId.value,
        target_position: targetPosition.value,
        difficulty: difficulty.value,
        total_questions: totalQuestions.value
      },
      (msg) => { startingTitle.value = msg },
      (questions) => { streamedQuestions.value = questions },
      async (data) => {
        startProgress.value = 100
        startingTitle.value = '面试准备完成！'
        stopStartingAnimation()
        await new Promise(r => setTimeout(r, 800))
        router.push(`/interview/${data.interview_id}`)
      }
    )
  } catch (e) {
    stopStartingAnimation()
    error.value = e.message
    step.value = 2
  } finally {
    starting.value = false
  }
}
```

- [x] **Step 4: style 增加题目卡片样式**

In `<style scoped>`, append:
```css
.streamed-questions { margin-top: 16px; text-align: left; max-height: 180px; overflow-y: auto; }
.streamed-question { display: flex; gap: 8px; padding: 8px 12px; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 8px; font-size: 13px; }
.q-index { flex-shrink: 0; width: 20px; height: 20px; border-radius: 50%; background: #4f46e5; color: #fff; text-align: center; line-height: 20px; font-size: 12px; }
.q-text { color: #374151; line-height: 1.5; }
```

- [x] **Step 5: build 验证**

Run: `cd ai-interview-frontend && npm run build`
Expected: 构建成功，无语法错误

- [x] **Step 6: 提交**

```bash
git add ai-interview-frontend/src/views/ResumeUpload.vue
git commit -m "feat(frontend): ResumeUpload 开始面试流式化，题目逐条浮现"
```

---

## 手动端到端验证（全部完成后）

1. 起容器：`cd ai-interview-backend && docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
2. 后端全量单测：`docker exec shilian-app pytest -m "unit"`（全绿）
3. 流式 curl 验证（出题走主分支，会真实消耗 DeepSeek token）：
   ```bash
   curl -N -X POST "http://localhost:8006/api/v1/interviews/start?stream=true" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer <client_token>" \
     -d '{"resume_id": <id>, "target_position": "Python 后端", "difficulty": "medium", "total_questions": 3}'
   ```
   Expected: 依次看到 `event: status` → 多行 `event: chunk`（JSON 字符逐片流出）→ `event: done`（含 `interview_id`）
4. 浏览器走前端流程：上传简历 → 开始面试 → starting 阶段看到题目逐条浮现 → done 后跳转 `/interview/{id}`
5. 回归：position_agent 入口 B（`PositionMatch.vue` 开始面试）不受影响（走非流式路径）

## 已知边界

- 总耗时不变（~16s），流式只改善感知
- 兜底分支（题库不足/为空）无 token 流，前端只见 status → done
- `tryParseQuestions` 后端 Python 版（Task 1）与前端 JS 版（Task 6）逻辑等价但独立维护；改动需两边同步
- 提交全部在本地 `dev`，不 push（文档与代码改动均遵循仓库分支策略）
