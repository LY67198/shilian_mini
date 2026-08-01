# 设计：出题链路 SSE 流式化（感知提速）

日期：2026-08-01
状态：已确认，待实施
关联：出题链路提速（`5d2df5e`，63s→16s）的体验侧收尾

## 背景与动机

`POST /interviews/start` 的 16s 等待中，~14s 花在一次 DeepSeek 选题 LLM 调用
（`ai_service.select_and_adapt_questions`，`chain.ainvoke` 一次性阻塞）。该调用非流式，
前端 `ResumeUpload.vue:handleStart()` 在等待期间只显示一个**假进度条动画**（匀速推进，
非真实进度），用户感知为"全黑等待"。

答题链路已有成熟的 SSE 模式可复用：
- 后端：`app/api/client/v1/interview.py:89-107`（`StreamingResponse` + `X-Accel-Buffering: no`）
- 前端：`app/.../src/api/interview.js:12-84`（fetch + ReadableStream + SSE 事件解析）
- 事件封装：`app/workflows/_shared/sse.py:_sse()`

## 目标与非目标

**目标**：出题环节改为 SSE 流式输出，让用户看到题目**逐条浮现**，消除 16s 假进度条等待感。

**非目标（YAGNI）**：
- ❌ 不改变出题实际耗时（~16s 不变，流式只改善感知）
- ❌ 不碰答题链路的流式（已存在，`submit_answer?stream=true`）
- ❌ position_agent 入口 B 不流式化（它调 service 非流式路径）
- ❌ 不并行化 RAG 管线（pipeline 内 vector/BM25 实际串行，属后续独立优化项）

## 方案选择

已确认 **方案 A：同端点 `?stream=true` + 全程 SSE**。

- 与 `submit_answer?stream=true` 模式一致
- 非流式路径完全保留（position_agent 入口 B、现有测试零回归）
- RAG 检索阶段也推 `status` 事件，全程有反馈

流式粒度：**token 逐字流出**（`chain.astream` 逐 token 推 `chunk` 事件）。
前端展示：**实时解析 buffer → 题目浮现**（LLM 输出是 JSON 数组，直接显示原始 token 观感差，
改为前端增量 `JSON.parse`，解析出完整题目就渲染卡片）。

## 数据流

```
ResumeUpload.vue handleStart()
  → startInterviewStream(data, onStatus, onQuestion, onDone)
  → fetch POST /interviews/start?stream=true
  → interview_service.start_interview_stream()          // async generator
      ① 简历归属 + 解析状态校验（复用现有逻辑）
      ② RAG 检索（embedding + BM25 + rerank）
         → yield SSE status   "正在检索题库..."
      ③ 候选判定（cnt≥N / 0<cnt<N / cnt==0）
         主分支 → select_and_adapt_questions_stream()   // chain.astream 逐 token
                   → yield SSE chunk × N（真实生成过程）
         兜底分支 → ainvoke 一次性生成（无 token 流，直接得结果）
      ④ _merge_selected_questions 补参考答案（纯内存，按 bank_id 补齐）
      ⑤ increment_use_count + 创建 Interview + 首条 InterviewMessage（DB 写入）
      ⑥ yield SSE done {interview_id, first_question, question_index, total_questions}
  → 前端 done → 进度 100 → router.push(`/interview/${interview_id}`)
```

## 后端改动（3 个文件）

### A. `app/services/client/ai_service.py` — 新增 `select_and_adapt_questions_stream()`

与 v2 `select_and_adapt_questions`（行 197-249）同 prompt / 输入 / 兜底，唯一差异是
`chain.ainvoke` → `chain.astream`。产出二元组序列：`("token", str)` 逐 token 推送，
收齐后 `("result", list)` 返回合并结果。

```python
async def select_and_adapt_questions_stream(
    self, candidates, parsed_resume, target_position, difficulty, target_n,
):
    """v2 选题的流式版：token 逐字产出 + 最终合并结果。"""
    is_intern = any(kw in target_position for kw in ["实习", "intern", "Intern"])
    intern_hint = ("候选人为实习岗位，优先选择基础类、项目类问题。" if is_intern else "")
    slim_candidates = [
        {"id": c.get("id"), "question": c.get("question")} for c in candidates
    ]
    prompt = load_prompt("question_select")
    llm = get_chat_llm(temperature=0.3)
    chain = prompt | llm

    chunks: list[str] = []
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

    content = "".join(chunks)
    selected = self._as_question_list(self._extract_json(content))
    yield ("result", self._merge_selected_questions(selected, candidates, target_n))
```

复用现有 `_extract_json` / `_as_question_list` / `_merge_selected_questions`，零新增逻辑。

### B. `app/services/client/interview_service.py` — 拆分 + 新增流式入口

1. **抽取 `_prepare_questions()`**：把 `_generate_questions_with_rag`（行 47-192）里的
   RAG 检索 + 候选判定 + 兜底分支选择拆出，返回 `(branch, candidates)`。
   非流式 `_generate_questions_with_rag` 改为调用它 + 原 LLM 调用（行为不变）。
2. **新增 `start_interview_stream()`** async generator，产出 SSE 字符串：
   - 简历归属 + 解析状态校验（复用 `start_interview` 行 222-239 逻辑）
   - `yield _sse("status", {"message": "正在检索题库..."})`
   - `_prepare_questions()` → 分支处理：
     - 主分支：`async for kind, payload in ai_service.select_and_adapt_questions_stream(...)`：
       `kind == "token"` → `yield _sse("chunk", {"content": payload})`；
       `kind == "result"` → `questions = payload`
     - 兜底分支（`generate_with_seeds` / `generate_questions`）：保持 `ainvoke` 非流式，
       `questions = await ...`
   - DB 写入（increment_use_count / Interview / 首条 Message，复用 `start_interview` 行 250-279）
   - `yield _sse("done", {"interview_id": ..., "first_question": ..., "question_index": 0,
     "total_questions": ...})`——字段与现有 `start_interview` 返回（行 281-286）一致

