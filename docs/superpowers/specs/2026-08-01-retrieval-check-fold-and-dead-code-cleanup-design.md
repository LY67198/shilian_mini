# retrieval_check 降级 + 死代码清理 — Design Doc

**日期**：2026-08-01
**状态**：Approved
**范围**：合并处理待办清单 **Topic 2（retrieval_check 降级纯循环）+ Topic 3（死代码清理 6 项）**。**不并入**审计清单中其他独立 bug（越权注入 P1 / check_finished 越界 / evaluate commit 吞异常 / 报告分数回退泄漏），那些单独出 spec。

---

## 背景

2026-08-01 五路并行架构评审确认：

- **Topic 2**：`workflows/retrieval_check/` 的 StateGraph 是**自包含的有界循环**（最多 2 次重写、reason 引导），可折叠为普通 async while-loop，**近零损失**。`retry_reason` 反馈是 prompt 级信号而非图基础设施。审计同时确认两个真实 bug：**`is_first_call` 门控 bug**（HITL resume 后后续问题知识注入为空，P1）与 **`debug_info` 被丢弃**（自检循环零可观测性）。
- **Topic 3**：5 项死代码中 4 项确认为死代码（2 项删除**必须**同步改 `__init__.py`），`rerank.py` 事件循环阻塞是**活的未修复 bug**（P1，线上路径）。审计补查新增第 3 个孤儿 prompt `question_agent.yaml`（QuestionAgent 删除后残留）。

范围决策（头脑风暴确认）：**严格限定 Topic 2 + 3**，其他审计 bug 单独设计。方案选型：**Topic 2 保留包结构 + 类，内部换 while 循环**（方案 A，最小改动）。

## 设计决策（头脑风暴确认）

