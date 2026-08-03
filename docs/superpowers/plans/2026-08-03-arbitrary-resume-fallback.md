# 任意简历可用 —— 岗位匹配 LLM 兜底 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让任意行业/任意背景的简历都能走通"岗位匹配 → 推荐 → 逐题面试 → 评分报告"闭环；模板/题库覆盖不到的岗位由 LLM 合成兜底。

**Architecture:** 保留现有 PositionAgent 5 工具链不动（固定 DAG 设计约束）。`match_positions` 增加最低分阈值：Top 分达标走模板匹配（现状），不达标时 `ai_service.synthesize_positions` 从候选人画像合成 1-3 个岗位并落库为 `category="custom"` 的岗位模板行，让后续 `get_position_interview_focus` / `start_mock_interview` 原样穿透。配套把画像/出题/评分 prompt 去掉技术向措辞，扩 backoffice category 枚举，前端加"AI 定制"标签与入口引导。

**Tech Stack:** FastAPI + LangChain @tool + SQLAlchemy + pgvector + Vue 3（后端在 Docker 容器内跑测试）。

---

## 前置条件

- 后端 dev 容器运行中（`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`），`shilian-app` 容器存在。
- 后端 `app/` 与 `tests/` 均为 bind mount，改完代码**无需重建镜像**，直接在容器内跑 pytest 即可。
- 测试命令统一走容器：`docker exec shilian-app pytest <file> -v`。
- 前端在 Windows 本地 build 验证。

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/prompts/position_synthesize.yaml` | 合成岗位 prompt | 新建 |
| `app/services/client/ai_service.py` | 新增 `synthesize_positions` 方法 | 修改 |
| `app/core/config.py` | 新增 `MIN_MATCH_SCORE` | 修改 |
| `app/services/client/position_agent_tools.py` | `match_positions` 阈值分支 + `_synthesize_and_persist` + `_build_template_recommendation` | 修改 |
| `app/prompts/position_candidate_profile.yaml` | 放宽 `position_hints` + 去技术措辞 | 修改 |
| `app/prompts/evaluator_agent.yaml` | 去技术措辞 | 修改 |
| `app/prompts/question_generate.yaml` | 去技术措辞 | 修改 |
| `app/prompts/question_generate_one.yaml` | 去技术措辞 | 修改 |
| `app/prompts/question_select.yaml` | 去技术措辞 | 修改 |
| `app/prompts/question_select_one.yaml` | 去技术措辞 | 修改 |
| `app/prompts/question_seed.yaml` | 去技术措辞 | 修改 |
| `app/schemas/backoffice/position_template.py` | category 枚举扩 "custom" | 修改 |
| `ai-interview-frontend/src/views/PositionMatch.vue` | custom 推荐加"AI 定制"标签 | 修改 |
| `ai-interview-frontend/src/views/ResumeUpload.vue` | 引导"不确定做什么？试试 AI 岗位匹配" | 修改 |
| `tests/unit/test_position_synthesize.py` | `synthesize_positions` 单测 | 新建 |
| `tests/unit/test_position_match_fallback.py` | `match_positions` 阈值/兜底单测 | 新建 |
| `tests/unit/test_prompt_generalization.py` | prompt 去技术化守卫 | 新建 |
| `tests/unit/test_position_template_schema.py` | category 枚举单测 | 新建 |
| `tests/unit/test_position_agent.py` | 更新 `position_candidate_profile` 措辞断言 | 修改 |

---

### Task 1: `position_synthesize` prompt + `ai_service.synthesize_positions`

**Files:**
- Create: `app/prompts/position_synthesize.yaml`
- Modify: `app/services/client/ai_service.py`（在 `generate_questions` 方法后追加）
- Test: `tests/unit/test_position_synthesize.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/unit/test_position_synthesize.py`：

```python
"""ai_service.synthesize_positions — 岗位匹配兜底合成单测"""
import pytest
from unittest.mock import AsyncMock, patch
from app.services.client.ai_service import ai_service


