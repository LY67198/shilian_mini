"""面试 workflow 节点单元测试"""
import pytest

from app.workflows.interview.nodes.check_finished import check_finished_node


@pytest.mark.unit
class TestCheckFinished:
    """check_finished_node — 纯函数节点，无外部依赖"""

    async def test_not_finished(self):
        state = {"current_index": 2, "questions": [{}] * 5}
        result = await check_finished_node(state)
        assert result == {"is_finished": False}

    async def test_finished_last_question(self):
        state = {"current_index": 4, "questions": [{}] * 5}
        result = await check_finished_node(state)
        assert result == {"is_finished": True}

    async def test_finished_beyond_last(self):
        state = {"current_index": 5, "questions": [{}] * 5}
        result = await check_finished_node(state)
        assert result == {"is_finished": True}

    async def test_empty_state_defaults(self):
        state = {}
        result = await check_finished_node(state)
        assert result == {"is_finished": True}  # 0 + 1 >= 0 → True

    async def test_progressive_not_finished_until_total_reached(self):
        """渐进模式：questions 少于 total 时不结束，继续生成"""
        state = {"current_index": 4, "questions": [{}] * 5, "total_questions": 8}
        result = await check_finished_node(state)
        assert result == {"is_finished": False}

    async def test_progressive_finished_at_total(self):
        state = {"current_index": 7, "questions": [{}] * 5, "total_questions": 8}
        result = await check_finished_node(state)
        assert result == {"is_finished": True}

    async def test_pre_generated_equivalent_when_len_equals_total(self):
        """预生成正常路径 total==len 时行为与旧逻辑一致"""
        state = {"current_index": 4, "questions": [{}] * 5, "total_questions": 5}
        result = await check_finished_node(state)
        assert result == {"is_finished": True}


@pytest.mark.unit
class TestScoreResult:
    """ScoreResult Pydantic 模型验证"""

    def test_valid_score(self):
        from app.workflows.interview.state import ScoreResult
        r = ScoreResult(score=8.5, feedback="不错", follow_up=False)
        assert r.score == 8.5
        assert r.feedback == "不错"
        assert r.follow_up is False

    def test_default_values(self):
        from app.workflows.interview.state import ScoreResult
        r = ScoreResult()
        assert r.score == 5.0
        assert r.feedback == ""
        assert r.follow_up is False

    def test_score_out_of_range_clamped(self):
        from app.workflows.interview.state import ScoreResult
        with pytest.raises(ValueError):
            ScoreResult(score=11.0)
        with pytest.raises(ValueError):
            ScoreResult(score=-1.0)


@pytest.mark.unit
class TestInterviewState:
    """InterviewState TypedDict 字段设置"""

    def test_minimal_state(self):
        from app.workflows.interview.state import InterviewState
        state: InterviewState = {
            "interview_id": 1,
            "user_id": 42,
        }
        assert state["interview_id"] == 1
        assert state["user_id"] == 42
        # 可选字段
        assert state.get("resume_context") is None
        assert state.get("is_finished") is None


