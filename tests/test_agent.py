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


class TestJavaCodeSupport:
    """Java 代码定位支持（真实业务案例：订单性质字段溯源）。"""

    def test_java_signature_extraction(self):
        from common.code_compressor import extract_java_signatures
        java = """public class BwhOrderServiceImpl {
    private final OrderProxyService orderProxyService;
    public JSONObject saasgetOrderInfo(OrderQueryParam param, String userName) {
        JSONObject response = orderProxyService.getOrderInfo(param, userName);
        invokeStrategyService.invokeStrategy(response, relationVO);
        return response;
    }
}"""
        sigs = extract_java_signatures(java)
        assert any(s["kind"] == "class" and s["name"] == "BwhOrderServiceImpl" for s in sigs)
        assert any(s["kind"] == "method" and s["name"] == "saasgetOrderInfo" for s in sigs)

    def test_java_compress_fallback(self):
        from common.code_compressor import compress_code
        java = """public class A {
    public String getOrderInfo(OrderParam p) {
        JSONObject r = proxy.getOrderInfo(p);
        return r.toString();
    }
}"""
        out = compress_code(java, file_path="A.java", keywords=["getOrderInfo"])
        assert "A.java" in out
        assert "getOrderInfo" in out

    def test_parse_problem_camelcase_keywords(self):
        from locator.agent import parse_problem
        p = parse_problem("接口 getOrderInfo 返回的 nature_name 为什么包含指派订单（order_id=70409418871768）")
        kws = p["keywords"]
        assert any("getOrderInfo" in k for k in kws)
        assert any("nature_name" in k for k in kws)
        assert any("order_id" in k for k in kws)

    def test_locate_code_java(self, tmp_path):
        from locator.agent import locate_code
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "BwhOrderServiceImpl.java").write_text(
            "public class BwhOrderServiceImpl { public JSONObject saasgetOrderInfo() { return null; } }")
        (tmp_path / "src" / "NoiseUtil.java").write_text("public class NoiseUtil {}")
        found = locate_code(str(tmp_path), ["getOrderInfo", "nature_name"], "")
        names = [f["path"].split("/")[-1] for f in found]
        assert "BwhOrderServiceImpl.java" in names
        assert "NoiseUtil.java" not in names


class TestRecordFilter:
    """记录级过滤（log_search trace_detail 思想的通用复刻：空业务标识记录不产生 token）。"""

    def test_drop_empty_uri_records(self):
        from common.log_compressor import _drop_empty_identifier_records
        lines = [
            '[span0] uri=kefu/getOrderInfo app=[wb] status=0',
            '[span0req] {"interface": "/kefu/getOrderInfo"}',
            '[span1] uri= app=[bridge] status=0 logs=0',
            '[span1call] GET status=0',
            '[span2] uri=[URI not found] app=[x] status=0',
            '[span3] uri=dos/getOrderInfo app=[dos] status=0',
        ]
        f = _drop_empty_identifier_records(lines)
        kept = [l.split()[0] for l in f if l.startswith("[span")]
        assert not any(k.startswith(("[span1]", "[span2]")) for k in kept)
        assert any(k.startswith("[span0]") for k in kept)
        assert any(k.startswith("[span3]") for k in kept)

    def test_keep_normal_records(self):
        from common.log_compressor import _drop_empty_identifier_records
        lines = [
            '[span0] uri=/api/order/getOrderInfo app=[wb] status=0',
            '[log1] 2026-08-07 14:00:00 ERROR RedisTimeoutException',
        ]
        f = _drop_empty_identifier_records(lines)
        assert len(f) == 2

    def test_placeholder_rows(self):
        from common.log_compressor import _is_placeholder_row
        assert _is_placeholder_row("2026-01-01 [uri not found]")
        assert _is_placeholder_row("2026-01-01 00:00:00 ERROR")
        assert not _is_placeholder_row("2026-01-01 ERROR boom redis timeout")