@pytest.mark.unit
class TestSynthesizePositions:
    def test_prompt_yaml_exists(self):
        from app.llm.prompts import load_prompt
        prompt = load_prompt("position_synthesize")
        assert "{candidate_profile_json}" in prompt.messages[-1].prompt.template

    async def test_returns_list_from_array_json(self):
        raw = '[{"title": "市场营销专员", "core_skills": ["市场调研"]}]'
        with patch.object(ai_service, "_chat", AsyncMock(return_value=raw)):
            out = await ai_service.synthesize_positions({"primary_stack": ["营销"]})
        assert out[0]["title"] == "市场营销专员"

    async def test_returns_list_from_dict_json(self):
        raw = '{"custom_positions": [{"title": "销售经理", "confidence": 0.8}]}'
        with patch.object(ai_service, "_chat", AsyncMock(return_value=raw)):
            out = await ai_service.synthesize_positions({"primary_stack": ["销售"]})
        assert out[0]["title"] == "销售经理"
        assert out[0]["confidence"] == 0.8

    async def test_filters_empty_title(self):
        raw = '[{"title": "  "}, {"title": "新媒体运营"}]'
        with patch.object(ai_service, "_chat", AsyncMock(return_value=raw)):
            out = await ai_service.synthesize_positions({})
        assert len(out) == 1
        assert out[0]["title"] == "新媒体运营"

    async def test_returns_empty_on_garbage(self):
        with patch.object(ai_service, "_chat", AsyncMock(return_value="not json at all")):
            out = await ai_service.synthesize_positions({})
        assert out == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_position_synthesize.py -v`
Expected: FAIL — `ImportError`/`AttributeError`（`synthesize_positions` 不存在）。

- [ ] **Step 3: 新建 prompt 文件** — 创建 `app/prompts/position_synthesize.yaml`：

```yaml
# PositionSynthesize — 无模板匹配时，从画像合成岗位
version: 1
temperature: 0.4
response_format: json
system: |
  你是一位资深招聘顾问。根据候选人的简历画像，为没有匹配到标准岗位模板的候选人
  推荐 1-3 个适合他的具体岗位。
  必须返回纯 JSON（不要 markdown 代码块）。结构为数组：
  [
    {
      "title": "具体岗位名称",
      "core_skills": ["岗位核心能力，3-6 个"],
      "focus_topics": ["面试重点考察方向，3-5 个"],
      "reasons": "为什么适合该候选人（结合画像的强项与项目方向）",
      "confidence": 0.0-1.0 的匹配置信度
    }
  ]
  要求：
  - title 要具体（如"市场营销专员"），不要泛泛的"综合管理岗"
  - core_skills / focus_topics 用岗位所在行业通用的表述
  - confidence 反映画像与岗位的契合程度
user_template: |-
  候选人画像：
  {candidate_profile_json}
```

- [ ] **Step 4: 实现 `synthesize_positions`** — 在 `ai_service.py` 的 `generate_questions` 方法（第 195 行 `return self._as_question_list(...)` 之后）追加：

```python
    async def synthesize_positions(self, candidate_profile: dict) -> list:
        """岗位匹配兜底：候选人与模板库不匹配时，LLM 从画像合成 1-3 个岗位。

    Args:
        candidate_profile: build_candidate_profile 输出的画像字典。

    Returns:
        合成岗位列表，每项含 title / core_skills / focus_topics / reasons / confidence。
        解析失败或无可合成项时返回空列表。
    """
        prompt = load_prompt("position_synthesize")
        messages = [
            {"role": "system", "content": prompt.messages[0].prompt.template},
            {
                "role": "user",
                "content": prompt.messages[-1].prompt.template.format(
                    candidate_profile_json=json.dumps(candidate_profile, ensure_ascii=False)
                ),
            },
        ]
        raw = await self._chat(messages, temperature=0.4)
        parsed = self._extract_json(raw)

        if isinstance(parsed, list):
            items = [p for p in parsed if isinstance(p, dict)]
        elif isinstance(parsed, dict):
            items = parsed.get("custom_positions") or parsed.get("positions") or []
            items = [p for p in items if isinstance(p, dict)]
        else:
            items = []
        return [p for p in items if (p.get("title") or "").strip()]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_position_synthesize.py -v`
Expected: PASS（5 passed）。

- [ ] **Step 6: 提交**

```bash
git add app/prompts/position_synthesize.yaml app/services/client/ai_service.py tests/unit/test_position_synthesize.py
git commit -m "feat(agent): 新增 position_synthesize prompt + ai_service.synthesize_positions 合成岗位兜底"
```

---

### Task 2: settings 新增 `MIN_MATCH_SCORE`

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: 加配置项** — 在 `config.py` 的 `QUESTION_BANK_TOP_K`（第 130 行）之后插入：

```python
    # 岗位匹配：低于该匹配分触发 LLM 合成岗位兜底
    MIN_MATCH_SCORE: float = 0.25
