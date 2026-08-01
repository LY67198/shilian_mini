"""
单元测试 — try_parse_partial_array 增量 JSON 数组解析（SSE 流式出题前端逻辑的后端等价验证）

运行：
  pytest tests/test_json_partial_array.py -m "unit"
"""
import pytest
from app.common.json_utils import try_parse_partial_array


@pytest.mark.unit
class TestTryParsePartialArray:
    def test_full_array(self):
        assert try_parse_partial_array('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]

    def test_partial_array_with_complete_elements(self):
        assert try_parse_partial_array('[{"a": 1}, {"a": 2}') == [{"a": 1}, {"a": 2}]

    def test_partial_array_with_incomplete_last_element(self):
        assert try_parse_partial_array('[{"a": 1}, {"a": 2') is None

    def test_empty_returns_none(self):
        assert try_parse_partial_array("") is None

    def test_non_array_returns_none(self):
        assert try_parse_partial_array('{"a": 1}') is None

    def test_partial_non_array_returns_none(self):
        assert try_parse_partial_array('{"a": 1') is None
