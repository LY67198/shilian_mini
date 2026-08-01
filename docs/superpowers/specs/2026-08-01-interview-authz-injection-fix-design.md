# P1 越权注入修复 — Design Doc

**日期**：2026-08-01
**状态**：Approved
**范围**：仅修复 `/answer` 端点的越权注入 bug（审计 P1 项）。**不并入** Topic 2/3（retrieval_check + 死代码清理，已有独立 spec），**不处理** check_finished 越界 / evaluate 吞异常 / 报告分数回退泄漏（各为独立审计项）。

---

## 背景

2026-08-01 五路并行审计确认一个 P1 越权问题：任意登录用户可向他人面试注入 candidate 消息。

**漏洞链路**：
1. `POST /api/v1/interviews/{interview_id}/answer` 端点（`app/api/client/v1/interview.py:47-48`）只做 JWT 认证（`get_current_user`），**无归属校验**
2. `submit_answer()`（`app/workflows/interview/service.py:41-48`）**先写入** `InterviewMessage(interview_id, role="candidate", content=answer)` 并 `await db.commit()`
3. **之后** graph 内部 `fetch_context_node`（`app/workflows/interview/nodes/fetch_context.py:39-41`）才通过 `interview_repo.get_by_id_for_user()` 校验归属 → 越权时抛 `ValueError` → 端点 500

**后果**：
- 越权消息**已持久化**后才报错，攻击者可污染他人面试记录
- `generate_report` 的 `get_scored_messages()` 会把注入的"未回答"消息计入 → 报告失真
- 报 500 而非 404/403，泄露行为异常

**对比**：`get_report` / `get_messages` / `delete` 端点均通过 `interview_service` 做了归属校验，唯独 `/answer` 没有。

## 设计决策（头脑风暴确认）

| # | 决策 | 理由 |
|---|------|------|
| 1 | 越权返回 **NotFoundError 404** | 与 get_report/get_messages/delete 现有端点完全一致；不泄露 interview_id 是否存在 |
| 2 | 已完成面试（status=completed）同样返回 **404** | 统一外部行为、实现最简 |
| 3 | 校验放 **API 端点层** | `submit_answer` 是 async generator，stream 路径经 `StreamingResponse` **先发 200 再迭代 generator**；若校验在 generator 内抛 404，会因响应已开始而无法传播。端点层校验在进入 stream 分支之前完成，stream/non-stream 都能正确返 404 |
| 4 | 方案 A：端点层模块级辅助函数 `_assert_owned_active` | 复用已存在的 `interview_repo.get_by_id_for_user`；diff 最小；可单测 |
| 5 | 测试粒度：辅助函数单测 + 端点级测试 | 避免重蹈幂等测试"只测 repo 不测实际行为"的虚假信心教训 |

## 修复设计

### 文件 1：`app/api/client/v1/interview.py` — 新增辅助函数

```python
from app.exceptions.http_exceptions import NotFoundError
from app.repositories.interview_repo import interview_repo


async def _assert_owned_active(
    db: AsyncSession, user_id: int, interview_id: int
) -> None:
    """校验面试归属权 + 进行中状态。

    Args:
        db: 数据库会话。
        user_id: 当前用户 ID。
        interview_id: 面试主键。

    Raises:
        NotFoundError: 面试不存在、不属于当前用户、或已 completed。
    """
    interview = await interview_repo.get_by_id_for_user(db, interview_id, user_id)
    if not interview or interview.status != "in_progress":
        raise NotFoundError(message="面试记录不存在")
```

> 复用 `InterviewRepository.get_by_id_for_user`（`app/repositories/interview_repo.py:44-65`，按 id + user_id 查面试），无需新增 repo 方法。

### 文件 2：`app/api/client/v1/interview.py` — submit_answer 端点开头调用

在 `submit_answer` handler（第 48 行）开头、进入 `if not stream:` 分支**之前**插入：

