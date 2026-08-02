# 面试中逐题实时生成（出题渐进式 + 逐字流式）— 设计文档

日期：2026-08-02
状态：已确认（用户批准，方案 1）
范围：岗位匹配入口渐进式出题 + 面试页/`ask_question_node` 逐题现场生成（双模兼容）+ 题目逐字流式显示

## 背景与动机

用户在岗位匹配页开始面试时，看到题目**一次性全部生成**，不是逐字输出。调查结论：

1. **入口根因**：用户实测走岗位匹配页
   `PositionMatch.vue:272 → startInterviewFromAgent → POST /position-agent/start-interview`
   （`app/api/client/v1/position_agent.py:77`）→ `start_mock_interview`（`position_agent_tools.py:242`）
   → `interview_service.start_interview`（`interview_service.py:217`）——**完全非流式**，
   一次性生成全部题目落库。
2. **生成模型**：题目启动时一次性全部生成，存 `Interview.questions_data`；
   面试中 `ask_question_node`（`nodes/ask_question.py:33`）只按 `current_index` 取下一题，
   **不逐题生成**；结束判断 `check_finished_node` 用 `current_index+1 >= len(questions)`。
3. **流式粒度**：即使上传页走 `start_interview_stream`，前端 `tryParseQuestions`
   是"整组题目浮现"，不是逐字。

**用户决策（2026-08-02，两次澄清）**：
- 不是"开始前准备面板"，而是**开始面试后、面试进行中**的逐题现场生成；
- 逐题实时生成 + 题目文本**逐字流式**（真流式，非打字机假流式）；
- 范围：**只做岗位匹配入口**（方案 1），上传页预生成路径零改动。

## 现状

**两个出题入口（行为不同）**：
- 上传简历页 `ResumeUpload.vue` → `startInterviewStream` → `POST /interviews/start?stream=true`
  → `start_interview_stream`（`interview_service.py:311`）：真 SSE（status→chunk→done），
  但只有「题库充分」分支流式（`select_and_adapt_questions_stream`，`ai_service.py:251`）；
  前端整组浮现。**保留现状，本次不动。**
- 岗位匹配页（见上）：非流式，一次性生成全部。**本次改造。**

**HITL StateGraph**（`workflows/interview/graph.py`）：`interrupt_after=["ask_question"]`，
链路 `fetch_context → retrieve_knowledge → evaluate → check_finished → ask_question → (resume) fetch_context … → generate_report`。
`fetch_context` 每轮从 DB 重读 `questions_data` 与 `current_question_index`。

## 目标与非目标

### 目标
1. 岗位匹配入口进入面试后，题目**逐题现场生成**并**逐字流式**显示在聊天区
2. 第 1 题由面试页挂载时生成；第 N 题（N≥2）由 `ask_question_node` 在评估后生成
3. `check_finished` 改为按 `total_questions` 判断（对预生成兼容，行为零变化）
4. 结构化落库不受影响：每题生成后 `questions_data` append + 存 InterviewMessage + use_count
5. 上传页预生成路径、现有 `start_interview_stream` 及其测试**零改动**

### 非目标（本次不做）
- 上传页入口的渐进式改造 / `start_interview_stream` 退役
- 兜底分支（`generate_with_seeds` / `generate_questions`）整体流式化——新路径已用
  `generate_next_question_stream` 逐题处理，旧方法保留供上传页
- 前端打字机假流式（用户明确要真流式）

## 设计

### 1. 新增 prompt ×2（`app/prompts/`）

**`question_select_one.yaml`**（题库有候选时选 1 题）：
- system：资深技术面试官为【{target_position}】选题（难度 {difficulty}，当前第 {current_index+1}/{total_questions} 题）。
  从 {candidate_count} 道候选中选 1 道。规则：第 1 题固定自我介绍（无合适可微调一道相关题）；不重复已用 bank_id（{used_bank_ids}）；难度递进；可微调题面但不变本质；**返回单个 JSON 对象（非数组）**：
  `{"index": N, "question": "题面（可微调）", "category": "self-intro/project/technical/coding/system-design", "bank_id": <候选id>}`。
