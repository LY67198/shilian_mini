# 面试反馈真流式化 — 设计文档

日期：2026-08-02
状态：已确认（用户批准）
范围：后端 evaluate/generate_report 节点 + 前端 submitAnswerStream 增量渲染

## 背景与动机

面试提交回答后的"反馈"（评分反馈 + 评估报告）目前**不是 token 级流式输出**：
用户在 `POST /interviews/{id}/answer?stream=true` 提交回答后，SSE 只有
`status("正在评估回答...")` →（静默数秒~数十秒）→ 一次性 `score` / `report` 事件。
等待期间前端无任何增量内容，表现为"卡死"假象。

**根因**：`evaluate` 与 `generate_report` 节点使用
`llm.with_structured_output(...)` + `chain.ainvoke()`。`ainvoke` 不产生
`on_chat_model_stream` 事件，而 SSE 的 `chunk` 事件只来源于该事件
（`app/workflows/_shared/sse.py`）。结构化输出要求 LLM 返回完整合法 JSON
再解析，与逐 token 流式存在结构性冲突。

**用户决策（2026-08-02）**：要真流式（token 级），不要"状态细化"折中方案。

## 现状：项目已有成熟流式范式

出题链路已实现真流式，本次反馈流式化**复制该范式**而非从零造轮子：

- 后端：`ai_service.select_and_adapt_questions_stream`
  （`app/services/client/ai_service.py:251-303`）
  - 不用 `with_structured_output`，用 `chain = prompt | llm` + `chain.astream()` 逐 token
  - 累积 chunks → 最后 `extract_json` 一次性解析出结构化结果
- 前端：`startInterviewStream` + `tryParseQuestions`
  （`src/api/interview.js:56-92`）——"累积 jsonBuffer + 增量 JSON 解析"

## 目标与非目标

### 目标
1. `evaluate` 评分反馈真流式：feedback 评语逐字出现，score 最后跳动
2. `generate_report` 报告真流式：报告内容逐字出现
3. 结构化落库不受影响（score/feedback/report 仍强类型写入 DB）
4. `sse.py` 不修改

### 非目标（本次不做）
- 出题兜底分支（题库不足时 AI 补全 / 纯 AI 生成）的流式化——保留现状，后续另议
- token 级出题（题库充分分支已流式，不涉及）

## 设计

### 1. 后端：两个节点改 `astream`

**共同模式**（evaluate 与 generate_report 一致）：

```
chain = prompt | llm.bind(response_format={"type": "json_object"})
async for chunk in chain.astream(input=variables):
    if chunk.content: chunks.append(chunk.content)
# 结束时用 extract_json 解析 → 返回结构化 dict 写回 state → DB
```

关键决策：
- **保留 `response_format={"type": "json_object"}`**（等价现有 json_mode）：
  保证 DeepSeek 输出合法 JSON，`extract_json` 解析稳定，score/report 字段齐全。
  用 `llm.bind(...)` 而非 `with_structured_output`，因为后者包装后 astream
  行为不可控，前者让底层的 `on_chat_model_stream` 事件正常触发。
- **节点内累积 chunks + `extract_json` 最后解析**：与出题链路完全一致。
  解析失败走 `json_utils.extract_json` 既有 fallback（不抛异常）。
- **返回结构化 dict 给 state**（evaluate 返回 `score`/`feedback`，
  generate_report 返回 `overall_score`/`report`/`all_scores`/`is_finished`），
  落库逻辑（`interview_repo.update_result` / `InterviewMessage.score`）不变。

**evaluate_node 具体改动**（`app/workflows/interview/nodes/evaluate.py`）：
- 移除 `structured_llm = llm.with_structured_output(ScoreResult, method="json_mode")`
- 改为 `llm.bind(response_format={"type": "json_object"})` + `chain.astream()`
- 累积文本 → `extract_json` → 取 `score`/`feedback`（校验类型，非法回 fallback）
- 其余（chat_history 构建、ref_block/kb_block、persist 到 InterviewMessage）不变

**generate_report_node 具体改动**（`app/workflows/interview/nodes/generate_report.py`）：
- 移除 `with_structured_output(ReportResult, method="json_mode")`
- 同上改为 `astream` + `extract_json` → 组装 `report` dict
- 其余（qa_data 构建、`update_result` 落库）不变

### 2. SSE 事件流（`sse.py` 无需改动）

```
POST /interviews/{id}/answer?stream=true
  → status("正在评估回答...")
  → chunk*   （feedback 评语逐字出现）
  → score    （完整数据：score/feedback/follow_up）
  → status("正在生成评估报告...")
  → chunk*   （报告内容逐字出现）
  → report   （完整报告结构）
  → done
```

机制：节点内 `astream` 触发 `on_chat_model_stream` →
`astream_to_sse`（`sse.py:49-52`）自动发 `chunk`；节点结束
`on_chain_end` → `score`（`sse.py:60-65`）/ `report`（`sse.py:71-75`）事件保留。

### 3. 前端：`submitAnswerStream` 增量渲染

