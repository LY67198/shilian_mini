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