| # | 决策 | 理由 |
|---|------|------|
| 1 | **Topic 2 保留 `RetrievalCheckService` 类 + 包路径**（方案 A） | import 零破坏、测试契约不变、diff 最小；核心目标（LangGraph 编排 → 纯 Python 循环）与纯函数方案等价达成 |
| 2 | **删除 graph.py / state.py / nodes/**（~180 行脚手架） | 有界循环不需要 LangGraph 基础设施；`retry_reason` 反馈闭环保留在 YAML prompt + while 循环中 |
| 3 | **`submit_answer` 每轮无条件构建 `RetrievalCheckService`**（去掉 `is_first_call` 门控） | BM25 是 lifespan 预构建单例、pipeline `__init__` 纯属性赋值、embeddings 单例 → 每轮构建开销可忽略；且每轮用当前请求的新 db session 更正确 |
| 4 | **`debug_info` 存入 `InterviewState`** | 自检循环从"零可观测性"变为可观测（轮次/重写 query/召回数可经 checkpoint 或日志查看），零 UI 改动 |
| 5 | **Topic 3 六项一次原子通过** | 全部零调用者或 mock 覆盖，同步改 `__init__.py` 后可整体验证；完成后 grep 确认零残留 |

---

## Topic 2：retrieval_check 降级纯循环

### 2.1 文件结构

| 动作 | 文件 |
|------|------|
| 删除 | `app/workflows/retrieval_check/graph.py` |
| 删除 | `app/workflows/retrieval_check/state.py` |
| 删除 | `app/workflows/retrieval_check/nodes/` 整目录（retrieve / check_sufficiency / rewrite_query / format_context + `__init__.py`）|
| 重写 | `app/workflows/retrieval_check/service.py` |
| 更新 docstring | `app/workflows/retrieval_check/__init__.py` |

### 2.2 重写后的 `service.py`

保留对外契约（`retrieve_knowledge_node` 与 `interview/service.py` 的 import 与调用方式不变）：

```python
@dataclass
class RetrievalCheckResult:
    final_context: list[str] = field(default_factory=list)
    debug_info: dict[str, Any] = field(default_factory=dict)

class RetrievalCheckService:
    def __init__(self, pipeline: RetrievalPipeline, max_retries: int = 2): ...
    async def check_and_retrieve(self, query: str, max_retries: int | None = None,
                                 retrieval_filters: dict | None = None) -> RetrievalCheckResult: ...
```

删除：`_graph` 懒编译字段、`StateGraph` / `build_retrieval_check_graph` import。新增 3 个私有辅助函数（模块级，便于单测）：`_check_sufficiency` / `_rewrite_query` / `_format_context`，逻辑平移自原 nodes。

### 2.3 while 循环语义（精确复刻 graph 行为）

```
retry_count = 0; current_query = query; history = []
while True:
    # ① retrieve：异常 → 空结果不抛
    try: results = await pipeline.search(current_query, filters=retrieval_filters)
    except Exception: results = []
    history.append({round: retry_count, query: current_query, results: [...]})

    # ② check：空结果短路 too_few（不调 LLM）；LLM 失败 → (True, 'ok') 不阻断
    if not results: is_sufficient, reason = False, "too_few"
    else:
        try: is_sufficient, reason = await _check_sufficiency(current_query, results)
        except Exception: is_sufficient, reason = True, "ok"

    # ③ 决策（复刻 _route_after_check：graph.py:68-76）
    if is_sufficient or retry_count >= max_retries: break

    # ④ rewrite：失败保原文 query；retry_count += 1 后回到 ①
    try: current_query = await _rewrite_query(query, reason, history)
    except Exception: current_query = query
    retry_count += 1

# ⑤ _format_context：跨轮 content[:100] 去重 + score 降序 + debug_info
return RetrievalCheckResult(final_context, debug_info)
```

**4 个兜底语义 + 边界逐条对上**：

| 语义 | 原实现 | 新循环 |
|------|--------|--------|
| 空结果 | check_sufficiency.py:35-39 → `(False, 'too_few')` 短路 | ② 同 |
| LLM check 失败 | check_sufficiency.py:61-66 → `(True, 'ok')` 永不阻断 | ② except 同 |
| rewrite 失败 | rewrite_query.py:46-48 → 保留原 query | ④ except 同 |
| max_retries=2 边界 | graph.py:71-73 决策在 `retry_count` 自增前判断，`retry_count >= max` 强制 format | ③ 同 → **恰好 3 检索轮 + 2 重写** |
| format 去重/排序 | format_context.py:25-38 → content[:100] 去重 + score 降序 | ⑤ 同 |

### 2.4 is_first_call 门控修复

`app/workflows/interview/service.py`：

- `submit_answer` 中 `checkpointer.aget` 检查**保留**——它仍用于区分 resume vs 初始态（`Command(resume=...)` 分支）
- `_build_retrieval_check_service(session)` **去掉 `is_first_call` 参数**，每轮无条件构建：

```python
def _build_retrieval_check_service(session: AsyncSession):
    knowledge_bm25 = get_knowledge_bm25()
    if not knowledge_bm25 or not session:
        return None
    knowledge_pipeline = RetrievalPipeline(
        session=session, collection="knowledge_chunks",
        bm25_index=knowledge_bm25,
        vector_top_k=settings.VECTOR_TOP_K, bm25_top_k=settings.BM25_TOP_K,
        final_top_k=settings.KNOWLEDGE_TOP_K, enable_rerank=True,
    )
    return RetrievalCheckService(pipeline=knowledge_pipeline,
                                 max_retries=settings.SELF_CHECK_MAX_RETRIES)
```

效果：**每道题（含 HITL resume 后的所有轮次）都有知识注入**，P1 修复。

### 2.5 debug_info 存入 InterviewState

- `app/workflows/interview/state.py`：新增 `retrieval_debug: Optional[dict]`（归入"知识库"注释段，`knowledge_context` 旁）
- `app/workflows/interview/nodes/retrieve_knowledge.py`：返回

```python
return {"knowledge_context": result.final_context,
        "retrieval_debug": result.debug_info}
```

（保留原有 service 为 None / 异常 → 空 `knowledge_context` 的降级路径；`debug_info` 仅在成功时写入）

---

## Topic 3：死代码清理 6 项

| # | 项 | 动作 | 必须同步改 |
|---|-----|------|-----------|
| 1 | `app/workflows/_shared/llm.py`（仅含 `get_workflow_llm`，零调用者）| 整文件删除 | `_shared/__init__.py` 移除 import(:18) + `__all__`(:26) |
| 2 | `app/workflows/_shared/tools.py`（仅含 `DEFAULT_TOOLS`/`get_default_tools`，零调用者）| 整文件删除 | `_shared/__init__.py` 移除 import(:23) + `__all__`(:34) |
| 3 | 孤儿 prompt `evaluate_answer.yaml` / `generate_report.yaml` / `question_agent.yaml`（零 `load_prompt` 调用；live 用 `evaluator_agent` / `report_agent`）| 删除 3 文件 | 无 |
| 4 | `app/llm/embedding.py` 的 `embed_text_sync` / `embed_texts_sync`（零调用者；`embed_texts_sync:108` 含 `aembed_documents` 未 await 笔误）| 删除 2 函数 | `app/llm/__init__.py` 移除 2 import(:14-15) + 2 `__all__`(:25-26) |
| 5 | `app/retrieval/rerank.py:39` 同步 `TextReRank.call` 阻塞事件循环（P1，线上路径，`enable_rerank=True` 默认开启）| **修复** | 无 |

**rerank 修复代码**：

```python
import asyncio
...
resp = await asyncio.to_thread(
    TextReRank.call,
    model=model, query=query, documents=documents, top_n=top_k,
)
```

（对比 `pipeline.py:85` BM25 已 `asyncio.to_thread`；`asyncio.to_thread` 支持 kwargs 传递）

**删除后验证**：grep 确认零残留引用 `get_workflow_llm` / `get_default_tools` / `DEFAULT_TOOLS` / `embed_text_sync` / `embed_texts_sync`。

---

## 测试

### 重写测试文件

`tests/unit/test_retrieval_check_nodes.py` → 重写为 `tests/unit/test_retrieval_check_service.py`，测 while 循环（含 `max_retries` 边界与 rewrite 触发，补审计指出的覆盖缺口）：

| # | 用例 | 断言 |
|---|------|------|
| 1 | 空结果 → `too_few` 触发 rewrite | mock pipeline 恒返回空 → 3 检索轮 + 2 重写，最终 `final_context=[]`，debug_info 轮次=3 |
| 2 | LLM check 失败降级 | mock `_check_sufficiency` 抛异常 → 返回 `(True,'ok')`，1 轮即结束，不 rewrite |
| 3 | rewrite 失败保原文 | mock `_rewrite_query` 抛异常 → `current_query` 保持原文，retry_count 自增 |
| 4 | **max_retries=2 边界** | mock check 恒 insufficient → **恰好 3 检索轮 + 2 重写**，最终走 format |
| 5 | format 去重 + 排序 | 两轮含重复 content[:100] → 去重后唯一，score 降序 |
| 6 | pipeline 异常 | mock search 抛异常 → 空 `final_context` + `debug_info.error` |
| 7 | debug_info 完整 | `rounds` / `final_query` / `total_retrieval_rounds` / `total_unique_results` / `retry_count` 字段齐全 |

（LLM 依赖沿用原测试的 monkeypatch 手法 mock `get_chat_llm` / `load_prompt`，不联网）

### 回归验证

1. `pytest -m unit` 全绿（35 现有 + 新增）
2. 现有 `test_interview_nodes.py` 的 knowledge_context / evaluate 用例不回归
3. import 冒烟：删除 `__init__.py` 引用后 `python -c "import app"` 无 ImportError（interview graph / pipeline tracing / ai_service 均引 `_shared` 与 `app.llm`）
4. grep 零残留：Topic 3 五个符号 + 三个孤儿 prompt 文件名
5. smoke test（容器内）：BM25 索引仍构建（32+84 文档），面试知识检索路径正常

---

## 影响范围汇总

| 文件 | 动作 | 说明 |
|------|------|------|
| `app/workflows/retrieval_check/graph.py` | 删除 | StateGraph 脚手架 |
| `app/workflows/retrieval_check/state.py` | 删除 | RetrievalCheckState |
| `app/workflows/retrieval_check/nodes/` | 删除 5 文件 | 4 nodes + `__init__.py` |
| `app/workflows/retrieval_check/service.py` | 重写 | 类保留，graph.ainvoke → while 循环，新增 3 私有辅助 |
| `app/workflows/retrieval_check/__init__.py` | 更新 docstring | |
| `app/workflows/interview/service.py` | 修改 | `_build_retrieval_check_service` 去 `is_first_call` 参数，无条件构建 |
| `app/workflows/interview/state.py` | 修改 | 新增 `retrieval_debug: Optional[dict]` |
| `app/workflows/interview/nodes/retrieve_knowledge.py` | 修改 | 返回 debug_info |
| `app/workflows/_shared/llm.py` | 删除 | 仅 `get_workflow_llm` |
| `app/workflows/_shared/tools.py` | 删除 | 仅 `DEFAULT_TOOLS`/`get_default_tools` |
| `app/workflows/_shared/__init__.py` | 修改 | 移除 4 行（2 import + 2 `__all__`）|
| `app/prompts/evaluate_answer.yaml` / `generate_report.yaml` / `question_agent.yaml` | 删除 | 孤儿 prompt |
| `app/llm/embedding.py` | 修改 | 删 2 sync 函数 |
| `app/llm/__init__.py` | 修改 | 移除 4 行（2 import + 2 `__all__`）|
| `app/retrieval/rerank.py` | 修改 | `asyncio.to_thread` 修复 |
| `tests/unit/test_retrieval_check_nodes.py` | 重写为 `test_retrieval_check_service.py` | 7 用例 |

- **无新增依赖**
- **无数据库迁移**
- **无 API / 前端变更**
- 净删代码量：约 280 行（retrieval_check 包）+ ~60 行（死代码 + 孤儿 prompt）− 新增 ~40 行（service 重写）− 测试重写

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 删 `_shared/llm.py`/`tools.py` 不同步改 `__init__.py` → 启动 ImportError | 原子完成 + import 冒烟验证（已验证仅 `__init__.py` 间接引用）|
| while 循环语义与 graph 不一致（边界 bug） | 逐条对照兜底语义表 + 测试用例 1/2/3/4 精确锁定 |
| 每轮无条件构建 pipeline 的性能顾虑 | pipeline `__init__` 纯属性赋值，BM25/embeddings 均单例，开销可忽略 |
| `rerank.py` `asyncio.to_thread` 变更引入回归 | 行为等价（仅去阻塞），现有 mock 测试覆盖 fallback 路径 |

## 后续可选项（本次不做）

- 其他审计 bug（越权注入 P1 / check_finished 越界 / evaluate commit 吞异常 / 报告分数回退泄漏）— 单独 spec
- 审计 info 项（`close_checkpointer` 未调用连接池泄漏 / prompt 污染 / `build_candidate_profile` 硬编码 prompt / `extract_json` 数组返回 / BM25-only 无 metadata）— 待排期