**现状（问题根源之一）**：`Interview.vue` 的 `onChunk` 回调（`Interview.vue:224-236`）
把流式收到的原始文本累积后，用正则**过滤掉所有 JSON 片段**、只保留"JSON 之外的非
结构文本"做打字机显示；`onDone`（`Interview.vue:239-249`）同样过滤后把剩余文本
push 为面试官消息。而 evaluate 用 json_mode（response_format）输出**纯 JSON**，
几乎无非结构文本 → 正则过滤后用户几乎看不到任何流式内容，只能等 `score` 事件。
这正是"反馈不是流式"的前端侧根因。`onChunk` 的 `data.content` 是透传原文，
`submitAnswerStream`（`src/api/interview.js:146-147`）本身无解析逻辑。

改造：
1. 新增**对象型增量 JSON 解析器** `tryParseFeedbackObject(buffer)`：
   - 参照 `tryParseQuestions`（`interview.js:78-92`）的累积模式，但针对对象结构
   - 能解出多少解多少：完整字段先渲染，未闭合的长文本字段（feedback）流式追加
   - 解析失败保留上次成功渲染，不闪断
2. `Interview.vue` 的 `onChunk` 回调：**移除正则过滤逻辑**，改为累积 jsonBuffer →
   调增量解析器 → 从解析结果取 `feedback` 字段流式显示为 interviewer 消息
3. `score` / `report` 事件到达时，以权威完整数据覆盖增量内容（与落库一致），
   `onDone` 现有 push 逻辑（`Interview.vue:239-249`）保留、去掉过滤步骤

展示形态：feedback 评语逐字出现 → 分数最后跳动 → 报告逐字出现 → 完整报告渲染。

### 4. 错误处理

| 场景 | 处理 |
|---|---|
| LLM 流式中断 | 节点内 catch → 用已累积文本尽力 `extract_json`；残缺 → fallback dict（对齐 `json_utils` 语义） |
| 前端增量解析失败 | 保留上次成功渲染，等下一 chunk / 完整事件 |
| 报告生成失败 | 沿用现有 `"报告生成失败"` fallback dict |
| 非流式路径（`stream=false`） | 不受影响：仍走 `ainvoke` 现有逻辑，但节点已是 astream 实现，需统一（见下） |

**注意**：改造后节点不再区分 stream 与否——`astream` 是唯一 LLM 调用方式。
非流式场景（`submit_answer(stream=False)`，`service.py:85,94` 走 `graph.ainvoke`）
时节点内 `astream` 也会被 `astream_events` 之外的普通执行收集完成，
返回结果与现状一致，无需分支。需在实施时验证 `ainvoke` 下节点内 `astream`
能正常跑完并返回 state（LangGraph 节点内 async for 消费 generator 即可）。

### 5. 测试计划

- `evaluate_node` 单测：mock `get_chat_llm` 返回 astream 产出预置 JSON 片段的
  FakeLLM，断言：返回 dict 含 score/feedback、落库到 InterviewMessage
- `generate_report_node` 单测：同上，断言 report 组装 + `update_result` 落库
- 解析失败路径：mock LLM 输出非 JSON，断言 fallback
- 现有 mock `ainvoke`/`with_structured_output` 的用例同步更新
- 端到端（手动）：`?stream=true` 观察 chunk 逐字、score/report 完整、非流式路径正常

### 6. 规则更新（CLAUDE.md）

给 Phase 2 硬性规则打补丁，记录设计取舍：

- **流式链路**：允许 `chain.astream() 累积 + extract_json`（与出题链路一致）——
  这是真流式展示的必要手段，结构化结果仍由 extract_json + Pydantic 校验保证
- **非流式链路**：仍强制 `with_structured_output`
- 文档明确：evaluate / generate_report 属于流式链路

## 风险与权衡

1. **JSON 解析健壮性**：LLM 偶发输出不干净 JSON → `extract_json` fallback
   兜底（已有），feedback 缺失时回退评分 5.0 + 提示，不阻断流程
2. **`response_format` json_object 约束**：DeepSeek 要求 prompt 含 JSON 相关提示词，
   现有 prompt 需确认满足（实施时检查 evaluator_agent / report_agent 的 yaml）
3. **前端增量解析复杂度**：对象结构 + 长文本字段，实现需谨慎；
   解析失败保留上次渲染保证不闪断
4. **回归面**：改两个节点 + 前端一个函数，涉及现有单测更新；
   非流式路径需回归验证

## 涉及文件

| 文件 | 改动 |
|---|---|
| `app/workflows/interview/nodes/evaluate.py` | `ainvoke` → `astream` + `extract_json` |
| `app/workflows/interview/nodes/generate_report.py` | 同上 |
| `ai-interview-frontend/src/api/interview.js` | `submitAnswerStream` 增量解析 + 新增对象解析器 |
| `ai-interview-frontend/src/views/Interview.vue` | `onChunk`/`onDone` 移除正则过滤，改增量 JSON 解析流式显示 feedback |
| `ai-interview-backend/tests/` | evaluate/report 相关单测适配 |
| `CLAUDE.md` | Phase 2 规则补丁 + 状态更新 |