### C. `app/api/client/v1/interview.py` — 路由加 `?stream`

`POST /start` 加 `stream: bool = Query(False)`，照抄 `submit_answer`（行 89-107）的 SSE 模式：

```python
@router.post("/start")
async def start_interview(
    data: InterviewStart,
    stream: bool = Query(default=False, description="是否使用 SSE 流式出题"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    interview_service: InterviewService = Depends(get_interview_service),
):
    if stream:
        async def event_generator():
            async for sse_str in interview_service.start_interview_stream(
                db=db, user_id=current_user.id, resume_id=data.resume_id,
                target_position=data.target_position, difficulty=data.difficulty,
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
        db=db, user_id=current_user.id, resume_id=data.resume_id,
        target_position=data.target_position, difficulty=data.difficulty,
        total_questions=data.total_questions,
    )
    return ApiResponse.success(data=result)
```

## 前端改动（2 个文件）

### A. `ai-interview-frontend/src/api/interview.js` — 新增 `startInterviewStream()`

复用 `submitAnswerStream`（行 12-84）的 SSE 解析循环（fetch + ReadableStream + `event:`/`data:`/空行分隔），
核心差异是 **chunk 增量 JSON 解析**：

```js
export async function startInterviewStream(data, onStatus, onQuestion, onDone, signal) {
  const authStore = (await import('../stores/auth')).useAuthStore()
  const response = await fetch('/api/v1/interviews/start?stream=true', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authStore.token}` },
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
        if (line === '') { currentEvent = ''; continue }
        if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim() }
        else if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6))
          switch (currentEvent) {
            case 'status': onStatus(data.message); break
            case 'chunk': {
              jsonBuffer += data.content   // 追加到 JSON 累积缓冲，增量解析
              const parsed = tryParseQuestions(jsonBuffer)
              if (parsed && parsed.length) onQuestion(parsed)
              break
            }
            case 'error': throw new Error(data.message || data.error || 'Unknown error')
            case 'done': onDone(data); break
          }
        }
      }
    }
  } finally { try { reader.releaseLock() } catch (_) {} }
}

function tryParseQuestions(buffer) {
  const trimmed = buffer.trim()
  if (!trimmed) return null
  try { return JSON.parse(trimmed) } catch {}
  if (trimmed.endsWith(']')) return null
  try {
    const arr = JSON.parse(trimmed + ']')   // 补右括号提前解出已完整对象
    return Array.isArray(arr) ? arr : null
  } catch { return null }                    // 末尾对象未闭合 → 保留上次结果
}
```

注意：`lineBuffer`（SSE 行拆分）与 `jsonBuffer`（LLM 输出 JSON 累积）**必须分开两个变量**——
`chunk` 事件推的是 LLM 输出 token，不能混入 SSE 行解析缓冲。

### B. `ai-interview-frontend/src/views/ResumeUpload.vue` — `handleStart()` 改用流式

- `startInterview(...)` → `startInterviewStream(...)`，`onStatus` 更新提示文案（"正在检索题库..."
  → "正在生成面试题..."），`onQuestion` 逐条渲染题目卡片，`done` 后进度 100 → 跳转 `/interview/{id}`
- 保留 `startStartingAnimation` 作为整体进度骨架，题目卡片区并行渲染
- `error` 分支：stopStartingAnimation + error 提示 + 回退步骤 2（现状一致）

## 错误处理

| 故障 | 处理 |
|---|---|
| `chain.astream` 中断/异常 | generator 内 catch → 走 v2 同款回退（候选前 N 题）→ done 仍带题目 |
| RAG 失败 / 候选为空 | 走 `generate_questions` 兜底（非流式 ainvoke），前端只见 status + done |
| 整体失败 | yield `_sse("error", ...)` → 前端 catch → 回退步骤 2（现状一致） |
| 前端取消/刷新 | `signal` AbortController 断开连接 → 后端 generator 随连接终止 |

## 测试计划

- **单元**：`select_and_adapt_questions_stream` 的 token 聚合 + `_extract_json` /
  `_merge_selected_questions` 正确性（mock `chain.astream` 返回分片 token，模拟中途截断）
- **单元**：`tryParseQuestions` 增量解析算法——后端用**等价的 Python 单测验证算法**，
  前端 JS 复用同逻辑（前端无测试框架）
- **集成**（mock LLM）：`start_interview_stream` 事件序列 = `status` → `chunk`×N → `done`，
  `done` 字段与现有 `start_interview` 返回一致（`interview_id/first_question/question_index/total_questions`）
- **回归**：`start_interview`（非流式）现有测试全过；position_agent 入口 B 不受影响

## 文件改动清单

| 文件 | 改动 |
|---|---|
| `app/services/client/ai_service.py` | 新增 `select_and_adapt_questions_stream()` |
| `app/services/client/interview_service.py` | 抽取 `_prepare_questions()`；新增 `start_interview_stream()` |
| `app/api/client/v1/interview.py` | `/start` 加 `?stream=true` SSE 分支 |
| `ai-interview-frontend/src/api/interview.js` | 新增 `startInterviewStream()` + `tryParseQuestions()` |
| `ai-interview-frontend/src/views/ResumeUpload.vue` | `handleStart()` 改用流式 + 题目浮现区 |
| `tests/`（新增） | 流式出题单测 + 集成测试 |
