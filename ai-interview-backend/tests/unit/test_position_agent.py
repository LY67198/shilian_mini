"""岗位匹配 Agent 系统 Prompt 完整性测试"""
import pytest


@pytest.mark.unit
class TestPositionAgentSystemPrompt:
    def test_system_prompt_lists_all_five_tools(self):
        from app.services.client.position_agent_service import _get_system_prompt
        template = _get_system_prompt()
        for tool in (
            "get_parsed_resume",
            "build_candidate_profile",
            "match_positions",
            "get_position_interview_focus",
            "start_mock_interview",
        ):
            assert tool in template

    def test_system_prompt_includes_interview_result_field(self):
        from app.services.client.position_agent_service import _get_system_prompt
        template = _get_system_prompt()
        assert "interview_result" in template


@pytest.mark.unit
class TestCandidateProfilePrompt:
    def test_prompt_yaml_exists_and_has_parsed_resume_var(self):
        from app.llm.prompts import load_prompt
        prompt = load_prompt("position_candidate_profile")
        assert prompt.messages[-1].prompt.template == "候选人简历：\n{parsed_resume}"

    async def test_build_candidate_profile_uses_prompt(self):
        import json
        from unittest.mock import AsyncMock, patch
        from app.services.client.position_agent_tools import ai_service, build_candidate_profile

        fake_chat = AsyncMock(return_value='{"experience_level": "junior", "primary_stack": ["Python"]}')
        with patch.object(ai_service, "_chat", fake_chat):
            out = await build_candidate_profile.ainvoke({"parsed_resume": {"skills": ["Python"]}})

        assert out["experience_level"] == "junior"
        assert out["primary_stack"] == ["Python"]
        # 确认传给 LLM 的 system prompt 来自 YAML
        messages = fake_chat.call_args[0][0]
        assert "资深招聘顾问 + HR 顾问" in messages[0]["content"]
