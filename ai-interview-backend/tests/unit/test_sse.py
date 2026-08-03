"""sse.py — on_chat_model_stream 按节点区分 question_chunk（出题）/ chunk（反馈/报告）"""
import json

import pytest

from app.workflows._shared.sse import astream_to_sse


def _parse(sse_str: str):
    lines = sse_str.strip().splitlines()
    event = lines[0].replace("event: ", "").strip()
    data_line = next((l for l in lines if l.startswith("data: ")), "")
    return event, json.loads(data_line.replace("data: ", "", 1))


def _stream_event(node: str | None = None) -> dict:
    ev = {
        "event": "on_chat_model_stream",
        "name": "ChatOpenAI",
        "data": {"chunk": type("C", (), {"content": "文本"})()},
    }
    if node:
        ev["metadata"] = {"langgraph_node": node}
    return ev


async def _astream(events: list[dict]):
    """astream_to_sse 内部用 async for 迭代，测试需异步迭代器包装"""
    for ev in events:
        yield ev


@pytest.mark.unit
class TestAstreamToSseQuestionChunk:
    async def test_ask_question_node_yields_question_chunk(self):
        events = [
            {"event": "on_chain_start", "name": "ask_question", "data": {}},
            _stream_event("ask_question"),
            {"event": "on_chain_end", "name": "ask_question",
             "data": {"output": {"next_question": "Q", "index": 1}}},
        ]
        out = [_parse(s) for s in [x async for x in astream_to_sse(_astream(events))]]
        assert out[0][0] == "status"
        assert out[1][0] == "question_chunk"
        assert out[1][1]["content"] == "文本"

    async def test_evaluate_node_yields_chunk(self):
        events = [_stream_event("evaluate")]
        out = [_parse(s) for s in [x async for x in astream_to_sse(_astream(events))]]
        # done 是末尾事件；前面只有 evaluate 的 chunk
        assert out[0][0] == "chunk"
        assert out[-1][0] == "done"

    async def test_no_metadata_falls_back_to_tracked_node(self):
        """metadata 缺失时用 on_chain_start 维护的 _current_node 兜底"""
        events = [
            {"event": "on_chain_start", "name": "ask_question", "data": {}},
            _stream_event(),  # 无 metadata
        ]
        out = [_parse(s) for s in [x async for x in astream_to_sse(_astream(events))]]
        assert out[1][0] == "question_chunk"
