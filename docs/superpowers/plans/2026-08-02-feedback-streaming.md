# 面试反馈真流式化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让面试提交回答后的反馈（评分 feedback + 评估报告）实现 token 级流式输出，消除"静默卡死"等待假象。

**Architecture:** 复用出题链路 `select_and_adapt_questions_stream` 的成熟范式——evaluate / generate_report 节点从 `with_structured_output + ainvoke` 改为 `prompt | llm.bind(response_format=json_object)` + `astream`（自动触发 LangGraph `on_chat_model_stream` → SSE `chunk`），节点内累积 chunks + `extract_json` 最后解析出结构化 dict 写回 state → DB。前端 `Interview.vue` 移除正则过滤 JSON 的逻辑，改为对象增量 JSON 解析 + feedback / 报告摘要逐字流式显示。

**Tech Stack:** LangGraph astream_events、LangChain ChatOpenAI、SSE（sse-starlette）、Vue 3 `fetch` + `getReader()` 流式解析

**前置知识（实现者必读）**
- `app/workflows/_shared/sse.py:49-52`：`on_chat_model_stream` 事件 → SSE `chunk`（已实现，无需改）
- `app/common/json_utils.py:11-63`：`extract_json` 从 AI 文本提取 dict，失败返回 `{"score":5.0,"feedback":原文[:200],"parse_failed":True}`，不抛异常
- 测试在容器内跑：`docker exec shilian-app pytest ...`（dev compose 已挂载 `./tests`）
- 参考范式：`app/services/client/ai_service.py:251-303`（`select_and_adapt_questions_stream`）

---

## Task 1: evaluate_node 真流式化（后端）

**Files:**
- Modify: `ai-interview-backend/app/workflows/interview/nodes/evaluate.py`
- Test: `ai-interview-backend/tests/unit/test_interview_nodes.py`

- [ ] **Step 1: 改 `evaluate.py` — LLM 调用从 `ainvoke` 改 `astream` + `extract_json`**

文件顶部 import 区增加一行（现有 import 里无 `extract_json`）：
```python
from app.common.json_utils import extract_json
```

把 try 块内的 LLM 调用段（当前第 84-91 行）替换为：
```python
    try:
        prompt = load_prompt("evaluator_agent")
        llm = get_chat_llm(temperature=0.3)
        structured_llm = llm.bind(response_format={"type": "json_object"})
        chain = prompt | structured_llm

        chunks: list[str] = []
        async for chunk in chain.astream(variables):
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                chunks.append(text)

        parsed = extract_json("".join(chunks))
        result = ScoreResult(
            score=float(parsed.get("score", 5.0)),
            feedback=str(parsed.get("feedback", "")),
            follow_up=bool(parsed.get("follow_up", False)),
        )
    except Exception as e:
        logger.error(f"Structured scoring failed, returning fallback: {e}")
        return {"score": 5.0, "feedback": f"评分异常，已记录: {str(e)[:100]}"}
```

保持 try 之后的 DB 持久化段（select 最新未评分 candidate msg、写 score/feedback/question_index、commit）完全不变。

- [ ] **Step 2: 更新 `TestEvaluateNode` 测试 — mock 从 `ainvoke` 改 `astream`**

`tests/unit/test_interview_nodes.py` 的 `TestEvaluateNode` 类中，`chain.ainvoke.return_value = ScoreResult(...)` + `with_structured_output` 的 mock 方式全部废弃。用统一的 **AsyncMock + async generator side_effect** 模式替代（`ScoreResult` import 不再需要时可删）。

`test_returns_score_and_feedback` 整体替换为：
```python
    async def test_returns_score_and_feedback(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "请介绍 Python 的 GIL",
            "answer": "GIL 是全局解释器锁...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "user_id": 42,
        }

        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 7.5, "feedback": "回答良好", "follow_up": false}')

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
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
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 7.5
        assert result["feedback"] == "回答良好"
        assert mock_msg.score == 7.5
        assert mock_msg.feedback == "回答良好"
        mock_db.commit.assert_called_once()
```

`test_persists_score_to_db_message` 的 chain mock 段替换为：
```python
        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 9.0, "feedback": "Excellent", "follow_up": false}')

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
```
并把 patch 块里的 `mock_get_llm.return_value.with_structured_output.return_value = object()` 改为 `mock_get_llm.return_value.bind.return_value = MagicMock()`。断言（score/feedback/question_index/commit）不变。