- 输入变量：`target_position` / `difficulty` / `intern_hint` / `current_index` / `total_questions` /
  `used_bank_ids` / `resume_json` / `candidate_count` / `candidates_json`（slim：id+question）。

**`question_generate_one.yaml`**（题库无候选时兜底生成 1 题）：
- system：基于简历/岗位/难度生成 1 题，输出单个 JSON 对象：
  `{"index": N, "question": "...", "category": "...", "reference_answer": "标准答案要点", "source": "ai_fallback"}`。
  要求不重复已问题目（{asked_questions}），第 1 题自我介绍，难度递进。
- 输入变量：`target_position` / `difficulty_desc` / `position_hint` / `current_index` / `total_questions` /
  `asked_questions` / `resume_json`。

### 2. `ai_service.generate_next_question_stream(...)`（`app/services/client/ai_service.py`）

```
async def generate_next_question_stream(candidates, parsed_resume, target_position,
                                        difficulty, current_index, total_questions,
                                        used_bank_ids, chat_history):
    # candidates 非空 → question_select_one；空 → question_generate_one
    chain = prompt | llm
    chunks = []
    try:
        async for chunk in chain.astream(input={...}):
            if chunk.content: chunks.append(chunk.content); yield ("token", chunk.content)
    except Exception as e:
        logger.warning(...)
    content = "".join(chunks)
    result = self._extract_json(content)
    if 选一分支: yield ("result", self._merge_selected_questions([result], candidates, 1)[0])
    else:       归一化（setdefault source/bank_id）; yield ("result", result)
```

- 复用既有 `_extract_json` / `_as_question_list` / `_merge_selected_questions`（`_merge_selected_questions([result], candidates, 1)` 取首项即可）。
- 另加**非流式包装** `generate_next_question(...)`（累积 token 返回 dict），供测试/复用。
- 流式中断：catch 后尽力解析；解析失败走 `extract_json` fallback（不抛异常）。

### 3. `interview_service.generate_next_question_stream(db, user_id, interview_id)`（新端点用）

```
yield _sse("status", {"message": "正在检索题库..."})
校验归属 + in_progress（NotFound/Validation 与 start_interview 一致）
interview = load; resume = load parsed_content
questions = interview.questions_data or []
target_index = len(questions)                       # 下一题落点
candidates = await self._prepare_questions(db, parsed_resume, target_position, difficulty, total_questions)
used_bank_ids = [q["bank_id"] for q in questions if q.get("bank_id")]
chat_history = 从 messages 读
async for kind, payload in ai_service.generate_next_question_stream(...):
    if kind == "token": yield _sse("question_chunk", {"content": payload})
    else: question = payload
# 幂等：target_index 已存在 → 直接返回现有题（重复调用防重）
落库：questions_data.append(question); InterviewMessage(question, target_index);
      current_question_index = target_index; increment_use_count
yield _sse("done", {"index": target_index, "question": question["question"]})
```

### 4. 新端点 `POST /interviews/{id}/next-question?stream=true`（`app/api/client/v1/interview.py`）

- `_assert_owned_active` 校验；StreamingResponse 转发 `interview_service.generate_next_question_stream`。

### 5. `ask_question_node` 双模（`app/workflows/interview/nodes/ask_question.py`）

```
db = config["configurable"]["db"]; next_index = current_index + 1; questions = state["questions"]
if next_index < len(questions):                      # 预生成模式（上传页，现状保留）
    next_question = questions[next_index]["question"]
    update_question_index; create_message; commit; return {...}
else:                                                # 渐进模式（岗位匹配）
    candidates = await interview_service._prepare_questions(db, state["resume_context"], ...)
    used_bank_ids = [q["bank_id"] for q in questions if q.get("bank_id")]
    # token 经 chain.astream 被 astream_events 自动捕获流出（同 evaluate 范式）
    async for kind, payload in ai_service.generate_next_question_stream(...):
        if kind == "result": question = payload
    # 落库：questions_data append + InterviewMessage + update index + increment_use_count
    return {"current_index": next_index, "next_question": question["question"], "index": next_index}
```