```

- [ ] **Step 2: 验证**

Run: `docker exec shilian-app python -c "from app.core.config import settings; assert settings.MIN_MATCH_SCORE == 0.25; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 提交**

```bash
git add app/core/config.py
git commit -m "feat(config): 新增 MIN_MATCH_SCORE 岗位匹配兜底阈值"
```

---

### Task 3: `match_positions` 阈值分支 + custom 模板落库

**Files:**
- Modify: `app/services/client/position_agent_tools.py`
- Test: `tests/unit/test_position_match_fallback.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/unit/test_position_match_fallback.py`：

```python
"""match_positions 阈值分支 + LLM 合成兜底单元测试"""
import hashlib
import pytest
from unittest.mock import AsyncMock


class MockTemplate:
    def __init__(self, position_tag, title, category="backend", core_skills=None,
                 nice_to_have_skills=None, project_keywords=None, level="junior"):
        self.position_tag = position_tag
        self.title = title
        self.category = category
        self.level = level
        self.core_skills = core_skills or []
        self.nice_to_have_skills = nice_to_have_skills or []
        self.project_keywords = project_keywords or []


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.unit
class TestMatchPositionsFallback:
    IT_TEMPLATES = [
        MockTemplate("python_backend", "Python 后端开发", core_skills=["Python", "FastAPI", "SQL"],
                     project_keywords=["后端", "API"]),
        MockTemplate("vue_frontend", "Vue 前端开发", core_skills=["Vue", "JavaScript", "CSS"],
                     project_keywords=["前端", "组件"]),
    ]

    def _patch(self, monkeypatch, templates=None, existing_by_tag=None):
        from app.services.client import position_agent_tools as tools

        monkeypatch.setattr(
            tools, "get_session_local",
            lambda: lambda: _SessionCtx(None),
        )
        monkeypatch.setattr(
            tools.position_template_service, "get_active_list",
            AsyncMock(return_value=templates if templates is not None else self.IT_TEMPLATES),
        )
        monkeypatch.setattr(
            tools.position_template_service, "get_by_tag",
            AsyncMock(side_effect=lambda db, tag: (existing_by_tag or {}).get(tag)),
        )
        create_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(tools.position_template_service, "create", create_mock)
        return create_mock

    async def test_high_score_uses_template_without_synthesize(self, monkeypatch):
        from app.services.client import position_agent_tools as tools
        create_mock = self._patch(monkeypatch)
        syn = AsyncMock(return_value=[{"title": "不应被调用"}])
        monkeypatch.setattr(tools.ai_service, "synthesize_positions", syn)

        profile = {"primary_stack": ["Python", "FastAPI"], "secondary_stack": [],
                   "project_directions": []}
        out = await tools.match_positions.ainvoke({"candidate_profile": profile})

        assert out["match_source"] == "template"
        assert out["recommended_positions"][0]["title"] == "Python 后端开发"
        syn.assert_not_called()
        create_mock.assert_not_called()

    async def test_low_score_synthesizes_custom_position(self, monkeypatch):
        from app.services.client import position_agent_tools as tools
        create_mock = self._patch(monkeypatch, existing_by_tag={})
        monkeypatch.setattr(
            tools.ai_service, "synthesize_positions",
            AsyncMock(return_value=[{
                "title": "市场营销专员",
                "core_skills": ["市场调研", "文案策划"],
                "focus_topics": ["渠道策略"],
                "reasons": "候选人具备市场推广经验",
                "confidence": 0.72,
            }]),
        )

        profile = {"primary_stack": ["市场调研", "活动策划"], "secondary_stack": [],
                   "project_directions": ["品牌推广"]}
        out = await tools.match_positions.ainvoke({"candidate_profile": profile})

        assert out["match_source"] == "custom"
        pos = out["recommended_positions"][0]
        assert pos["title"] == "市场营销专员"
        assert pos["category"] == "custom"
        assert pos["position_tag"].startswith("custom_")
        assert pos["match_score"] == 0.72
        # 落库调用：get_by_tag + create
        create_mock.assert_awaited_once()
        create_kwargs = create_mock.await_args.args[1]
        assert create_kwargs["category"] == "custom"
        assert create_kwargs["title"] == "市场营销专员"

    async def test_synthesize_skips_create_when_row_exists(self, monkeypatch):
        from app.services.client import position_agent_tools as tools
        title = "市场营销专员"
        tag = "custom_" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
        create_mock = self._patch(
            monkeypatch,
            existing_by_tag={tag: MockTemplate(tag, title, category="custom")},
        )
        monkeypatch.setattr(
            tools.ai_service, "synthesize_positions",
            AsyncMock(return_value=[{"title": title, "confidence": 0.7}]),
        )

        profile = {"primary_stack": ["市场"], "secondary_stack": [], "project_directions": []}
        out = await tools.match_positions.ainvoke({"candidate_profile": profile})

        assert out["recommended_positions"][0]["position_tag"] == tag
        create_mock.assert_not_called()

    async def test_synthesize_failure_falls_back_to_top_template(self, monkeypatch):
        from app.services.client import position_agent_tools as tools
        self._patch(monkeypatch, existing_by_tag={})
        monkeypatch.setattr(tools.ai_service, "synthesize_positions", AsyncMock(return_value=[]))

        profile = {"primary_stack": ["市场"], "secondary_stack": [], "project_directions": []}
        out = await tools.match_positions.ainvoke({"candidate_profile": profile})

        assert out["match_source"] == "fallback_low"
        assert out["recommended_positions"][0]["title"] == "Python 后端开发"

    async def test_custom_template_excluded_from_scoring(self, monkeypatch):
        from app.services.client import position_agent_tools as tools
        # custom 模板技能高度匹配画像（若不排除会拿到高分），另一条真实模板低分
        custom = MockTemplate("custom_abcdef12", "某定制岗位", category="custom",
                              core_skills=["Python", "FastAPI"], project_keywords=["后端"])
        low = MockTemplate("other_role", "低匹配岗位", core_skills=["Java", "Spring"],
                           project_keywords=["后端"])
        self._patch(monkeypatch, templates=[custom, low])
        syn = AsyncMock(return_value=[])
        monkeypatch.setattr(tools.ai_service, "synthesize_positions", syn)

        profile = {"primary_stack": ["Python", "FastAPI"], "secondary_stack": [],
                   "project_directions": []}
        out = await tools.match_positions.ainvoke({"candidate_profile": profile})

        # custom 模板被排除（即使技能高度匹配），best_score 只来自 low（0 分）→ 低于阈值走合成
        assert out["match_source"] == "fallback_low"
        assert "某定制岗位" not in [p["title"] for p in out["recommended_positions"]]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_position_match_fallback.py -v`
