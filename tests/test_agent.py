"""locator.agent 单元测试：analyze_logs 与短路求值判定（不调 LLM）。

注：analyze_root_cause / locate_root_cause 的完整链路需要 LLM API，
此处只测确定性部分（日志分析 + 短路判定逻辑）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from locator.agent import analyze_logs, parse_problem


class TestParseProblem:
    def test_keywords_extracted(self):
        p = parse_problem("Redis 连接池超时 RedisTimeoutException 订单接口超时")
        assert len(p["keywords"]) > 0
        assert "problem" in p

    def test_empty_input(self):
        p = parse_problem("")
        assert isinstance(p, dict)


class TestAnalyzeLogs:
    def test_returns_views(self):
        log = ["2026-01-01 00:00:00 [frontend] ERROR RedisTimeoutException boom"]
        al = analyze_logs(log)
        assert "formatted" in al
        assert "analysis_view" in al
        assert "error_clues" in al

    def test_error_clues_detected(self):
        log = ["2026-01-01 00:00:00 [s] ERROR timeout waiting for connection",
               "2026-01-01 00:00:01 [s] INFO request ok"] * 3
        al = analyze_logs(log)
        assert len(al["error_clues"]) >= 1

    def test_analysis_view_signal_first(self):
        # 分析视图结构：信号优先（服务分布 + 高价值错误 + 尾部兜底）
        log = ["2026-01-01 00:00:%02d [%s] %s" % (i % 60, ["a", "b", "c"][i % 3],
               "ERROR boom_%d" % (i % 9) if i % 7 == 0 else
               ("WARN fallback_%d" % (i % 5) if i % 5 == 0 else "INFO request ok_%d" % (i % 11)))
               for i in range(1000)]
        al = analyze_logs(log)
        view = al["analysis_view"]
        assert view.startswith("Log Summary")
        assert "[服务级错误分布]" in view
        assert "[业务错误日志(高价值)]" in view

    def test_real_case(self):
        base = Path(__file__).parent.parent
        case = json.load(open(base / "samples" / "case_1_redis_pool.json"))
        al = analyze_logs(case["logs"][:5000])
        assert len(al["error_clues"]) >= 1


class TestShortCircuitRule:
    """短路判定规则（与 locate_root_cause 中逻辑一致）。"""

    def _should_short(self, clue_text):
        first = clue_text.lower()
        return any(k in first for k in ("exception", "timeout", "refused", "oom", "panic", "not found"))

    def test_exception_short_circuits(self):
        assert self._should_short("ERROR RedisTimeoutException: JedisConnectionException")

    def test_info_does_not(self):
        assert not self._should_short("WARN fallback used, circuit open")

    def test_error_without_exception_class(self):
        assert not self._should_short("ERROR request failed with 500")
