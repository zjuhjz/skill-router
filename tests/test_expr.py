import pytest

from skillrouter.expr import ExpectError, evaluate

OUTPUT = {"meta": {"platform": "douyin", "id": "123"}, "files": [{"size_bytes": 150000, "path": "a.mp4"}], "media": [{}]}


def test_comparisons_len_exists_matches():
    assert evaluate("output.meta.platform == 'douyin'", OUTPUT)
    assert evaluate("output.files[0].size_bytes > 100000", OUTPUT)
    assert evaluate("len(output.media) >= 1", OUTPUT)
    assert evaluate("exists(output.meta.id)", OUTPUT)
    assert not evaluate("exists(output.missing)", OUTPUT)
    assert evaluate("matches(output.meta.id, '^[0-9]+$')", OUTPUT)


def test_bad_expression_is_rejected():
    with pytest.raises(ExpectError):
        evaluate("import os", OUTPUT)