Expected: FAIL — 断言 `match_source` 失败（当前 `match_positions` 不返回该字段）。

- [ ] **Step 3: 实现** — 修改 `position_agent_tools.py`：

3a. 顶部 import 区（第 28 行 `logger = logging.getLogger(__name__)` 之后）追加两行：

```python
import hashlib
from app.core.config import settings
```

3b. 把 `match_positions`（第 144-202 行）整体替换为：

```python
@tool
async def match_positions(candidate_profile: dict, top_n: int = 3) -> dict:
    """
    基于候选人画像，从岗位模板库匹配最适合的 1-3 个岗位。
    必须在调用 build_candidate_profile 之后才能使用本工具。

    Args:
        candidate_profile: 来自 build_candidate_profile 的输出
        top_n: 返回 Top N 个推荐岗位（默认 3）

    Returns:
        recommended_positions 字段为列表，每条含 position_tag, title, match_score,
        reasons, missing_skills, matched_skills
    """
    if not candidate_profile or candidate_profile.get("error"):
        return {"error": "candidate_profile 无效"}

    async with get_session_local()() as db:
        templates = await position_template_service.get_active_list(db)

    # 排除系统兜底生成的 custom 模板（不参与岗位匹配）
    templates = [t for t in templates if getattr(t, "category", None) != "custom"]

    if not templates:
        return {"recommended_positions": [], "warning": "岗位模板库为空"}

    scored = []
    for t in templates:
        score, matched, missing = _calculate_match_score(candidate_profile, t)
        scored.append((t, score, matched, missing))

    scored.sort(key=lambda x: x[1], reverse=True)
    best_score = scored[0][1] if scored else 0.0

    # 最高分达到阈值 → 走模板匹配（现状）
    if best_score >= settings.MIN_MATCH_SCORE:
        top = scored[:top_n]
        recommended = [
            _build_template_recommendation(candidate_profile, t, score, matched, missing)
            for t, score, matched, missing in top
        ]
        return {"recommended_positions": recommended, "match_source": "template"}

    # 最高分低于阈值 → LLM 合成岗位兜底
    synthesized = await _synthesize_and_persist(candidate_profile)
    if synthesized:
        return {"recommended_positions": synthesized, "match_source": "custom"}

    # 合成失败兜底：返回最高分模板并标注低匹配度
    logger.warning("[match_positions] LLM 合成岗位失败，回退最高分模板")
    t, score, matched, missing = scored[0]
    recommended = [
        _build_template_recommendation(candidate_profile, t, score, matched, missing)
    ]
    return {"recommended_positions": recommended, "match_source": "fallback_low"}


def _build_template_recommendation(
    candidate_profile: dict,
    template: PositionTemplate,
    score: float,
    matched: list,
    missing: list,
) -> dict:
    """把单个模板匹配结果构造成推荐条目（模板/兜底共用，DRY）。"""
    reasons = []
    if matched:
        reasons.append(f"具备 {', '.join(matched[:3])} 等核心技能")
    if template.position_tag in (candidate_profile.get("position_hints") or []):
        reasons.append("候选人画像与该岗位方向一致")
    directions = candidate_profile.get("project_directions") or []
    keywords = template.project_keywords or []
    if directions and keywords:
        overlap = [d for d in directions if any(str(kw).lower() in str(d).lower() for kw in keywords)]
        if overlap:
            reasons.append(f"项目方向匹配（{overlap[0]}）")
    if not reasons:
        reasons.append("整体技能与岗位要求有一定重合")

    return {
        "position_tag": template.position_tag,
        "title": template.title,
        "category": getattr(template, "category", None),
        "level": getattr(template, "level", None),
        "match_score": score,
        "matched_skills": matched,
        "missing_skills": missing[:5],
        "reasons": reasons,
    }


async def _synthesize_and_persist(candidate_profile: dict) -> list:
    """LLM 合成岗位并落库为 category=custom 模板，返回 recommended 形状列表。

    同一标题重复合成 → position_tag 稳定（title 的 sha1 前 8 位），幂等复用。
    """
    try:
        positions = await ai_service.synthesize_positions(candidate_profile)
    except Exception as e:
        logger.error(f"[match_positions] 岗位合成失败: {e}")
        return []
    if not positions:
        return []

    result = []
    async with get_session_local()() as db:
        for p in positions:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            tag = "custom_" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            existing = await position_template_service.get_by_tag(db, tag)
            if existing is None:
                try:
                    await position_template_service.create(db, {
                        "position_tag": tag,
                        "title": title,
                        "category": "custom",
                        "level": "junior",
                        "core_skills": p.get("core_skills") or [],
                        "nice_to_have_skills": [],
                        "project_keywords": [],
                        "focus_topics": p.get("focus_topics") or [],
                        "recommended_query_keywords": [],
                        "recommended_difficulty": "medium",
                        "recommended_question_count": 7,
                        "jd_summary": p.get("reasons") or "",
                        "typical_companies": [],
                        "sort_order": 0,
                        "is_active": True,
                    })
                except ValueError:
                    logger.warning(f"[match_positions] custom 模板 {tag} 已存在，跳过创建")
            result.append({
                "position_tag": tag,
                "title": title,
                "category": "custom",
                "level": "junior",
                "match_score": round(float(p.get("confidence") or 0.5), 4),
                "matched_skills": p.get("core_skills") or [],
                "missing_skills": [],
                "reasons": [p.get("reasons") or ""],
            })
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_position_match_fallback.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: 回归既有岗位测试**

Run: `docker exec shilian-app pytest tests/unit/test_position_agent.py tests/unit/test_position_agent_idempotency.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/client/position_agent_tools.py tests/unit/test_position_match_fallback.py
git commit -m "feat(agent): match_positions 加最低分阈值，低匹配触发 LLM 合成岗位落库 custom 模板"
```

---

### Task 4: 画像 prompt 放宽 `position_hints` + 去技术措辞

**Files:**
- Modify: `app/prompts/position_candidate_profile.yaml`
- Modify: `tests/unit/test_position_agent.py:45`

- [ ] **Step 1: 改写 prompt** — 用 Write 整体重写 `app/prompts/position_candidate_profile.yaml`：

```yaml
# PositionAgent — 候选人画像提炼提示词
version: 1
temperature: 0.3
response_format: json
system: |
  你是一个资深招聘顾问 + HR 顾问。
  请根据候选人结构化简历，提炼成画像信息。
  必须返回纯 JSON 格式（不要 markdown 代码块），包含字段：
  {
    "experience_level": "campus / junior / mid / senior",
    "primary_stack": ["核心技能/技术栈，最多 8 个（非技术岗位填业务技能）"],
    "secondary_stack": ["次要技能/技术栈"],
    "project_directions": ["项目方向标签，如 电商后端 / AI应用 / 品牌推广 / 活动运营"],
    "strong_points": ["3 条具体优势"],
    "weak_points": ["3 条具体不足"],
    "position_hints": ["建议匹配的岗位方向，自由填写，如 市场营销 / 产品运营"]
  }
  评估标准：
  - 在校学生 / 应届生只有实习经历 → campus
  - 1-3 年经验 → junior
  - 3-5 年经验 → mid
  - 5 年以上 → senior
  position_hints 不限定候选值：根据候选人画像自由给出最贴合的岗位方向；
  若某方向恰好对应岗位模板 tag（如 python_backend），可照实填写以作强匹配加分。