- 关键：节点内 `chain.astream` 的 token 由 `astream_to_sse` 的 `on_chat_model_stream` 事件拾取
  （与 evaluate 反馈流式同一机制），节点正常返回最终结果写 state。

### 6. `check_finished_node`（`nodes/check_finished.py`）

- `is_finished = current_index + 1 >= total_questions`（从 state 取 `total_questions`）。
- 预生成场景 `total_questions == len(questions_data)`，行为与现状完全一致。

### 7. `sse.py` 区分出题/反馈 token（`app/workflows/_shared/sse.py`）

```
if kind == "on_chat_model_stream":
    chunk = ...; node = event.get("metadata", {}).get("langgraph_node", "")
    if node == "ask_question":
        yield _sse("question_chunk", {"content": chunk.content})
    else:
        yield _sse("chunk", {"content": chunk.content})
```

- LangGraph 1.2.8 `astream_events` v2 事件携带 `metadata.langgraph_node`。
- **兜底**：若实测 metadata 不可靠，用 `on_chain_start` 维护 `_current_node` 追踪当前节点（图线性，可靠）。

### 8. `position_agent_tools.start_mock_interview` 快建（`position_agent_tools.py:242`）

- `interview_service.start_interview(..., generate_questions=False)`：
  校验简历 + 创建 Interview（`questions_data=[]`、`current_question_index=0`）+ 幂等检查保留；
  不产出题、不写首条 InterviewMessage（由 next-question 端点补）。
  返回 `{interview_id, total_questions}`（**无 first_question**，为 null）。
- `start_interview` 加参数 `generate_questions: bool = True`；`True` 时走现有 `_generate_questions_with_rag` 全量逻辑（上传页/API 非流式路径零变化），`False` 时只建记录、返回不含 first_question。
- 前端 `PositionMatch.vue` 仅校验 `data.interview_id`（现有逻辑已满足），零改动。

### 9. 模型/迁移

- **零改动**：`Interview.questions_data` 已是 JSONB，可为空。

## SSE 事件流

**新端点（第 1 题）**：
```
POST /interviews/{id}/next-question?stream=true
  → status("正在检索题库...")
  → question_chunk*  （题目文本逐字）
  → done{index, question}
```

**submit_answer（第 N 题，N≥2）**（现有连接扩展）：
```
POST /interviews/{id}/answer?stream=true
  → status("正在评估回答...") → chunk*（feedback）→ score
  → status("正在准备下一题...")
  → question_chunk*  （下一题文本逐字）
  → next_question{question, index}
  → done
```

## 前端改动

### `src/api/interview.js`
1. 新增 `generateNextQuestion(interviewId, onStatus, onQuestionChunk, onDone, signal)`：
   fetch `/api/v1/interviews/${id}/next-question?stream=true`，SSE 解析 `status`/`question_chunk`/`error`/`done`；
   非 200 抛错（与 `startInterviewStream` 一致，`onDone`/`onError` 不触发时 UI 不卡死）。
2. `submitAnswerStream` 新增 `onQuestionChunk` 回调与 `question_chunk` 事件分支：
   `chunk`（feedback/report）与 `question_chunk`（题目）用**独立 buffer**，互不串扰。
3. 新增 `extractQuestionText(buffer)`：从累积的题目 JSON 原文提取**最后一个**
   `"question": "..."` 文本前缀（正则 `/"question"\s*:\s*"((?:\\.|[^"\\])*)/g` 取最后一条），
   逐字增长。

### `src/views/Interview.vue`
1. 新增 `streamingQuestion` ref（AI 气泡逐字显示，复用现有 streaming 样式 + 光标闪烁）。
2. **挂载**：getMessages 后判定**当前题缺失**（messages 为空，或最后一条无 `question_index`）
   → 调 `generateNextQuestion`：`onStatus` 显示"正在检索题库..."，`onQuestionChunk` →
   `streamingQuestion` 逐字追加，`onDone` 权威题目 push 进 messages 并清空 `streamingQuestion`。
3. **handleSubmit**：feedback 流处理不变；`submitAnswerStream` 的 `onQuestionChunk` →
   `streamingQuestion` 逐字更新；`next_question`（onDone）→ 权威题目落 messages、清空 `streamingQuestion`。
