# 任意简历可用 —— 岗位匹配 LLM 兜底设计

> 日期：2026-08-03
> 状态：已批准（brainstorming）
> 目标：让**任意行业/任意背景的简历**都能完整走通"岗位匹配 → 推荐 → 逐题面试 → 评分报告"闭环。模板/题库覆盖不到的岗位，由大模型兜底合成。

## 背景与问题

当前岗位匹配 Agent（`position_agent_tools.py`）只面向 8 个 IT 岗位模板（python/java/vue/react/ai/fullstack/android/devops）：

- `match_positions` **没有最低分阈值**——非技术简历（营销/人事/财务等）匹配 8 个 IT 模板分数普遍很低，但仍会硬挑 Top 3 返回，并配误导性兜底理由"整体技术栈与岗位要求有一定重合"。
- `position_candidate_profile.yaml` 的 `position_hints` 候选值硬编码 8 个 IT tag，非技术简历会被 LLM 硬塞进这 8 个里。
- 面试出题/知识检索已具备优雅降级（题库空 → 纯 AI 生成；知识库空 → `knowledge_context=[]`），这部分无需改造。
- 评分/报告/出题 prompt 存在**技术向措辞**（"技术要点 40%"、"工程经验 10%"、"资深技术面试官"），对非技术岗位不公允/违和。

## 设计决策

### 方案：模板优先 + 最低分阈值 + LLM 合成岗位兜底

保留模板匹配为主力；匹配分低于阈值时，不再硬塞 IT 岗位，改为 LLM 从候选人画像合成岗位并启动面试。

### 保留 Agent 不拆壳

`position_agent_service.py` 的 LangChain `create_agent` 5 工具链（固定 DAG、系统 prompt 锁死工具顺序）是既有设计约束。合成岗位通过**落库为 `category="custom"` 的岗位模板行**穿透工具链，`get_position_interview_focus` / `start_mock_interview` 无需改工具契约。

## 改动清单

### 1. 匹配阈值 + LLM 合成（`app/services/client/position_agent_tools.py`）

- `match_positions` 增加最低分阈值判断：
  - 新增 `MIN_MATCH_SCORE`（默认 `0.25`，放 `app/core/config.py` settings）。
  - 计算全部模板分数后，取 `best_score`。`best_score >= MIN_MATCH_SCORE` → 现状返回 Top N 模板推荐。
  - `best_score < MIN_MATCH_SCORE` → 调用 LLM 合成（见下），返回合成岗位列表（同一 `recommended_positions` 结构，`match_score` 用 `confidence`）。
- 新增 `ai_service.synthesize_positions(candidate_profile)` + prompt `app/prompts/position_synthesize.yaml`：
  - 输入候选人画像；输出 1-3 个岗位，每项 `title / core_skills / focus_topics / reasons / confidence(0-1)`。
- 合成岗位落库为 custom 模板（upsert 幂等）：
  - `position_tag = "custom_<slug>"`（由 title 生成，如 `custom_marketing`），`category = "custom"`，写入 `core_skills` / `focus_topics` / `recommended_difficulty` / `recommended_question_count`（默认 medium / 7），`is_active = True`。
  - 重复合成同标题岗位 → 复用已有行（按 `position_tag` upsert），不产生脏行。
- `match_positions` 打分时**排除 `category="custom"`**（合成岗位不参与未来匹配，避免污染模板库）。
- 错误处理：LLM 合成失败或落库失败 → 回退返回原 Top 1 模板推荐并标注低匹配度，前端提示"无强匹配岗位，可手动填写岗位"，流程不崩。

### 2. 画像 prompt 放宽（`app/prompts/position_candidate_profile.yaml`）

- `position_hints` 放宽为**自由填写岗位方向**（不再限制 8 个 IT tag）。
- 角色描述 "资深技术面试官 + HR 顾问" → "资深招聘顾问 + 面试官"。
- `primary_stack / secondary_stack` 描述改为"核心技能 / 次要技能（非技术岗位填业务技能）"，**字段名保持不变**（`_calculate_match_score` 读取这些字段，避免联动改匹配代码）。

