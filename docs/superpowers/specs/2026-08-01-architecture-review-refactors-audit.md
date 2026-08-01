# 架构评审三话题深度审计 — 2026-08-01

**日期**：2026-08-01
**来源**：3 路并行深度审计（Workflow，共 96 次工具调用）
**用途**：为"待办"三话题（岗位匹配幂等 / retrieval_check 降级 / 死代码清理）的下次设计提供代码事实、风险与推荐方案。

---

## Topic 1：岗位匹配 Agent — 审计要点

> 结论已改判：**保留 Agent 不拆壳**，仅修重复建面试 bug。设计见 `2026-08-01-position-agent-interview-idempotency-design.md`。

**代码事实**：
- `create_agent` 唯一真 Agent：`app/services/client/position_agent_service.py:46-57`（注意实际路径是 `services/client/`，CLAUDE.md 目录树写的 `services/`）
- Prompt 锁死顺序（`app/prompts/position_agent_system.yaml:7,17`）：get_parsed_resume → build_candidate_profile → match_positions → get_position_interview_focus → **输出最终 JSON**
- **关键矛盾**：`start_mock_interview` 注册在 `POSITION_AGENT_TOOLS`（position_agent_tools.py:314-320）但 prompt 从不指示调用 —— 无前置校验、无幂等保护的 DB 写工具悬空
- `/start-interview` 端点（`app/api/client/v1/position_agent.py:98`）直接 `start_mock_interview.ainvoke()` 绕过 Agent，无幂等保护
- 前端实际完整驱动：`PositionMatch.vue` runMatch → 展示 Top3 → 用户点选 → startInterview；按钮已防双击（`PositionMatch.vue:156` 点击即 `:disabled`）
- `build_candidate_profile` 内联硬编码 Python prompt（position_agent_tools.py:86-107），违反 YAML 规则
- **零测试覆盖**：ReAct 循环、工具链、DB 副作用、两个端点全无测试

**重复建面试根因**（topic 1 修复对象）：
- `start_mock_interview` 每次调用 `interview_service.start_interview`（interview_service.py:194-286）→ INSERT Interview + InterviewMessage + 递增 `question_bank.use_count`
- `Interview` 表无 `(user_id, status)` 唯一约束（models/interview.py:24-31）
- `start_interview` 有两个调用者：position_agent_tools.py:297 与 `app/api/client/v1/interview.py:36`（通用 `/interview/start`）

---

## Topic 2：retrieval_check 降级 — 审计要点

**一句话结论**：自包含的有界循环（最多 2 次重写、reason 引导），可折叠为普通 async while-loop，**近零损失**；`retry_reason` 反馈是 prompt 级信号而非图基础设施，且当前被调用方丢弃。

**代码事实**：
- 结构：`workflows/retrieval_check/` graph.py（START→retrieve→check_sufficiency→条件路由）+ state.py + 4 nodes + service.py + 2 YAML prompts
- `max_retries` 从 `state.custom.max_retries` 读取，默认 2；`retry_count >= max_retries` 强制 format（graph.py:72）
- 语义细节（折叠时必须精确复刻）：
  - 空结果 → `is_sufficient=False, retry_reason='too_few'` 短路（check_sufficiency.py:35-39）
  - LLM check 失败 → fallback `is_sufficient=True/'ok'`，永不阻断（check_sufficiency.py:61-66）
  - rewrite 失败 → 保留原 query（rewrite_query.py:46-48）
  - `retry_count` 在 rewrite 中自增（rewrite_query.py:50）；max_retries=2 → 最多 3 检索轮 + 2 重写
  - format_context 跨轮按内容前缀[:100] 去重 + 按 score 降序（format_context.py:25-35）
- **嵌套图调用**：`service.py:89 graph.ainvoke(initial_state)`（无 config），retrieve_knowledge_node 以 `graph.ainvoke()` 命令式触发 → 断 trace
- **唯一调用者**：`workflows/interview/nodes/retrieve_knowledge.py:31-42`，只消费 `result.final_context`，**丢弃 debug_info**
- **门控 bug**：`interview/service.py:127-152` `_build_retrieval_check_service` 在 HITL resume（checkpoint 非 None）时返回 None → 只有**第一题**有知识注入，后续问题 `knowledge_context=[]`
- 测试：`tests/unit/test_retrieval_check_nodes.py`（4 类 5 测，node 级 mock）；路由/max_retries 边界/服务/集成全未覆盖

**推荐方案**：折叠为单个 `self_check_retrieve(query, *, pipeline, max_retries=2, filters=None) -> RetrievalCheckResult` while 循环；保留 2 个 YAML prompt；删 graph.py/state.py/nodes/__init__.py + service.py 的编译图代码（~180 行）；重写测试含 max_retries 边界与 rewrite 触发。

---

## Topic 3：死代码清理 5 项 — 审计要点

**一句话结论**：5 项中 4 项确认为死代码（2 项删除**必须**同步改 `__init__.py`）；rerank 事件循环阻塞是**活的未修复 bug**（非 stale）。

| # | 项 | 证据 | 处理 |
|---|-----|------|------|
| 1 | `get_workflow_llm`（`_shared/llm.py:21-40`） | 空壳，仅委托 get_chat_llm；**零调用者** | 删除 + 改 `_shared/__init__.py:18,26` |
| 2 | `DEFAULT_TOOLS`/`get_default_tools`（`_shared/tools.py`） | 空占位（docstring 标注"占位"）；**零调用者** | 删除 + 改 `_shared/__init__.py:23,34` |
| 3 | 孤儿 prompts `evaluate_answer.yaml` / `generate_report.yaml` | 无 `load_prompt()` 调用；live 用 `evaluator_agent`（evaluate.py:78）/ `report_agent`（generate_report.py:61） | 删除 |
| 4 | `embed_text_sync`/`embed_texts_sync`（`app/llm/embedding.py`） | 均**零调用者**；`embed_texts_sync:108` 调 `aembed_documents` **未 await**（sync 函数内 → TypeError 潜在） | 删除 + 改 `app/llm/__init__.py:14-15,25-26` |
| 5 | `rerank.py:39` 同步 SDK 阻塞 | `cross_encoder_rerank`（async）内直接 `TextReRank.call(...)` 阻塞事件循环；对比 pipeline.py:85 BM25 已 `asyncio.to_thread` | **修复**：`await asyncio.to_thread(...)` + `import asyncio` |

**风险要点**：
- 删 `_shared/llm.py` 或 `tools.py` 不同步改 `_shared/__init__.py` → 每次 import `_shared.*`（interview graph、pipeline tracing 都引）启动即 ImportError
- 删 embedding sync 变体不同步改 `app/llm/__init__.py` → `from app.llm import ...`（ai_service / position_agent_service / evaluate / generate_report 都引）启动即 ImportError
- `deps.py` 不引用上述任何符号 —— DI 层无风险
- 测试全为 mock 覆盖（test_retrieval.py TestRerank 169-215 mock TextReRank；pipeline 测试 237 mock cross_encoder_rerank）→ 修复后期望无测试改动
- rerank 默认开启（pipeline.py:38 enable_rerank=True）→ 每次混合检索都命中该阻塞

**推荐方案**：5 项一次原子通过；完成后 grep 确认各符号零残留引用。