user_template: |-
  候选人简历：
  {parsed_resume}
```

- [ ] **Step 2: 更新现有测试断言** — 改 `tests/unit/test_position_agent.py:45`：

```python
        assert "资深技术面试官 + HR 顾问" in messages[0]["content"]
```
改为：
```python
        assert "资深招聘顾问 + HR 顾问" in messages[0]["content"]
```

- [ ] **Step 3: 运行测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_position_agent.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 4: 提交**

```bash
git add app/prompts/position_candidate_profile.yaml tests/unit/test_position_agent.py
git commit -m "feat(prompts): 画像 prompt 放宽 position_hints 为自由填写，去技术措辞"
```

---

### Task 5: 出题/评分 prompt 去技术化 + 守卫测试

**Files:**
- Modify: `app/prompts/evaluator_agent.yaml`
- Modify: `app/prompts/question_generate.yaml`
- Modify: `app/prompts/question_generate_one.yaml`
- Modify: `app/prompts/question_select.yaml`
- Modify: `app/prompts/question_select_one.yaml`
- Modify: `app/prompts/question_seed.yaml`
- Test: `tests/unit/test_prompt_generalization.py`

- [ ] **Step 1: 写守卫测试** — 创建 `tests/unit/test_prompt_generalization.py`：

```python
"""prompt 去技术化守卫测试：核心 prompt 不得含技术向专属措辞"""
import pytest
from app.llm.prompts import load_prompt


