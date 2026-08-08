"""轻量 BM25 相关性打分器（零第三方依赖）。

复刻自 headroom-ai 的 BM25Scorer 实现（headroom/relevance/bm25.py），
为避免开源项目引入第三方运行时依赖，按相同算法独立实现：
- 同一 tokenization（UUID / 4+ 位数字 / 字母数字）
- 同一 IDF 公式（Lucene/ES 的 floored 变体: log((N-n+0.5)/(n+0.5)+1)）
- 同一评分公式（k1=1.5, b=0.75）+ 长匹配奖励（>=8 字符 +0.3）
- score_batch 以整批为语料计算 IDF（稀有词加权）

用于从关键日志模板中选出与错误查询最相关的行（帕累托优化实验验证有效）。
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\b\d{4,}\b"
    r"|[a-zA-Z0-9_]+"
)


class BM25Scorer:
    """BM25 keyword relevance scorer (zero dependencies)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75,
                 normalize_score: bool = True, max_score: float = 10.0):
        self.k1 = k1
        self.b = b
        self.normalize_score = normalize_score
        self.max_score = max_score

    def _tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        return _TOKEN_PATTERN.findall(text.lower())

    def _compute_idf(self, term: str, doc_count: int, doc_freq: int) -> float:
        if doc_freq <= 0:
            return 0.0
        return math.log((doc_count - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0)

    def _bm25_score(self, doc_tokens: list[str], query_tokens: list[str],
                    avg_doc_len: float | None = None,
                    idf_map: dict[str, float] | None = None) -> tuple[float, list[str]]:
        if not doc_tokens or not query_tokens:
            return 0.0, []
        doc_len = len(doc_tokens)
        avgdl = avg_doc_len or doc_len or 1
        doc_freq = Counter(doc_tokens)
        query_freq = Counter(query_tokens)
        score = 0.0
        matched: list[str] = []
        for term, qf in query_freq.items():
            if term not in doc_freq:
                continue
            f = doc_freq[term]
            matched.append(term)
            idf = idf_map.get(term, math.log(2.0)) if idf_map is not None else math.log(2.0)
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1 - self.b + self.b * doc_len / avgdl)
            score += idf * numerator / denominator * qf
        return score, matched

    def score_batch(self, items: list[str], context: str) -> list[dict]:
        """对一批 item 按与 context 的相关性打分。

        返回: [{"score": float, "matched_terms": list[str]}, ...]
        （与 headroom 版同排序，独立结构便于零依赖使用）
        """
        context_tokens = self._tokenize(context)
        if not context_tokens:
            return [{"score": 0.0, "matched_terms": []} for _ in items]
        all_tokens = [self._tokenize(item) for item in items]
        avg_len = sum(len(t) for t in all_tokens) / max(len(items), 1)
        n_docs = len(all_tokens)
        doc_freq_across: Counter = Counter()
        for tokens in all_tokens:
            doc_freq_across.update(set(tokens))
        idf_map = {term: self._compute_idf(term, n_docs, doc_freq_across[term])
                   for term in set(context_tokens) if term in doc_freq_across}
        results = []
        for item_tokens in all_tokens:
            raw, matched = self._bm25_score(item_tokens, context_tokens,
                                            avg_doc_len=avg_len, idf_map=idf_map)
            normalized = min(1.0, raw / self.max_score) if self.normalize_score else raw
            long_matches = [t for t in matched if len(t) >= 8]
            if long_matches:
                normalized = min(1.0, normalized + 0.3)
            results.append({"score": normalized, "matched_terms": matched[:10]})
        return results
