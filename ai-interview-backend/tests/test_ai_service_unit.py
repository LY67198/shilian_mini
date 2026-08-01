"""
单元测试 — 测试 AIService._extract_json，无任何外部依赖

运行：
  pytest tests/test_ai_service_unit.py -m "unit"
"""
import pytest
from app.services.client.ai_service import AIService


class TestExtractJson:
    """验证从 DeepSeek 返回的自由文本中提取 JSON 的逻辑"""

    def test_pure_json_object(self):
        text = '{"score": 7.5, "feedback": "ok"}'
        result = AIService._extract_json(text)
        assert result == {"score": 7.5, "feedback": "ok"}

    def test_pure_json_array(self):
        text = '[{"index": 0, "question": "Q1"}]'
        result = AIService._extract_json(text)
        assert result == [{"index": 0, "question": "Q1"}]

    def test_markdown_fenced_json(self):
        text = '好的，这是评分：\n```json\n{"score": 8.0}\n```\n以上是评分。'
        result = AIService._extract_json(text)
        assert result == {"score": 8.0}

    def test_json_with_leading_explanation(self):
        text = '评价如下：这是一个不错的回答。{"score": 6.5, "feedback": "good"}'
        result = AIService._extract_json(text)
        assert result["score"] == 6.5
        assert result["feedback"] == "good"

    def test_json_with_trailing_explanation(self):
        text = '{"score": 9.0, "follow_up": false} —— 评分完成'
        result = AIService._extract_json(text)
        assert result == {"score": 9.0, "follow_up": False}

    def test_empty_text_fallback(self):
        result = AIService._extract_json("")
        assert result["score"] == 5.0
        assert result["parse_failed"] is True

    def test_whitespace_text_fallback(self):
        result = AIService._extract_json("   \n  \t  ")
        assert result["score"] == 5.0
        assert result["parse_failed"] is True

    def test_no_json_in_text_fallback(self):
        result = AIService._extract_json("这是普通文字，没有任何 JSON")
        assert result["score"] == 5.0
        assert result["parse_failed"] is True

    def test_pass_through_dict(self):
        """已是 dict/list 的直接透传"""
        d = {"score": 10}
        assert AIService._extract_json(d) is d

    def test_nested_object(self):
        text = '```json\n{"score": 7.5, "extras": {"follow_up": true, "tags": ["a", "b"]}}\n```'
        result = AIService._extract_json(text)
        assert result["score"] == 7.5
        assert result["extras"]["follow_up"] is True
        assert result["extras"]["tags"] == ["a", "b"]


@pytest.mark.unit
class TestAsQuestionList:
    """generate_questions 结果归一化 — 防止 dict/parse_failed 兜底导致 setdefault AttributeError"""

    def test_list_passthrough(self):
        result = [{"question": "Q1"}, {"question": "Q2"}]
        assert AIService._as_question_list(result) == result

    def test_dict_with_questions_key(self):
        result = {"questions": [{"question": "Q1"}]}
        assert AIService._as_question_list(result) == [{"question": "Q1"}]

    def test_parse_failed_fallback_dict_returns_empty(self):
        result = {"score": 5.0, "feedback": "x", "parse_failed": True}
        assert AIService._as_question_list(result) == []

    def test_non_list_non_dict_returns_empty(self):
        assert AIService._as_question_list("garbage") == []

    def test_filters_non_dict_items(self):
        result = [{"question": "Q1"}, "not-a-dict"]
        assert AIService._as_question_list(result) == [{"question": "Q1"}]