### 3. Prompt 通用化（去技术向措辞）

| 文件 | 改动 |
|---|---|
| `evaluator_agent.yaml` | "技术要点 40%" → "题目要点 40%"；"工程经验 10%" → "实际经验 10%" |
| `question_generate.yaml` / `question_generate_one.yaml` | "资深技术面试官" → "资深面试官"；"结合技术栈提问" → "结合经历/背景提问"；"覆盖技术深度" → "覆盖专业深度与项目经历"；`category` 枚举**保持不动**（LLM 尽量挑贴近类别，避免 rippling 到报告/统计逻辑） |
| `question_select.yaml` / `question_select_one.yaml` / `question_seed.yaml` | "资深技术面试官" → "资深面试官" |
| `report_agent.yaml` | 已通用，不改 |

### 4. Schema 扩枚举（`app/schemas/backoffice/position_template.py`）

- `category` 枚举扩为 `["backend", "frontend", "ai", "mobile", "devops", "custom"]`。
- **"custom" 仅供系统内部兜底使用**：管理端（`ai-interview-admin`）创建/编辑表单的下拉选项过滤掉该值，后端 API 层面 custom 行仅供 Agent 工具读写。

### 5. 前端

- `ai-interview-frontend/src/views/PositionMatch.vue`：推荐卡片对 `category === "custom"` 显示"AI 定制"小标签；`match_score` 位置显示 confidence。推荐卡片本就透传 `position_tag`，custom 岗位无需其他改动。
- `ai-interview-frontend/src/views/ResumeUpload.vue`：岗位输入框旁加引导文案"不确定自己能做什么？试试 AI 岗位匹配 →"，点击跳转 `/position-match`（解决"不知道自己能做什么"的入口）。

### 6. 数据

- seed 模板不动（非技术岗位走 LLM 兜底，不铺数据）。
- 无需数据库迁移（`position_templates.category` 已是 `String(50)`，新枚举值不落 schema 约束）。

## 数据流（非技术简历示例：市场营销）

```
上传简历 → 解析 → build_candidate_profile（LLM 提炼画像，position_hints 自由）
→ match_positions：8 个 IT 模板分数 < 0.25
→ LLM synthesize_positions：合成"市场营销专员"等 1-3 个岗位
→ upsert custom 模板行（category="custom"）
→ Agent 走 get_position_interview_focus(custom_marketing) → start_mock_interview
→ start_mock_interview 用 custom 模板 title 建 Interview（快建，generate_questions=False）
→ 面试页 next-question 端点逐题生成（题库空 → 纯 AI 生成，已有兜底）
→ evaluate / generate_report 用通用化 prompt 评分出报告
```

## 测试

- **单元测试**：
  - 阈值分支：构造 `best_score >= 阈值` 走模板 / `< 阈值` 走合成。
  - custom 模板 upsert 幂等：同标题重复合成不产生重复行。
  - 合成 JSON 解析失败 → 回退 Top 1 低匹配模板，不抛异常。
  - `match_positions` 打分排除 `category="custom"`。
- **非技术简历链路**（mock LLM 为主）：市场营销简历 → match 返回 custom 推荐 → start_interview → next-question 出题。
- **回归**：现有 IT 面试测试全绿，`pytest -m "unit"`。

## 范围外（YAGNI）

- 不扩非技术岗位模板 seed（LLM 兜底优先）。
- 不改 `question_generate_one.yaml` 的 category 枚举。
- 不做管理端 custom 模板的批量清理功能（后续如有脏行再补）。

## 参考

- 方案选择：模板优先 + 阈值 + LLM 合成兜底（方案 A）。
- 涉及文件：`position_agent_tools.py`、`ai_service.py`、`app/prompts/*.yaml`（6 个）、`schemas/backoffice/position_template.py`、`PositionMatch.vue`、`ResumeUpload.vue`、`app/core/config.py`。
