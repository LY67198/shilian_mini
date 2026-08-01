# P1 越权注入修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `POST /api/v1/interviews/{id}/answer` 的越权注入漏洞——提交回答前校验面试归属权 + 进行中状态，越权/已完成一律返回 404 且不写库。

**Architecture:** 在 API 端点层（`app/api/client/v1/interview.py`）新增模块级辅助函数 `_assert_owned_active`，复用已存在的 `interview_repo.get_by_id_for_user`（按 id + user_id 查询）。`submit_answer` 端点开头、进入 stream 分支之前调用它——因为 `submit_answer` 是 async generator，stream 路径经 `StreamingResponse` 先发 200 再迭代，校验必须在 generator 之外完成才能正确返回 404。

**Tech Stack:** FastAPI + SQLAlchemy AsyncSession + pytest + httpx (ASGITransport)。

**Spec:** `docs/superpowers/specs/2026-08-01-interview-authz-injection-fix-design.md`

**Baseline:** `docker exec shilian-app pytest -m "unit"` 35 passed。

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `app/api/client/v1/interview.py` | Modify | 新增 `_assert_owned_active` 辅助函数 + `submit_answer` 端点开头调用 |
| `tests/unit/test_interview_authorization.py` | Create | 3 个辅助函数单测 + 1 个端点级越权测试 |

**复用（不改动）**：`app/repositories/interview_repo.py:44-65` `get_by_id_for_user`；`app/exceptions/http_exceptions.py:47-50` `NotFoundError`。

**设计要点**：
- 端点级测试用**最小 FastAPI app**（仅 include interview router + APIException handler），而非 `create_app()`——避免静态文件 mount、docs 子应用、CORS 依赖等副作用，且 ASGITransport 不触发 lifespan（不连 DB/Redis/BM25）。断言只依赖真实 router + handler 链，与完整 app 等价。
- 测试通过 `patch.object(interview_repo, "get_by_id_for_user", new=AsyncMock(return_value=...))` 控制归属校验结果——`_assert_owned_active` 只依赖 repo 方法返回值，mock 契约贴近真实调用。

---

### Task 1: `_assert_owned_active` 辅助函数（TDD：先测试后实现）

**Files:**
- Create: `tests/unit/test_interview_authorization.py`
- Modify: `app/api/client/v1/interview.py`（新增辅助函数）

- [ ] **Step 1: 写辅助函数单测**

创建 `tests/unit/test_interview_authorization.py`：

```python
"""/answer 端点越权注入修复 — 单元测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions.http_exceptions import NotFoundError
from app.repositories.interview_repo import interview_repo


class MockInterview:
    """最小 Interview 实例，仅含辅助函数校验用到的字段。"""

    def __init__(self, status: str = "in_progress"):
        self.status = status


@pytest.mark.unit
class TestAssertOwnedActive:
    """_assert_owned_active — 归属权 + 进行中状态校验"""

    async def test_not_found_raises_404(self):
        from app.api.client.v1.interview import _assert_owned_active

        with patch.object(
            interview_repo, "get_by_id_for_user", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(NotFoundError):
                await _assert_owned_active(AsyncMock(), user_id=1, interview_id=99)

    async def test_completed_raises_404(self):
        from app.api.client.v1.interview import _assert_owned_active

        with patch.object(
            interview_repo,
            "get_by_id_for_user",
            new=AsyncMock(return_value=MockInterview(status="completed")),
        ):
            with pytest.raises(NotFoundError):
                await _assert_owned_active(AsyncMock(), user_id=1, interview_id=99)

    async def test_in_progress_passes(self):
        from app.api.client.v1.interview import _assert_owned_active

        with patch.object(
            interview_repo,
            "get_by_id_for_user",
            new=AsyncMock(return_value=MockInterview(status="in_progress")),
        ):
            # 不抛异常即通过
            await _assert_owned_active(AsyncMock(), user_id=1, interview_id=99)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `docker exec shilian-app pytest tests/unit/test_interview_authorization.py -q`
Expected: FAIL — `ImportError: cannot import name '_assert_owned_active'`（辅助函数尚未定义）

- [ ] **Step 3: 实现辅助函数**

在 `app/api/client/v1/interview.py` 的 import 区加入：

```python
from app.exceptions.http_exceptions import NotFoundError
from app.repositories.interview_repo import interview_repo
```

在 `router = APIRouter()` 之后、`@router.post("/start")` 之前插入辅助函数：

```python
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

- [ ] **Step 4: 运行测试验证通过**

Run: `docker exec shilian-app pytest tests/unit/test_interview_authorization.py -q`
Expected: PASS — 3 passed（TestAssertOwnedActive 三个用例）

- [ ] **Step 5: 提交**

```bash
git add app/api/client/v1/interview.py tests/unit/test_interview_authorization.py
git commit -m "feat: add _assert_owned_active guard for interview ownership"
```

---

### Task 2: 端点级越权测试 + `submit_answer` 接线

**Files:**
- Modify: `tests/unit/test_interview_authorization.py`（追加端点级测试类）
- Modify: `app/api/client/v1/interview.py:47-84`（submit_answer 端点开头加一行调用）