```python
@router.post("/{interview_id}/answer")
async def submit_answer(
    interview_id: int,
    data: AnswerSubmit,
    stream: bool = Query(default=False, description="是否使用 SSE 流式返回"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交当前题目的回答（?stream=true 启动 SSE 流式评分）"""
    # P1 修复：先校验归属权 + 进行中状态，再写库（此前越权消息会被持久化）
    await _assert_owned_active(db, current_user.id, interview_id)

    if not stream:
        async for result in submit_answer_to_graph(...):
            return ApiResponse.success(data=result)
    ...
```

**关键点**：校验在 stream 分支之前 → `StreamingResponse` 尚未创建，404 能正常返回；越权时 `InterviewMessage` 永不写入。

### 不改动的部分

- `app/workflows/interview/service.py` — 不动（端点层已拦截；graph 内 `fetch_context_node` 保留的校验构成纵深防御）
- `app/workflows/interview/nodes/fetch_context.py` — 不动
- 无 schema / 无前端 / 无迁移 / 无新增依赖

## 测试

新增 `tests/unit/test_interview_authorization.py`：

### TestAssertOwnedActive（3 用例，mock session，贴合现有 mock 风格）

1. **not_found_raises_404**：`get_by_id_for_user` 返回 None → `pytest.raises(NotFoundError)`
2. **completed_raises_404**：interview 存在但 `status="completed"` → `pytest.raises(NotFoundError)`
3. **in_progress_passes**：`status="in_progress"` → 不抛异常

mock 方式：仿照 `tests/unit/test_position_agent_idempotency.py` 的 MockResult / MockSession（`execute` 返回 `scalar_one_or_none()`），或直接 `unittest.mock.patch` 掉 `interview_repo.get_by_id_for_user`。**用 patch 更贴近真实调用契约**（辅助函数只依赖 repo 方法返回值）。

### TestAnswerEndpointAuthorization（1 端点级用例）

**越权请求返回 404 且不写库**：
- 构造 app：`create_app()`（`app/route/route.py:78`，ENV 默认 development，CORS 通配，可正常构造）
- `app.dependency_overrides[get_current_user]` → 返回攻击者 User（mock，`id=1`）
- `app.dependency_overrides[get_db]` → 返回 mock session，`get_by_id_for_user` 返回 None（模拟 interview 不属于该用户）
- `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")` → `POST /api/v1/interviews/99/answer` `{"answer": "攻击注入"}` → 断言 `status_code == 404`
- 断言 `mock_db.add` / `commit` **从未被调用**（InterviewMessage 未写入）

> 注：ASGITransport 不触发 FastAPI lifespan（不连 DB/Redis/BM25），测试无外部依赖。`/api/v1` 前缀来自 `settings.API_V1_STR`（默认 `/api/v1`），路由已注册在 router_registry。

## 行为

- 越权提交（他人 interview_id）→ **404**，InterviewMessage 不写入，攻击者无法污染他人面试
- 已 completed 面试提交 → **404**，同上一律不写库
- 本人 in_progress 面试 → 照常走 `submit_answer` graph 流式评分，**行为无回归**

## 影响范围汇总

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `app/api/client/v1/interview.py` | +2 import + ~12 行辅助函数 + 1 行调用 | `_assert_owned_active` + submit_answer 开头校验 |
| `tests/unit/test_interview_authorization.py` | 新增 ~120 行 | 3 个辅助函数用例 + 1 个端点级用例 |

- **无新增依赖**（httpx 已依赖）
- **无数据库迁移**
- **无 API 契约变更**（行为从 500 改为 404，属修复）
- **无前端变更**

## 验证

- `docker exec shilian-app pytest tests/unit/test_interview_authorization.py -q` → 4 passed
- `docker exec shilian-app pytest -m "unit" -q` → 基线 35 用例 → 39 用例，无回归
- 越权攻击路径复现：越权 POST → 404，InterviewMessage 不写入

## 后续可选项（本次不做）

- 若未来引入新的 `submit_answer` 调用入口，需在端点层同步加校验（当前仅 1 个入口）
- `build_candidate_profile` prompt 硬编码、`extract_json` 数组兜底等审计 info 项另行处理
