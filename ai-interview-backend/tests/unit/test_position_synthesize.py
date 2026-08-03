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