@pytest.mark.unit
class TestPromptGeneralization:
    TECH_PHRASES = ["技术要点", "工程经验", "资深技术面试官", "技术面试官"]

    @pytest.mark.parametrize("name", [
        "evaluator_agent",
        "question_generate",
        "question_generate_one",
        "question_select",
        "question_select_one",
        "question_seed",
    ])
    def test_no_tech_only_phrases(self, name):
        prompt = load_prompt(name)
        content = prompt.messages[0].prompt.template
        for phrase in self.TECH_PHRASES:
            assert phrase not in content, f"{name} 仍含技术向措辞: {phrase}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_prompt_generalization.py -v`
Expected: FAIL — 多处命中 `TECH_PHRASES`。

- [ ] **Step 3: 逐文件修改措辞** — 用 Edit 精确替换：

`app/prompts/evaluator_agent.yaml`：
- `  - 准确性：回答是否抓住了技术要点（40%）` → `  - 准确性：回答是否抓住了题目要点（40%）`
- `  - 实践：是否能结合工程经验（10%）` → `  - 实践：是否能结合实际经验（10%）`

`app/prompts/question_generate.yaml`：
- `  你是一个资深技术面试官，正在面试{target_position}岗位。` → `  你是一位资深面试官，正在面试{target_position}岗位。`
- `  1. 结合候选人的项目经验和技术栈提问` → `  1. 结合候选人的项目经历和背景提问`
- `  3. 覆盖技术深度、项目经验、基础知识` → `  3. 覆盖专业深度、项目经历、基础知识`

