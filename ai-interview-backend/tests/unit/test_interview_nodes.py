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

    async def test_fewer_questions_than_target_still_finishes(self):
        """LLM 出题不足（len(questions) < total_questions）时按实际题数结束"""
        state = {"current_index": 4, "questions": [{}] * 5, "total_questions": 8}
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
    """evaluate_node — 使用 prompt | llm.with_structured_output(ScoreResult) 评分，需 mock LLM"""

    async def test_returns_score_and_feedback(self):
        """evaluate_node returns score + feedback from structured output"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.workflows.interview.nodes.evaluate import evaluate_node
        from app.workflows.interview.state import ScoreResult

        state = {
            "current_question": "请介绍 Python 的 GIL",
            "answer": "GIL 是全局解释器锁...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 1,
            "user_id": 42,
        }

        chain = AsyncMock()
        chain.ainvoke.return_value = ScoreResult(score=7.5, feedback="回答良好")
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
            mock_get_llm.return_value.with_structured_output.return_value = object()
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
        from app.workflows.interview.nodes.evaluate import evaluate_node
        from app.workflows.interview.state import ScoreResult

        state = {
            "current_question": "What is dependency injection?",
            "answer": "DI is a pattern where...",
            "resume_context": {},
            "chat_history": [],
            "knowledge_context": [],
            "interview_id": 5,
            "current_index": 2,
        }

        chain = AsyncMock()
        chain.ainvoke.return_value = ScoreResult(score=9.0, feedback="Excellent")
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
            mock_get_llm.return_value.with_structured_output.return_value = object()
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
    """ask_question_node — 越界必须在写 DB 之前触发"""

    async def test_oob_raises_before_db_write(self):
        from unittest.mock import AsyncMock, patch
        from app.workflows.interview.nodes.ask_question import ask_question_node

        mock_db = AsyncMock()
        mock_repo = AsyncMock()
        state = {"interview_id": 1, "current_index": 4, "questions": [{}] * 5}

        with patch(
            "app.workflows.interview.nodes.ask_question.interview_repo",
            mock_repo,
        ):
            with pytest.raises(RuntimeError):
                await ask_question_node(state, {"configurable": {"db": mock_db}})

        mock_repo.update_question_index.assert_not_called()
        mock_repo.create_message.assert_not_called()

    async def test_advances_index_in_bounds(self):
        from unittest.mock import AsyncMock, patch
        from app.workflows.interview.nodes.ask_question import ask_question_node

        mock_db = AsyncMock()
        mock_repo = AsyncMock()
        state = {
            "interview_id": 1,
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
