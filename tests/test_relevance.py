"""relevance.py（BM25 零依赖复刻）单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common.relevance import BM25Scorer


class TestBM25Scorer:
    def test_score_range(self):
        scorer = BM25Scorer()
        items = ["ERROR connection refused to redis", "INFO request ok",
                 "ERROR timeout waiting for pool", "WARN fallback retry"]
        scores = scorer.score_batch(items, "error timeout refused")
        for s in scores:
            assert 0.0 <= s["score"] <= 5.0

    def test_relevant_ranks_first(self):
        scorer = BM25Scorer()
        items = ["INFO heartbeat", "ERROR connection refused",
                 "INFO metrics report", "ERROR timeout"]
        scores = scorer.score_batch(items, "error refused timeout")
        ranked = [it for it, s in sorted(zip(items, scores), key=lambda x: -x[1]["score"])]
        # 错误行应排前
        assert ranked[0].startswith("ERROR")

    def test_empty_items(self):
        scorer = BM25Scorer()
        assert scorer.score_batch([], "query") == []

    def test_empty_query(self):
        scorer = BM25Scorer()
        items = ["a b c"]
        assert scorer.score_batch(items, "")[0]["score"] == 0.0

    def test_uuid_tokenized(self):
        scorer = BM25Scorer()
        items = ["request id 550e8400-e29b-41d4-a716-446655440000 failed"]
        scores = scorer.score_batch(items, "550e8400-e29b-41d4-a716-446655440000")
        # 标准 UUID 按独立 token 匹配（matched_terms 是匹配 term 列表）
        assert len(scores[0]["matched_terms"]) == 1


class TestBM25Determinism:
    def test_deterministic(self):
        scorer = BM25Scorer()
        items = ["ERROR a b", "INFO c", "ERROR d e"]
        s1 = scorer.score_batch(items, "error")
        s2 = scorer.score_batch(items, "error")
        assert [x["score"] for x in s1] == [x["score"] for x in s2]