`app/prompts/question_generate_one.yaml`：
- `  你是资深技术面试官，正在为{target_position}岗位进行逐题面试。` → `  你是一位资深面试官，正在为{target_position}岗位进行逐题面试。`
- `  2. 结合候选人的项目经验和技术栈提问` → `  2. 结合候选人的项目经历和背景提问`

`app/prompts/question_select.yaml`：
- `  你是资深技术面试官，正在为【{target_position}】岗位选题。` → `  你是一位资深面试官，正在为【{target_position}】岗位选题。`

`app/prompts/question_select_one.yaml`：
- `  你是资深技术面试官，正在为【{target_position}】岗位进行逐题面试。` → `  你是一位资深面试官，正在为【{target_position}】岗位进行逐题面试。`

`app/prompts/question_seed.yaml`：
- `  你是资深技术面试官，正在为【{target_position}】{intern_hint}岗位准备面试题。` → `  你是一位资深面试官，正在为【{target_position}】{intern_hint}岗位准备面试题。`

> category 枚举（`self-intro/project/technical/coding/system-design`）**保持不变**（spec 决策）。

- [ ] **Step 4: 运行守卫测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_prompt_generalization.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: 回归评分/报告相关单测**

Run: `docker exec shilian-app pytest tests/unit/test_interview_nodes.py tests/unit/test_ai_service_unit.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/prompts/evaluator_agent.yaml app/prompts/question_generate.yaml app/prompts/question_generate_one.yaml app/prompts/question_select.yaml app/prompts/question_select_one.yaml app/prompts/question_seed.yaml tests/unit/test_prompt_generalization.py
git commit -m "feat(prompts): 出题/评分 prompt 去技术向措辞，支持非技术岗位面试"
```

---

### Task 6: backoffice schema 扩 category 枚举

**Files:**
- Modify: `app/schemas/backoffice/position_template.py`
- Test: `tests/unit/test_position_template_schema.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/unit/test_position_template_schema.py`：

```python
"""backoffice 岗位模板 schema category 枚举测试"""
import pytest
from pydantic import ValidationError


@pytest.mark.unit
class TestPositionTemplateCategory:
    def test_accepts_custom_category(self):
        from app.schemas.backoffice.position_template import PositionTemplateCreate
        t = PositionTemplateCreate(position_tag="custom_x", title="自定义岗位", category="custom")
        assert t.category == "custom"

    def test_rejects_unknown_category(self):
        from app.schemas.backoffice.position_template import PositionTemplateCreate
        with pytest.raises(ValidationError):
            PositionTemplateCreate(position_tag="x", title="X", category="other")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `docker exec shilian-app pytest tests/unit/test_position_template_schema.py -v`
Expected: FAIL — `category` 的 Literal 不含 `"custom"`，Pydantic 报 ValidationError。

- [ ] **Step 3: 扩枚举** — 改 `app/schemas/backoffice/position_template.py` 两处 Literal：

第 11 行：
```python
    category: Literal["backend", "frontend", "ai", "mobile", "devops"] = Field(...)
```
改为：
```python
    category: Literal["backend", "frontend", "ai", "mobile", "devops", "custom"] = Field(...)
```

第 27 行：
```python
    category: Optional[Literal["backend", "frontend", "ai", "mobile", "devops"]] = None
```
改为：
```python
    category: Optional[Literal["backend", "frontend", "ai", "mobile", "devops", "custom"]] = None
```

> `"custom"` 仅供系统内部兜底使用；管理端前端下拉不暴露该值（见 Task 7/8，不改 admin 下拉，天然不含 custom）。

- [ ] **Step 4: 运行测试确认通过**

Run: `docker exec shilian-app pytest tests/unit/test_position_template_schema.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: 提交**

```bash
git add app/schemas/backoffice/position_template.py tests/unit/test_position_template_schema.py
git commit -m "feat(schemas): 岗位模板 category 枚举扩 custom（系统内部兜底用）"
```

---

### Task 7: 前端 PositionMatch 加"AI 定制"标签

**Files:**
- Modify: `ai-interview-frontend/src/views/PositionMatch.vue`

