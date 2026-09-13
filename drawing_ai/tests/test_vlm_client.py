from __future__ import annotations

import pytest

from drawing_ai.vlm_client import extract_json


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_code_fence():
    text = """```json
{"a": 1, "b": [1, 2, 3]}
```"""
    assert extract_json(text) == {"a": 1, "b": [1, 2, 3]}


def test_extract_json_with_preamble():
    text = 'ここに結果があります: {"a": 1} 以上です。'
    assert extract_json(text) == {"a": 1}


def test_extract_json_array():
    text = "回答: [1, 2, 3] です"
    assert extract_json(text) == [1, 2, 3]


def test_extract_json_failure_raises():
    with pytest.raises(ValueError):
        extract_json("no json here at all")