- [ ] **Step 1: 写端点级测试（越权返回 404 且不写库）**

在 `tests/unit/test_interview_authorization.py` 文件末尾追加：

```python
@pytest.mark.unit
class TestAnswerEndpointAuthorization:
    """POST /interviews/{id}/answer — 越权请求返回 404 且不写库"""

    async def test_unauthorized_returns_404_no_write(self):
        import httpx
        from fastapi import FastAPI

        from app.api.client.deps import get_current_user
        from app.api.client.v1 import interview as interview_api
        from app.db.session import get_db
        from app.exceptions.http_exceptions import APIException
        from app.schemas.response import ApiResponse

        # 最小 app：只挂载被测路由 + APIException handler
        app = FastAPI()
        app.include_router(interview_api.router, prefix="/api/v1/interviews")

        @app.exception_handler(APIException)
        async def api_exception_handler(request, exc):
            return ApiResponse.failed(
                message=exc.detail,
                body_code=exc.code,
                http_code=exc.status_code,
                data=exc.data,
            )

        # 攻击者身份 + mock 会话
        attacker = AsyncMock()
        attacker.id = 1
        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_current_user] = lambda: attacker
        app.dependency_overrides[get_db] = override_get_db

        # 归属校验：interview_id=99 不属于 user 1 → get_by_id_for_user 返回 None
        with patch.object(
            interview_repo, "get_by_id_for_user", new=AsyncMock(return_value=None)
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/interviews/99/answer",
                    json={"answer": "越权注入消息"},
                )

        assert resp.status_code == 404
        # 越权时不应写入 InterviewMessage（不触发 db.add / db.commit）
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `docker exec shilian-app pytest tests/unit/test_interview_authorization.py -q`
Expected: FAIL — 断言失败（当前无校验，端点会走 `submit_answer` → graph `fetch_context_node` 抛 `ValueError` → 全局 handler 返回 500；`resp.status_code` 为 500，`assert 500 == 404` 失败）

- [ ] **Step 3: `submit_answer` 端点接线**

修改 `app/api/client/v1/interview.py` 的 `submit_answer` 端点（`@router.post("/{interview_id}/answer")`），在 docstring 之后、`if not stream:` 之前插入一行：

```python
    """提交当前题目的回答（?stream=true 启动 SSE 流式评分）"""
    # P1 修复：先校验归属权 + 进行中状态，再写库（此前越权消息会被持久化）
    await _assert_owned_active(db, current_user.id, interview_id)

    if not stream:
```

- [ ] **Step 4: 运行测试验证通过**

Run: `docker exec shilian-app pytest tests/unit/test_interview_authorization.py -q`
Expected: PASS — 4 passed（3 个辅助函数用例 + 1 个端点级用例）

- [ ] **Step 5: 提交**

```bash
git add app/api/client/v1/interview.py tests/unit/test_interview_authorization.py
git commit -m "fix: reject unauthorized /answer with 404 before write"
```

---

### Task 3: 全量回归 + 验证

**Files:** 无代码改动（仅验证）

- [ ] **Step 1: 全量单元测试回归**

Run: `docker exec shilian-app pytest -m "unit" -q`
Expected: PASS — 39 passed（基线 35 + 新增 4，无回归）

- [ ] **Step 2: 关键模块 import 冒烟**

Run: `docker exec shilian-app python -c "from app.api.client.v1 import interview; from app.workflows.interview.service import submit_answer"`
Expected: 无异常（端点 import 链完整，无循环导入）

- [ ] **Step 3: 越权攻击路径手工复现（可选，需运行环境）**

1. `curl -X POST http://localhost:8006/api/v1/interviews/999/answer -H "Authorization: Bearer <任意用户token>" -H "Content-Type: application/json" -d '{"answer":"越权注入"}'`
2. Expected: HTTP 404，响应体 `body_code` 为 1004，`InterviewMessage` 表无新增行（可用 `docker exec shilian-postgres psql -U demo -d ai_interview -c "SELECT count(*) FROM interview_message WHERE interview_id=999;"` 验证）

- [ ] **Step 4: 提交（如有文档改动）**

如 CLAUDE.md「已完成」段需要补记本次修复，提交时**不要 push**：

```bash
git add -f CLAUDE.md
git commit -m "docs: record /answer authz injection fix"
```

> ⚠️ **约定**：`docs/`、`CLAUDE.md` 等设计文档只提交本地 dev，**绝不 push 远程**。代码改动（Task 1-3 前两步）如需同步远程，按用户指示单独 push（且需确认不携带设计文档提交）。

---

## 验证汇总

- `pytest tests/unit/test_interview_authorization.py -q` → 4 passed
- `pytest -m "unit" -q` → 39 passed，无回归
- 越权 POST → 404 + `body_code=1004`，InterviewMessage 不写入
- 已完成面试 POST → 404（同样被 `_assert_owned_active` 拦截）
- 本人 in_progress 面试 → 行为无回归（校验通过后照常走 graph 流式评分）