@pytest.mark.unit
class TestSelectAndAdaptQuestions:
    """select_and_adapt_questions 瘦身改造 — LLM 只选题面，参考答案按 bank_id 补齐"""

    CANDIDATES = [
        {
            "id": 1, "question": "讲下 Python 的 GIL",
            "reference_answer": "GIL 是全局解释器锁...", "key_points": ["GIL"],
            "difficulty": "medium", "position_tag": "python_backend",
            "similarity": 0.9, "source": "from_bank",
        },
        {
            "id": 2, "question": "asyncio 事件循环原理",
            "reference_answer": "事件循环基于协程...", "key_points": ["协程"],
            "difficulty": "medium", "position_tag": "python_backend",
            "similarity": 0.85, "source": "from_bank",
        },
        {
            "id": 3, "question": "RESTful 限流怎么做",
            "reference_answer": "令牌桶 + Redis Lua...", "key_points": ["限流"],
            "difficulty": "hard", "position_tag": "python_backend",
            "similarity": 0.7, "source": "from_bank",
        },
    ]

    @staticmethod
    def _patch_llm(monkeypatch, payload):
        """构造 prompt | llm chain 的 mock，返回 payload 作为 LLM 内容。"""
        from unittest.mock import AsyncMock

        from app.services.client import ai_service as mod

        chain = AsyncMock()
        fake_msg = type("M", (), {"content": payload})()
        chain.ainvoke.return_value = fake_msg
        mock_prompt = type("P", (), {"__or__": lambda self, other: chain})()
        monkeypatch.setattr(mod, "load_prompt", lambda name: mock_prompt)
        monkeypatch.setattr(mod, "get_chat_llm", lambda **kw: object())
        return chain

    async def test_merges_reference_answer_from_candidates(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        payload = (
            '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "category": "technical", "bank_id": 1, "source": "from_bank"},'
            ' {"index": 1, "question": "asyncio 事件循环原理", "category": "technical", "bank_id": 2, "source": "from_bank"}]'
        )
        self._patch_llm(monkeypatch, payload)

        questions = await ai_service.select_and_adapt_questions(
            candidates=self.CANDIDATES,
            parsed_resume={"skills": ["Python", "asyncio"]},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        )
        assert len(questions) == 2
        q = questions[0]
        assert q["question"] == "讲下 Python 的 GIL（微调）"
        assert q["bank_id"] == 1
        # 参考答案来自题库候选而非 LLM 输出
        assert q["reference_answer"] == "GIL 是全局解释器锁..."
        assert q["key_points"] == ["GIL"]
        assert q["source"] == "from_bank"

    async def test_sends_slim_candidates_to_llm(self, monkeypatch):
        """LLM 收到的 candidates_json 只含 id+question，不含参考答案，缩小输入。"""
        from app.services.client.ai_service import ai_service

        payload = '[{"index": 0, "question": "Q", "bank_id": 1}]'
        chain = self._patch_llm(monkeypatch, payload)

        await ai_service.select_and_adapt_questions(
            candidates=self.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=1,
        )
        args, _ = chain.ainvoke.call_args
        sent = args[0]["candidates_json"]
        assert '"reference_answer"' not in sent
        assert '"key_points"' not in sent
        assert '"id"' in sent and '"question"' in sent

    async def test_falls_back_to_index_when_bank_id_missing(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        # LLM 漏传 bank_id，仅靠 index 定位候选
        payload = '[{"index": 2, "question": "RESTful 限流怎么做", "category": "system-design"}]'
        self._patch_llm(monkeypatch, payload)

        questions = await ai_service.select_and_adapt_questions(
            candidates=self.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="hard",
            target_n=1,
        )
        assert len(questions) == 1
        assert questions[0]["bank_id"] == 3
        assert questions[0]["reference_answer"] == "令牌桶 + Redis Lua..."

    async def test_skips_unmatched_bank_id(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        payload = (
            '[{"index": 0, "question": "合法题", "bank_id": 1},'
            ' {"index": 99, "question": "幻觉题", "bank_id": 999}]'
        )
        self._patch_llm(monkeypatch, payload)

        questions = await ai_service.select_and_adapt_questions(
            candidates=self.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=1,
        )
        assert len(questions) == 1
        assert questions[0]["bank_id"] == 1

    async def test_empty_llm_output_falls_back_to_first_candidates(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        # LLM 返回 parse_failed 兜底 → 归一化为空 → 回退候选前 N 题
        self._patch_llm(monkeypatch, '{"parse_failed": true, "score": 5.0}')

        questions = await ai_service.select_and_adapt_questions(
            candidates=self.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        )
        assert len(questions) == 2
        assert questions[0]["bank_id"] == 1
        assert questions[1]["bank_id"] == 2
        assert questions[0]["reference_answer"] == "GIL 是全局解释器锁..."

    async def test_pads_when_llm_returns_fewer_than_target(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        payload = '[{"index": 0, "question": "仅此一题", "bank_id": 1}]'
        self._patch_llm(monkeypatch, payload)

        questions = await ai_service.select_and_adapt_questions(
            candidates=self.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=3,
        )
        assert len(questions) == 3
        assert {q["bank_id"] for q in questions} == {1, 2, 3}


@pytest.mark.unit
class TestSelectAndAdaptQuestionsStream:
    """select_and_adapt_questions_stream — chain.astream 逐 token 产出的流式选题孪生"""

    @staticmethod
    def _patch_llm_stream(monkeypatch, chunks, capture=None):
        from unittest.mock import AsyncMock

        from app.services.client import ai_service as mod

        chain = AsyncMock()

        async def fake_astream(**kwargs):
            if capture is not None:
                capture["input"] = kwargs.get("input")
            for c in chunks:
                yield type("C", (), {"content": c})()

        chain.astream = fake_astream
        mock_prompt = type("P", (), {"__or__": lambda self, other: chain})()
        monkeypatch.setattr(mod, "load_prompt", lambda name: mock_prompt)
        monkeypatch.setattr(mod, "get_chat_llm", lambda **kw: object())
        return chain

    async def test_yields_tokens_then_merged_result(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        chunks = [
            '[{"index": 0, "question": "讲下 Python 的 GIL（微调）", "category": "technical", "bank_id": 1, "source": "from_bank"},',
            ' {"index": 1, "question": "asyncio 事件循环原理", "category": "technical", "bank_id": 2, "source": "from_bank"}]',
        ]
        self._patch_llm_stream(monkeypatch, chunks)

        events = []
        async for kind, payload in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={"skills": ["Python", "asyncio"]},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        ):
            events.append((kind, payload))

        kinds = [k for k, _ in events]
        assert kinds == ["token", "token", "result"]
        questions = events[-1][1]
        assert len(questions) == 2
        assert questions[0]["bank_id"] == 1
        # 参考答案来自题库候选而非 LLM 输出
        assert questions[0]["reference_answer"] == "GIL 是全局解释器锁..."
        assert questions[1]["reference_answer"] == "事件循环基于协程..."

    async def test_falls_back_when_aggregated_text_is_garbage(self, monkeypatch):
        from app.services.client.ai_service import ai_service

        self._patch_llm_stream(monkeypatch, ["这是", "乱码"])

        result = None
        async for kind, payload in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        ):
            if kind == "result":
                result = payload

        assert result is not None
        assert len(result) == 2  # 回退候选前 N 题
        assert result[0]["bank_id"] == 1

    async def test_llm_error_still_yields_result_with_fallback(self, monkeypatch):
        """astream 中途抛异常 → 仍 yield ("result", ...)，回退候选前 N 题（不 NameError）。"""
        from unittest.mock import AsyncMock

        from app.services.client import ai_service as mod
        from app.services.client.ai_service import ai_service

        async def broken_astream(**kwargs):
            yield type("C", (), {"content": '[{"index": 0, "question": "Q", "bank_id": 1}'})()
            raise RuntimeError("connection error")

        chain = AsyncMock()
        chain.astream = broken_astream
        mock_prompt = type("P", (), {"__or__": lambda self, other: chain})()
        monkeypatch.setattr(mod, "load_prompt", lambda name: mock_prompt)
        monkeypatch.setattr(mod, "get_chat_llm", lambda **kw: object())

        result = None
        async for kind, payload in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=2,
        ):
            if kind == "result":
                result = payload

        assert result is not None
        assert len(result) == 2  # 异常后仍回退候选前 N 题
        assert result[0]["bank_id"] == 1

    async def test_sends_slim_candidates_to_llm(self, monkeypatch):
        """astream 收到的 candidates_json 只含 id+question，不含参考答案（对齐 v2 slim 测试）。"""
        import json

        from app.services.client.ai_service import ai_service

        captured = {}
        self._patch_llm_stream(
            monkeypatch,
            ['[{"index": 0, "question": "Q", "bank_id": 1}]'],
            capture=captured,
        )

        async for _ in ai_service.select_and_adapt_questions_stream(
            candidates=TestSelectAndAdaptQuestions.CANDIDATES,
            parsed_resume={},
            target_position="Python 后端",
            difficulty="medium",
            target_n=1,
        ):
            pass

        sent = json.loads(captured["input"]["candidates_json"])
        assert len(sent) == 3
        assert all(set(q) == {"id", "question"} for q in sent)
        assert sent == [
            {"id": 1, "question": "讲下 Python 的 GIL"},
            {"id": 2, "question": "asyncio 事件循环原理"},
            {"id": 3, "question": "RESTful 限流怎么做"},
        ]