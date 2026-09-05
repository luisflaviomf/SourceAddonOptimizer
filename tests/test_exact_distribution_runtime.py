import pytest

from exact_distribution.runtime import render_runtime


def test_runtime_rejects_malformed_contract_and_is_content_isolated():
    with pytest.raises(ValueError):
        render_runtime({})
    base = {"schema": "exact-distribution-runtime/v1", "family": "lzwav", "shard": 0,
            "content_id": "a" * 64, "entries": []}
    one = render_runtime(base)
    two = render_runtime(dict(base, content_id="b" * 64))
    assert one != two and b"a" * 64 in one and b"b" * 64 in two
    assert b"codex_exact_distribution" in one