- [ ] **Step 1: 加标签** — 在 `PositionMatch.vue` 推荐岗位卡片头（第 130-134 行 `position-header` div）内，`position-title` span 之后加一个条件 badge：

```html
            <div class="position-header">
              <span class="rank">#{{ idx + 1 }}</span>
              <span class="position-title">{{ pos.title }}</span>
              <span v-if="pos.category === 'custom'" class="custom-badge">AI 定制</span>
              <span class="match-score">匹配度 {{ Math.round(pos.match_score * 100) }}%</span>
            </div>
```

- [ ] **Step 2: 加样式** — 在 `<style scoped>` 内追加：

```css
.custom-badge {
  margin-left: 8px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  color: #7c3aed;
  background: #f3e8ff;
}
```

- [ ] **Step 3: 构建验证**

Run: `cd D:/shilian_ai/ai-interview-frontend && npm run build`
Expected: `build` 成功无报错。

- [ ] **Step 4: 提交**

```bash
git add ai-interview-frontend/src/views/PositionMatch.vue
git commit -m "feat(frontend): PositionMatch 推荐卡片对 custom 岗位显示 AI 定制标签"
```

---

### Task 8: 前端 ResumeUpload 加"不确定做什么"引导

**Files:**
- Modify: `ai-interview-frontend/src/views/ResumeUpload.vue`

- [ ] **Step 1: 加引导文案** — 在 `ResumeUpload.vue` 目标岗位输入框（第 17-18 行 `form-group` div 内 input 之后）追加一行：

```html
        <div class="form-group">
          <label>目标岗位</label>
          <input v-model="targetPosition" placeholder="例如：Python后端开发工程师" />
          <p class="hint">不确定自己能做什么？<router-link to="/position-match">试试 AI 岗位匹配 →</router-link></p>
        </div>
```

- [ ] **Step 2: 加样式** — 在 `<style scoped>` 内追加：

```css
.hint { font-size: 12px; color: #6b7280; margin-top: 6px; }
.hint a { color: #2563eb; }
```

- [ ] **Step 3: 构建验证**

Run: `cd D:/shilian_ai/ai-interview-frontend && npm run build`
Expected: `build` 成功无报错（路由 `/position-match` 已存在于 `src/router/index.js:12`）。

- [ ] **Step 4: 提交**

```bash
git add ai-interview-frontend/src/views/ResumeUpload.vue
git commit -m "feat(frontend): 上传页加'不确定能做什么？试试 AI 岗位匹配'引导入口"
```

---

### Task 9: 全量回归验证

**Files:** 无改动。

- [ ] **Step 1: 后端全量单元测试**

Run: `docker exec shilian-app pytest -m "unit"`
Expected: PASS（现有 115 + 新增 ~19：synthesize 5 + match_fallback 6 + prompt 守卫 6 + schema 2，全部通过）。

- [ ] **Step 2: 前端构建**

Run: `cd D:/shilian_ai/ai-interview-frontend && npm run build`
Expected: 成功。
Run: `cd D:/shilian_ai/ai-interview-admin && npm run build`
Expected: 成功。

- [ ] **Step 3: 人工 E2E（可选，需真实 DeepSeek token + 一份非技术简历）**

上传一份市场营销类简历 → PositionMatch 页 → 应看到"AI 定制"标签的推荐岗位（`match_source=custom`）→ 点开始模拟面试 → 逐题能出非技术面试题。

- [ ] **Step 4: 确认无遗留未提交改动**

Run: `git status --short`
Expected: 除 `.claude/`（untracked）与既有未提交文件外，本次计划的改动均已提交。

---

## Self-Review 记录

- **Spec 覆盖**：阈值（Task 3）、合成 prompt（Task 1）、custom 落库与幂等（Task 3）、custom 排除匹配（Task 3）、画像 position_hints 放宽（Task 4）、出题/评分 prompt 去技术化（Task 5）、schema 扩枚举（Task 6）、前端标签+入口引导（Task 7/8）、错误处理兜底 fallback_low（Task 3）、测试（Task 1/3/5/6）。✅ 全覆盖。
- **占位符扫描**：无 TBD/TODO，所有代码步骤含完整实现。
- **类型一致性**：`_build_template_recommendation` / `_synthesize_and_persist` / `synthesize_positions` 在 Task 1 定义、Task 3 使用，签名一致；`match_source` 三态（template/custom/fallback_low）在测试与实现中一致。