`test_fallback_on_llm_failure` **不改**（get_chat_llm 直接抛异常走 except，返回 `{"score": 5.0, "feedback": "评分异常..."}`，断言已匹配）。

`test_commit_failure_marks_persist_failed` 的 chain mock 段替换为：
```python
        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 6.0, "feedback": "ok", "follow_up": false}')

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
```
patch 块内同样把 `with_structured_output.return_value = object()` 改为 `mock_get_llm.return_value.bind.return_value = MagicMock()`。断言不变。

`test_history_excludes_current_answer` 的 chain mock 段替换为：
```python
        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 8.0, "feedback": "ok", "follow_up": false}')

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
```
patch 块内同样改 bind。`variables = chain.astream.call_args[0][0]` 断言保持不变（AsyncMock 记录调用参数）。

新增一个"astream 输出非 JSON 时回退默认分"的测试：
```python
    async def test_non_json_output_falls_back_to_default_score(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "Q",
            "answer": "A",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
        }

        async def _agen(variables):
            yield SimpleNamespace(content="这是纯文本，没有 JSON")

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
        mock_db = AsyncMock()

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 5.0
        assert result["feedback"]  # extract_json 兜底会把原文作为 feedback 返回
```

- [ ] **Step 3: 跑测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_interview_nodes.py -v`
Expected: TestEvaluateNode 全部 PASS（含新增 test_non_json_output_falls_back_to_default_score）

- [ ] **Step 4: Commit**

```bash
git add ai-interview-backend/app/workflows/interview/nodes/evaluate.py ai-interview-backend/tests/unit/test_interview_nodes.py
git commit -m "feat(workflows): evaluate_node 改 astream+extract_json 真流式评分（复用出题流式范式）"
```

---

## Task 2: generate_report_node 真流式化（后端）

**Files:**
- Modify: `ai-interview-backend/app/workflows/interview/nodes/generate_report.py`
- Test: `ai-interview-backend/tests/unit/test_interview_nodes.py`

- [ ] **Step 1: 改 `generate_report.py` — LLM 调用改 `astream` + `extract_json`**

文件顶部 import 区增加：
```python
from app.common.json_utils import extract_json
```

把 try 块内的 LLM 调用段（当前第 61-77 行）替换为：
```python
    try:
        prompt = load_prompt("report_agent")
        llm = get_chat_llm(temperature=0.5)
        structured_llm = llm.bind(response_format={"type": "json_object"})
        chain = prompt | structured_llm

        chunks: list[str] = []
        async for chunk in chain.astream({
            "resume_json": json.dumps(resume_context, ensure_ascii=False),
            "target_position": target_position,
            "qa_text": qa_text,
        }):
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                chunks.append(text)

        parsed = extract_json("".join(chunks))
        report = {
            "summary": str(parsed.get("summary", "报告生成失败")),
            "strengths": _as_str_list(parsed.get("strengths")),
            "weaknesses": _as_str_list(parsed.get("weaknesses")),
            "suggestions": _as_str_list(parsed.get("suggestions")),
            "hire_recommendation": str(parsed.get("hire_recommendation", "")),
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
```

在模块末尾新增 `_as_str_list` helper（放在 `_build_qa_text` 之后）：
```python
def _as_str_list(value) -> list:
    """把 LLM 返回的列表字段归一化为 list[str]；非列表/None 回空列表。"""
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []
```

保持 try 之后的所有逻辑不变：`report["question_scores"]` 组装、`interview_repo.update_result`、`db.commit`、返回 dict。

- [ ] **Step 2: 更新 `TestGenerateReport` 测试 — mock 改 `astream`**

`tests/unit/test_interview_nodes.py` 的 `TestGenerateReport.test_uses_db_feedback_and_zero_fallback` 中，chain mock 段替换为：
```python
        async def _agen(variables):
            yield SimpleNamespace(content='{"summary": "整体表现良好", "strengths": ["思路清晰"], "weaknesses": ["深度不足"], "suggestions": ["加强源码阅读"], "hire_recommendation": "建议录用"}')

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
```
patch 块里 `mock_get_llm.return_value.with_structured_output.return_value = object()` 改为 `mock_get_llm.return_value.bind.return_value = MagicMock()`。断言（question_scores、DB feedback、未评分回退 0.0）保持不变。

新增一个"报告 JSON 缺失列表字段回退空列表"的测试：
```python
    async def test_missing_list_fields_fallback_empty(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.generate_report import generate_report_node

        async def _agen(variables):
            yield SimpleNamespace(content='{"summary": "只有摘要", "hire_recommendation": "通过"}')

        chain = MagicMock()
        chain.astream = AsyncMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        mock_repo = AsyncMock()
        mock_repo.get_scored_messages.return_value = [
            SimpleNamespace(question_index=0, score=7.0, feedback="好", content="答案A"),
        ]
        state = {
            "interview_id": 1,
            "questions": [{"question": "Q0"}],
            "resume_context": {},
            "target_position": "Python",
            "current_index": 0,
            "answer": "答案A",
        }

        with patch(
            "app.workflows.interview.nodes.generate_report.interview_repo",
            mock_repo,
        ), patch(
            "app.workflows.interview.nodes.generate_report.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.generate_report.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await generate_report_node(
                state, {"configurable": {"db": AsyncMock()}}
            )

        assert result["report"]["summary"] == "只有摘要"
        assert result["report"]["strengths"] == []
        assert result["report"]["weaknesses"] == []
        assert result["report"]["suggestions"] == []
```

- [ ] **Step 3: 跑测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_interview_nodes.py -v`
Expected: TestGenerateReport 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add ai-interview-backend/app/workflows/interview/nodes/generate_report.py ai-interview-backend/tests/unit/test_interview_nodes.py
git commit -m "feat(workflows): generate_report_node 改 astream+extract_json 真流式报告（含 _as_str_list 归一化）"
```

---

## Task 3: 前端反馈增量解析流式显示

**Files:**
- Modify: `ai-interview-frontend/src/api/interview.js`
- Modify: `ai-interview-frontend/src/views/Interview.vue`

> 前端无单测基础设施，验证方式 = `npm run build` + 手动端到端。

- [ ] **Step 1: `interview.js` 新增对象型增量解析器**

在 `interview.js` 的 `tryParseQuestions` 函数之后、`submitAnswer` 之前，新增并导出 `tryParseStreamObject`：
```js
// 对象型增量 JSON 解析：能解出多少字段就返回多少（SSE 流式反馈/报告用）。
// 与 tryParseQuestions（数组，补右括号）互补：这里是对象，用字段正则提取
// 已闭合/正在闭合的字符串字段前缀，实现逐字显示。
export function tryParseStreamObject(buffer) {
  const t = buffer.trim()
  if (!t.startsWith('{')) return null
  try {
    const obj = JSON.parse(t)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj
  } catch (_) {}
  const result = {}
  const fb = t.match(/"feedback"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (fb && fb[1]) result.feedback = fb[1]
  const sum = t.match(/"summary"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (sum && sum[1]) result.summary = sum[1]
  const rec = t.match(/"hire_recommendation"\s*:\s*"((?:\\.|[^"\\])*)/)
  if (rec && rec[1]) result.hire_recommendation = rec[1]
  const sc = t.match(/"score"\s*:\s*(\d+(?:\.\d+)?)/)
  if (sc) result.score = parseFloat(sc[1])
  return Object.keys(result).length ? result : null
}
```

- [ ] **Step 2: `Interview.vue` — import + 移除 renderContent 的 JSON 过滤**

`Interview.vue` 第 123 行 import 改为：
```js
import { submitAnswerStream, getMessages, tryParseStreamObject } from '../api/interview'
```

`renderContent` 函数（当前第 160-168 行）替换为（streamingText / 消息内容已是干净文本，JSON 过滤正则全部移除）：
```js
function renderContent(text) {
  if (!text) return ''
  return text.replace(/\n/g, '<br>')
}
```

- [ ] **Step 3: `Interview.vue` — handleSubmit 的 onChunk / onDone 改增量解析**

`handleSubmit` 内，删掉 `let rawStreamText = ''` 那行（第 216 行），改为：
```js
  let feedbackJsonBuffer = ''  // LLM 流式输出的 JSON 原文累积缓冲
```

`onChunk` 回调（当前第 224-238 行）整体替换为：
```js
      (chunk) => {
        feedbackJsonBuffer += chunk
        const parsed = tryParseStreamObject(feedbackJsonBuffer)
        if (!parsed) return
        if (parsed.feedback) {
          // evaluate 阶段：feedback 评语逐字出现
          streamingText.value = parsed.feedback
        } else if (parsed.summary || parsed.hire_recommendation) {
          // generate_report 阶段：报告摘要逐字出现
          streamingText.value = parsed.summary
            ? '📋 报告摘要：' + parsed.summary
            : '💡 录用评价：' + parsed.hire_recommendation
        }
        scrollToBottom()
      },
```

`onDone` 回调（当前第 239-261 行）整体替换为（用权威 `score` 事件数据，不再从过滤后的原文拼 feedback）：
```js
      (data) => {
        if (data.feedback) {
          messages.value.push({ role: 'interviewer', content: data.feedback, score: data.score })
        }
        streamingText.value = ''
        feedbackJsonBuffer = ''
        const lastCandidate = [...messages.value].reverse().find(m => m.role === 'candidate')
        if (lastCandidate) lastCandidate.score = data.score

        if (data.is_finished) {
          finished.value = true
        } else if (data.next_question) {
          currentIndex.value = data.question_index
          messages.value.push({ role: 'interviewer', content: data.next_question })
        }
        scrollToBottom()
      },
```

> 说明：`score` 事件（`sse.py:60-65`）携带 `score`/`feedback`/`follow_up`，`submitAnswerStream` 的 `done` 回调会把它作为 `data` 传入——所以 `data.feedback` 是权威评语，无需再依赖流式原文过滤。`report` 事件仍被忽略（面试结束引导去独立报告页，现状保持）。

- [ ] **Step 4: 前端 build 验证**

Run: `cd ai-interview-frontend && npm run build`
Expected: build 成功，无报错

- [ ] **Step 5: Commit**

```bash
git add ai-interview-frontend/src/api/interview.js ai-interview-frontend/src/views/Interview.vue
git commit -m "feat(frontend): 面试反馈真流式——对象增量 JSON 解析 + feedback/报告摘要逐字显示，移除 JSON 正则过滤"
```

---

## Task 4: 回归验证 + CLAUDE.md 更新

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 全量单元测试回归**

Run: `docker exec shilian-app pytest -m "unit" -q`
Expected: 全部 PASS（重点确认 test_interview_nodes.py、test_start_interview_stream.py、test_start_stream_route.py）

- [ ] **Step 2: 手动端到端验证（需真实 API Key + 已完成简历）**

1. 进入面试页，提交回答 → 观察反馈评语逐字出现、分数最后跳动
2. 最后一题提交 → 观察报告摘要逐字出现 → "查看评估报告"按钮出现
3. 非流式路径：`POST /api/v1/interviews/{id}/answer`（不带 `?stream=true`）返回完整 score/feedback/report 结构（`_extract_response`，`service.py:98-122`）
4. 回归：出题流式（`?stream=true`）仍正常逐字出题

- [ ] **Step 3: 更新 `CLAUDE.md` — Phase 2 规则补丁 + 当前状态**

在「Phase 2 重构硬性规则」一节追加一条：
```markdown
- ❌ 禁止 `with_structured_output` + `ainvoke` 阻塞反馈流式 —— **流式链路**（evaluate / generate_report / 出题）用 `chain.astream() 累积 + extract_json` 最后解析（与出题链路一致），结构化结果仍由 Pydantic 校验保证；**非流式链路**仍强制 `with_structured_output`
```

在「当前状态」一节（2026-08-02 最新段）追加一行：
```markdown
- **面试反馈真流式化**（2026-08-02）：evaluate/generate_report 改 `astream + extract_json`（复用出题流式范式），SSE `chunk` 逐字流；前端 `Interview.vue` 改对象增量 JSON 解析，feedback/报告摘要逐字显示，移除 JSON 正则过滤。spec：`docs/superpowers/specs/2026-08-02-feedback-streaming-design.md`
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 更新——Phase 2 流式链路规则补丁 + 反馈真流式化状态"
```

---

## Self-Review（规划自检）

**Spec 覆盖对照**：
- evaluate 真流式（feedback 逐字 + score 后置）→ Task 1 + Task 3 ✅
- generate_report 真流式 → Task 2（后端）+ Task 3（前端摘要流式）✅
- 结构化落库不受影响（extract_json 解析 → state → DB）→ Task 1/2 保持原持久化段 ✅
- sse.py 不修改 → 全计划未触碰 sse.py ✅
- 非目标：出题兜底分支不改 → 未包含 ✅
- 测试适配 + 新增 fallback 用例 → Task 1/2 ✅
- CLAUDE.md 规则补丁 → Task 4 ✅

**占位符扫描**：无 TBD/TODO，所有代码步骤均含完整实现。

**类型一致性**：`tryParseStreamObject` 在 Task 3 Step 1 定义并导出、Step 2 import、Step 3 使用，签名一致；`_as_str_list` 在 Task 2 Step 1 定义、Step 1 使用，一致；`AsyncMock(side_effect=async_gen)` 模式在 Task 1/2 的测试中统一使用。