@pytest.mark.unit
class TestEvaluateNode:
    """evaluate_node — 使用 prompt | llm.bind(json_object) + astream + extract_json 评分，需 mock LLM"""

    async def test_returns_score_and_feedback(self):
        """evaluate_node returns score + feedback from streamed json output"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "请介绍 Python 的 GIL",
            "answer": "GIL 是全局解释器锁...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "user_id": 42,
        }

        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 7.5, "feedback": "回答良好", "follow_up": false}')

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        # Mock DB: return an unscored candidate message
        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 7.5
        assert result["feedback"] == "回答良好"
        # Verify score was written to the message
        assert mock_msg.score == 7.5
        assert mock_msg.feedback == "回答良好"
        mock_db.commit.assert_called_once()

    async def test_persists_score_to_db_message(self):
        """evaluate_node updates the latest unscored candidate message with score+feedback+question_index"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "What is dependency injection?",
            "answer": "DI is a pattern where...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 5,
            "current_index": 2,
        }

        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 9.0, "feedback": "Excellent", "follow_up": false}')

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert mock_msg.score == 9.0
        assert mock_msg.feedback == "Excellent"
        assert mock_msg.question_index == 2
        mock_db.commit.assert_called_once()

    async def test_fallback_on_llm_failure(self):
        """LLM failure returns fallback score=5.0, does not attempt DB write"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "Q",
            "answer": "A",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "user_id": 42,
        }

        mock_db = AsyncMock()

        with patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 5.0
        assert "评分异常" in result["feedback"]
        # DB must NOT be called when LLM fails (early return before DB write)
        mock_db.execute.assert_not_called()

    async def test_commit_failure_marks_persist_failed(self):
        """提交失败时打 persist_failed 标记，面试不中断"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "Q",
            "answer": "A",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "current_index": 0,
        }

        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 6.0, "feedback": "ok", "follow_up": false}')

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        mock_db.commit.side_effect = RuntimeError("db down")

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 6.0
        assert result["persist_failed"] is True

    async def test_history_excludes_current_answer(self):
        """history_text 排除与 {answer} 重复的当前答案消息"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "Q2",
            "answer": "当前答案",
            "resume_context": {},
            "chat_history": [
                {"role": "interviewer", "content": "Q1"},
                {"role": "candidate", "content": "旧回答"},
                {"role": "interviewer", "content": "Q2"},
                {"role": "candidate", "content": "当前答案"},  # 与 answer 重复
            ],
            "knowledge_context": [],
            "interview_id": 1,
            "current_index": 1,
        }

        async def _agen(variables):
            yield SimpleNamespace(content='{"score": 8.0, "feedback": "ok", "follow_up": false}')

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            await evaluate_node(state, {"configurable": {"db": mock_db}})

        variables = chain.astream.call_args[0][0]
        assert "当前答案" not in variables["history_text"]
        assert "旧回答" in variables["history_text"]
        assert variables["answer"] == "当前答案"

    async def test_non_json_output_falls_back_to_default_score(self):
        """astream 输出非 JSON 时 extract_json 兜底默认分，不抛异常"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.evaluate import evaluate_node

        state = {
            "current_question": "Q",
            "answer": "A",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
        }

        async def _agen(variables):
            yield SimpleNamespace(content="这是纯文本，没有 JSON")

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()
        mock_msg = MagicMock()
        mock_msg.score = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        with patch(
            "app.workflows.interview.nodes.evaluate.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.evaluate.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await evaluate_node(state, {"configurable": {"db": mock_db}})

        assert result["score"] == 5.0
        assert result["feedback"]  # extract_json 兜底会把原文作为 feedback 返回


@pytest.mark.unit
class TestExtractJson:
    """extract_json utility（app.common.json_utils）"""

    def test_fallback_on_empty(self):
        from app.common.json_utils import extract_json
        result = extract_json("")
        assert result["score"] == 5.0
        assert result["parse_failed"] is True

    def test_fallback_on_plain_text(self):
        from app.common.json_utils import extract_json
        result = extract_json("这是普通文字")
        assert result["score"] == 5.0
        assert result["parse_failed"] is True

    def test_extract_nested_json(self):
        from app.common.json_utils import extract_json
        text = '```json\n{"score": 8.5, "feedback": "优秀"}\n```'
        result = extract_json(text)
        assert result["score"] == 8.5
        assert result["feedback"] == "优秀"


@pytest.mark.unit
class TestRetrieveKnowledgeNode:
    """retrieve_knowledge_node — 委托 RetrievalCheckService，产出 knowledge_context + retrieval_debug"""

    async def test_returns_context_and_debug_info(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from app.workflows.interview.nodes.retrieve_knowledge import retrieve_knowledge_node

        fake_service = SimpleNamespace(
            check_and_retrieve=AsyncMock(return_value=SimpleNamespace(
                final_context=["知识片段1"],
                debug_info={"total_retrieval_rounds": 2, "retry_count": 1},
            ))
        )
        state = {"current_question": "什么是 GIL?"}
        config = {"configurable": {"retrieval_check_service": fake_service}}

        result = await retrieve_knowledge_node(state, config)
        assert result["knowledge_context"] == ["知识片段1"]
        assert result["retrieval_debug"] == {"total_retrieval_rounds": 2, "retry_count": 1}

    async def test_service_missing_returns_empty(self):
        from app.workflows.interview.nodes.retrieve_knowledge import retrieve_knowledge_node

        state = {"current_question": "什么是 GIL?"}
        config = {"configurable": {}}

        result = await retrieve_knowledge_node(state, config)
        assert result == {"knowledge_context": []}


@pytest.mark.unit
class TestAskQuestion:
    """ask_question_node 双模：预生成取题 / 渐进现场生成，越界在写 DB 前触发"""

    async def test_oob_raises_before_db_write(self):
        """next_index 达 total_questions → 渐进模式也不应超过目标题数，写 DB 前抛 RuntimeError"""
        from unittest.mock import AsyncMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.ask_question import ask_question_node

        mock_db = AsyncMock()
        mock_repo = AsyncMock()
        # next_index=5 已达 total_questions=5 → 越界
        interview = SimpleNamespace(
            resume_id=1, target_position="Python", difficulty="medium",
            total_questions=5,
            questions_data=[{"question": f"Q{i}"} for i in range(5)],
        )
        mock_repo.get_by_id_for_user = AsyncMock(return_value=interview)
        state = {"interview_id": 1, "user_id": 1, "current_index": 4, "questions": [{}] * 5}

        with patch(
            "app.workflows.interview.nodes.ask_question.interview_repo",
            mock_repo,
        ):
            with pytest.raises(RuntimeError):
                await ask_question_node(state, {"configurable": {"db": mock_db}})

        mock_repo.update_question_index.assert_not_called()
        mock_repo.create_message.assert_not_called()

    async def test_advances_index_in_bounds(self):
        """预生成模式：questions_data 充足时按索引取下一题并落库"""
        from unittest.mock import AsyncMock, patch
        from types import SimpleNamespace
        from app.workflows.interview.nodes.ask_question import ask_question_node

        mock_db = AsyncMock()
        mock_repo = AsyncMock()
        interview = SimpleNamespace(
            resume_id=1, target_position="Python", difficulty="medium",
            total_questions=5,
            questions_data=[{"question": "Q0"}, {"question": "Q1"}, {"question": "Q2"}],
        )
        mock_repo.get_by_id_for_user = AsyncMock(return_value=interview)
        state = {
            "interview_id": 1,
            "user_id": 1,
            "current_index": 1,
            "questions": [{"question": "Q0"}, {"question": "Q1"}, {"question": "Q2"}],
        }

        with patch(
            "app.workflows.interview.nodes.ask_question.interview_repo",
            mock_repo,
        ):
            result = await ask_question_node(state, {"configurable": {"db": mock_db}})

        mock_repo.update_question_index.assert_called_once_with(mock_db, 1, 2)
        assert result["current_index"] == 2
        assert result["next_question"] == "Q2"

    async def test_progressive_generates_and_persists(self, monkeypatch):
        """渐进模式：next_index >= len(questions) → 现场生成并落库"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.workflows.interview.nodes import ask_question as ask_mod
        from app.workflows.interview.nodes.ask_question import ask_question_node

        interview = SimpleNamespace(
            resume_id=1, target_position="Python 后端", difficulty="medium",
            total_questions=3,
            questions_data=[{"question": "Q0"}, {"question": "Q1"}],
            current_question_index=1,
        )
        resume = SimpleNamespace(parsed_content='{"skills": ["Python"]}')

        db = AsyncMock()
        db.get = AsyncMock(return_value=resume)

        repo = AsyncMock()
        repo.get_by_id_for_user.return_value = interview

        candidates = [{
            "id": 1, "question": "讲下 Python 的 GIL",
            "reference_answer": "GIL 是全局解释器锁...", "key_points": ["GIL"],
            "difficulty": "medium", "position_tag": "python_backend",
            "similarity": 0.9, "source": "from_bank",
        }]

        async def fake_prepare(**kwargs):
            return candidates

        async def fake_gen(**kwargs):
            yield ("token", '{"index": 2, "question": "讲下 GIL（微调）", "category": "technical", "bank_id": 1}')
            yield ("result", {
                "question": "讲下 GIL（微调）", "category": "technical", "bank_id": 1,
                "reference_answer": "GIL 是全局解释器锁...", "source": "from_bank",
            })

        monkeypatch.setattr(ask_mod, "interview_repo", repo)
        monkeypatch.setattr(ask_mod.interview_service, "_prepare_questions", fake_prepare)
        monkeypatch.setattr(ask_mod.ai_service, "generate_next_question_stream", fake_gen)
        monkeypatch.setattr(ask_mod.question_bank_service, "increment_use_count", AsyncMock())

        state = {
            "interview_id": 1, "user_id": 1, "current_index": 1,
            "questions": [{"question": "Q0"}, {"question": "Q1"}],
            "chat_history": [],
        }
        result = await ask_question_node(state, {"configurable": {"db": db}})

        assert result["current_index"] == 2
        assert result["next_question"] == "讲下 GIL（微调）"
        assert result["index"] == 2
        assert interview.questions_data[-1]["question"] == "讲下 GIL（微调）"
        assert interview.current_question_index == 2
        repo.create_message.assert_called_once()

    async def test_progressive_empty_question_raises(self, monkeypatch):
        """渐进模式：流式结果 question 为空 → 抛"下一题生成失败"，不落库"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.workflows.interview.nodes import ask_question as ask_mod
        from app.workflows.interview.nodes.ask_question import ask_question_node

        interview = SimpleNamespace(
            resume_id=1, target_position="Python 后端", difficulty="medium",
            total_questions=3,
            questions_data=[{"question": "Q0"}, {"question": "Q1"}],
            current_question_index=1,
        )
        resume = SimpleNamespace(parsed_content='{"skills": ["Python"]}')

        db = AsyncMock()
        db.get = AsyncMock(return_value=resume)

        repo = AsyncMock()
        repo.get_by_id_for_user.return_value = interview

        candidates = [{
            "id": 1, "question": "讲下 Python 的 GIL",
            "reference_answer": "GIL 是全局解释器锁...", "key_points": ["GIL"],
            "difficulty": "medium", "position_tag": "python_backend",
            "similarity": 0.9, "source": "from_bank",
        }]

        async def fake_prepare(**kwargs):
            return candidates

        async def fake_gen(**kwargs):
            yield ("result", {"question": "", "index": 2, "bank_id": None})

        monkeypatch.setattr(ask_mod, "interview_repo", repo)
        monkeypatch.setattr(ask_mod.interview_service, "_prepare_questions", fake_prepare)
        monkeypatch.setattr(ask_mod.ai_service, "generate_next_question_stream", fake_gen)
        monkeypatch.setattr(ask_mod.question_bank_service, "increment_use_count", AsyncMock())

        state = {
            "interview_id": 1, "user_id": 1, "current_index": 1,
            "questions": [{"question": "Q0"}, {"question": "Q1"}],
            "chat_history": [],
        }

        with pytest.raises(RuntimeError, match="下一题生成失败"):
            await ask_question_node(state, {"configurable": {"db": db}})

        repo.create_message.assert_not_called()
        db.commit.assert_not_called()

    async def test_progressive_prepare_failure_propagates(self, monkeypatch):
        """渐进模式：_prepare_questions 抛异常 → 原样传播出节点，不落库"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.workflows.interview.nodes import ask_question as ask_mod
        from app.workflows.interview.nodes.ask_question import ask_question_node

        interview = SimpleNamespace(
            resume_id=1, target_position="Python 后端", difficulty="medium",
            total_questions=3,
            questions_data=[{"question": "Q0"}, {"question": "Q1"}],
            current_question_index=1,
        )
        resume = SimpleNamespace(parsed_content='{"skills": ["Python"]}')

        db = AsyncMock()
        db.get = AsyncMock(return_value=resume)

        repo = AsyncMock()
        repo.get_by_id_for_user.return_value = interview

        async def fake_prepare(**kwargs):
            raise RuntimeError("检索失败")

        monkeypatch.setattr(ask_mod, "interview_repo", repo)
        monkeypatch.setattr(ask_mod.interview_service, "_prepare_questions", fake_prepare)

        state = {
            "interview_id": 1, "user_id": 1, "current_index": 1,
            "questions": [{"question": "Q0"}, {"question": "Q1"}],
            "chat_history": [],
        }

        with pytest.raises(RuntimeError, match="检索失败"):
            await ask_question_node(state, {"configurable": {"db": db}})

        repo.create_message.assert_not_called()
        db.commit.assert_not_called()


@pytest.mark.unit
class TestGenerateReport:
    async def test_uses_db_feedback_and_zero_fallback(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.generate_report import generate_report_node

        scored_msgs = [
            SimpleNamespace(question_index=0, score=7.0, feedback="回答良好", content="答案A"),
        ]
        questions = [{"question": "Q0"}, {"question": "Q1"}]

        mock_repo = AsyncMock()
        mock_repo.get_scored_messages.return_value = scored_msgs

        async def _agen(variables):
            yield SimpleNamespace(content='{"summary": "整体表现良好", "strengths": ["思路清晰"], "weaknesses": ["深度不足"], "suggestions": ["加强源码阅读"], "hire_recommendation": "建议录用"}')

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        state = {
            "interview_id": 1,
            "questions": questions,
            "resume_context": {},
            "target_position": "Python 后端",
            "current_index": 0,
            "answer": "答案A",
            "score": 9.0,  # 旧代码会把此值泄漏给未评分题
        }

        with patch(
            "app.workflows.interview.nodes.generate_report.interview_repo",
            mock_repo,
        ), patch(
            "app.workflows.interview.nodes.generate_report.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.generate_report.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await generate_report_node(
                state, {"configurable": {"db": AsyncMock()}}
            )

        qs = result["report"]["question_scores"]
        assert qs[0]["score"] == 7.0
        assert qs[0]["feedback"] == "回答良好"   # P2-3: DB feedback 不再硬编码空
        assert qs[1]["score"] == 0.0              # P2-4: 未评分回退 0.0 而非 9.0
        assert qs[1]["feedback"] == ""

    async def test_missing_list_fields_fallback_empty(self):
        """报告 JSON 缺失列表字段时 _as_str_list 回退空列表，不抛异常"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.generate_report import generate_report_node

        async def _agen(variables):
            yield SimpleNamespace(content='{"summary": "只有摘要", "hire_recommendation": "通过"}')

        chain = MagicMock()
        chain.astream = MagicMock(side_effect=_agen)
        mock_prompt = type("MockPrompt", (), {"__or__": lambda self, other: chain})()

        mock_repo = AsyncMock()
        mock_repo.get_scored_messages.return_value = [
            SimpleNamespace(question_index=0, score=7.0, feedback="好", content="答案A"),
        ]
        state = {
            "interview_id": 1,
            "questions": [{"question": "Q0"}],
            "resume_context": {},
            "target_position": "Python",
            "current_index": 0,
            "answer": "答案A",
        }

        with patch(
            "app.workflows.interview.nodes.generate_report.interview_repo",
            mock_repo,
        ), patch(
            "app.workflows.interview.nodes.generate_report.load_prompt",
            return_value=mock_prompt,
        ), patch(
            "app.workflows.interview.nodes.generate_report.get_chat_llm"
        ) as mock_get_llm:
            mock_get_llm.return_value.bind.return_value = MagicMock()
            result = await generate_report_node(
                state, {"configurable": {"db": AsyncMock()}}
            )

        assert result["report"]["summary"] == "只有摘要"
        assert result["report"]["strengths"] == []
        assert result["report"]["weaknesses"] == []
        assert result["report"]["suggestions"] == []
