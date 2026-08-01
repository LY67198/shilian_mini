# 岗位匹配流程幂等修复 — Design Doc

**日期**：2026-08-01
**状态**：Approved
**范围**：仅修复重复创建模拟面试 bug —— `start_mock_interview` 幂等。**不拆岗位匹配 Agent 壳**。

---

## 背景

2026-08-01 架构评审指出岗位匹配是"固定 DAG 伪装成 Agent"（`create_agent` 全仓库唯一真 Agent）。原计划 Route B 拆壳，但头脑风暴后**决定保留 Agent 逻辑不动**：

- 岗位匹配 Agent 是项目的功能卖点与面试叙事的一部分
- 拆壳是结构性重构，有回归风险（前端依赖 `intermediate_steps` 时间线、ReAct 响应契约）
- 系统当前可正常运行，改动收益低于风险

审计同时确认了与 Agent 逻辑无关的**真 bug**：`start_mock_interview` 无幂等保护，重复触发会创建多份相同面试。本 spec 只修这个 bug。

## 根因

`start_mock_interview`（`app/services/client/position_agent_tools.py:259-309`）调用 `interview_service.start_interview()` 时**从不检查是否已有同岗位的 in_progress 面试**：

- `Interview` 表（`app/models/interview.py`）无 `(user_id, status)` 或 `(user_id, resume_id, position)` 唯一约束
- `start_interview` 每次调用都会：INSERT `Interview` 行 + INSERT `InterviewMessage` 行 + 递增 `question_bank.use_count`

**重复触发路径**：
1. `/start-interview` 端点重复请求（重新匹配后再点、多标签页）
2. LLM 在 ReAct 循环（`recursion_limit=25`）内重复调用 `start_mock_interview`

> 注：前端按钮本身已防双击（`PositionMatch.vue:156` 点击即 `:disabled`），所以主要风险是**顺序重复**（重新匹配后再点）与**多标签页**（少见）。

## 设计决策（头脑风暴确认）

| # | 决策 | 理由 |
|---|------|------|
| 1 | **保留 Agent 逻辑不动**（不拆壳） | Agent 是卖点 + 拆壳回归风险 > 收益 |
| 2 | **幂等返回已有**：同岗位已有 in_progress 面试 → 直接返回它，不新建 | 用户可练不同岗位各开一个，但误点/重复不重复建 |
| 3 | 幂等检查放在 **`start_mock_interview` 工具内部** | 它是 LLM 路径与 `/start-interview` 端点路径的**唯一汇聚点**，一个修复覆盖两条路径 |

## 修复设计

**幂等键**：`(user_id, resume_id, target_position=模板.title)`。

Interview 表存的是 `target_position`（标题）而非 `position_tag`，但每个 `position_tag` 唯一对应一个模板标题，**功能等价，无需加列、无需迁移**。

### 文件 1：`app/repositories/interview_repo.py` — 新增查询方法

```python
async def get_active_by_position(
    self,
    db: AsyncSession,
    user_id: int,
    resume_id: int,
    target_position: str,
) -> Optional[Interview]:
    """查询 (user_id, resume_id, target_position) 的进行中面试。

    Args:
        db: 数据库会话。
        user_id: 用户 ID。
        resume_id: 简历 ID。
        target_position: 目标岗位标题。

    Returns:
        status=in_progress 且岗位匹配的 Interview，或 None。
    """
    stmt = select(Interview).where(
        Interview.user_id == user_id,
        Interview.resume_id == resume_id,
        Interview.target_position == target_position,
        Interview.status == "in_progress",
    ).order_by(Interview.id.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
```

### 文件 2：`app/services/client/position_agent_tools.py` — start_mock_interview 加幂等检查

在取到 `template`、`resume` 之后、调用 `interview_service.start_interview` 之前插入：

```python
# 幂等：同岗位已有进行中面试则直接返回，不重复创建
from app.repositories.interview_repo import interview_repo

active = await interview_repo.get_active_by_position(
    db, resume.user_id, resume_id, template.title
)
if active is not None:
    questions = active.questions_data or []
    logger.info(
        f"[PositionAgent] 岗位 {position_tag} 已有进行中面试 id={active.id}，幂等返回"
    )
    return {
        "interview_id": active.id,
        "position_tag": position_tag,
        "first_question": questions[0] if questions else None,
        "total_questions": active.total_questions,
        "existing": True,
    }
```

**不改动**：Agent 逻辑、system prompt、ReAct 循环、`POSITION_AGENT_TOOLS` 工具列表、`/start-interview` 端点、响应契约。

## 行为

- 重复触发（LLM 循环 / 重新匹配后再点 / `/start-interview` 重放）→ 返回已有面试的 `interview_id` + `existing: True`，前端照常跳转 `/interview/{id}`，**对用户无感知**
- 不新建 Interview / InterviewMessage，不递增 `use_count`
- 幂等检查与创建共用工具内同一 session（`get_session_local()()`），无额外连接

**已知边界**：并发双插入的竞态窗口（多标签页同秒触发）理论上仍可能各建一份。前端已禁双击，此为可接受边缘情况；若要彻底堵死需 partial unique index（`WHERE status='in_progress'`），本次不做以保持最小改动。

## 测试

新增 `tests/unit/test_position_agent_idempotency.py`：

1. `get_active_by_position` 命中：构造 in_progress + 匹配岗位的 Interview → 返回该实例
2. `get_active_by_position` 未命中：无匹配（岗位不同 / status 非 in_progress / 用户不同）→ 返回 None
3. `start_mock_interview` 幂等路径：mock session + template service 返回模板，已有 in_progress 面试 → 返回 `{existing: True, interview_id, ...}`，**断言不调用** `interview_service.start_interview`
4. `start_mock_interview` 正常路径：无已有面试 → 照常调用 `start_interview`（回归保护）

## 影响范围汇总

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `app/repositories/interview_repo.py` | +1 方法（~20 行） | `get_active_by_position` 查询 |
| `app/services/client/position_agent_tools.py` | +1 import + ~15 行 | start_mock_interview 幂等检查 |
| `tests/unit/test_position_agent_idempotency.py` | 新增 ~80 行 | 4 个用例 |

- **无新增依赖**
- **无数据库迁移**
- **无 API 变更**
- **无前端变更**
- **无 Agent 逻辑改动**

## 后续可选项（本次不做）

- partial unique index 堵死并发竞态
- 若未来做 Agent 拆壳，参考另一份设计（纯 async 顺序链 + 混合式输出 + `with_structured_output`）
