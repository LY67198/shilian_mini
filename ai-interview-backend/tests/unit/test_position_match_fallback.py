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
