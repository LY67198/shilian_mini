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