4. next-question SSE 也接 AbortController（离开页面取消，复用 `abortController`）。

## 错误处理

| 场景 | 处理 |
|---|---|
| next-question SSE `error`（面试不存在/已结束/LLM 失败） | 前端气泡提示 + 输入恢复可用 |
| LLM 流式中断 | 节点/service catch → 尽力 `extract_json`；残缺 → fallback dict |
| select_one 解析失败 | 复用 `_merge_selected_questions` 回退（候选首题，跳过已用） |
| generate_one 解析失败 | `extract_json` fallback dict（`parse_failed`），index 字段兜底 |
| 重复调用 next-question | `target_index` 已有题 → 直接返回现有题（幂等） |
| 上传页路径 | 完全不受影响（`generate_questions=True` 默认） |

## 测试计划

- **后端单元**：
  - `generate_next_question_stream`：select_one 分支 token 序列 + 合并结果；generate_one 分支；candidates 空。
  - `ask_question_node`：渐进分支（index ≥ len 时生成 + 落库 + 返回）；预生成分支回归（index < len 直接取）。
  - `check_finished_node`：`total_questions` 判定（含预生成等价性）。
  - `sse.py`：mock `metadata.langgraph_node`，断言 `question_chunk` vs `chunk` 区分。
  - `start_mock_interview`：`generate_questions=False` 快建（questions_data 空、幂等保留）。
  - 现有 `test_start_stream_route.py` / `test_start_interview_stream.py` **不动**（上传页路径不变）。
- **前端**：`npm run build` 通过。
- **手动 E2E**：岗位匹配 → 开始模拟面试 → 面试页第 1 题逐字生成 → 回答 → feedback 逐字 + 下一题逐字，
  至 N 题后生成报告；验证上传页路径回归无异常。

## 风险与权衡

1. **`ask_question_node` 双模分支**：预生成/渐进两路径并存（约 10 行分支）。
   换来岗位匹配入口渐进式 + 上传页零改动（贴合用户范围决策）。
2. **RAG 检索每题一次**：`_prepare_questions` 每题重跑（约 1-2s，非流式，status 提示掩盖）。
   换取零 schema 改动（不新增 candidates 持久化列）。每题独立检索质量不降。
3. **`langgraph_node` metadata 依赖**：LangGraph 1.2.8 应支持；已备 on_chain_start 兜底方案。
4. **逐字粒度 = LLM token 粒度**：DeepSeek 流式 chunk 可能多字一次到，视觉为"快速打字"，符合预期。
5. **两个入口体验不一致**：上传页仍整组浮现；岗位匹配渐进式。属本次范围决策（方案 1）。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `app/prompts/question_select_one.yaml` | 新增：候选选 1 题（单对象 JSON） |
| `app/prompts/question_generate_one.yaml` | 新增：兜底生成 1 题（单对象 JSON） |
| `app/services/client/ai_service.py` | 新增 `generate_next_question_stream` + 非流式包装 |
| `app/services/client/interview_service.py` | `start_interview` 加 `generate_questions` 参数；新增 `generate_next_question_stream` |
| `app/api/client/v1/interview.py` | 新增 `POST /interviews/{id}/next-question?stream=true` |
| `app/workflows/interview/nodes/ask_question.py` | 双模：预生成沿用 + 渐进现场生成落库 |
| `app/workflows/interview/nodes/check_finished.py` | `len(questions)` → `total_questions` |
| `app/workflows/_shared/sse.py` | `on_chat_model_stream` 按 `langgraph_node` 区分 `question_chunk`/`chunk` |
| `app/services/client/position_agent_tools.py` | `start_mock_interview` 快建（`generate_questions=False`） |
| `ai-interview-frontend/src/api/interview.js` | 新增 `generateNextQuestion` + `extractQuestionText`；`submitAnswerStream` 支持 `question_chunk` |
| `ai-interview-frontend/src/views/Interview.vue` | 挂载生成第 1 题 + `streamingQuestion` 逐字显示 |
| `ai-interview-backend/tests/` | 上述新增/适配单测 |
| `CLAUDE.md` | 状态更新 + 规则补丁（如需要） |
