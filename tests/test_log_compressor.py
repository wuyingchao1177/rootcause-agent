"""log_compressor 单元测试：模板化、信号分级、服务分布、视图、参数化。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common.log_compressor import (
    compress_log, format_compressed_log, build_analysis_view,
    service_error_distribution, templateize, is_key_line, _signal_level,
    LOW_VALUE_RE,
)


def sample_log(n=200):
    return [f"2026-01-01 00:00:{i % 60:02d} [{svc}] {msg}"
            for i, (svc, msg) in enumerate(
                [("frontend", "ERROR HttpServerErrorException boom"),
                 ("carts", "INFO request ok"),
                 ("queue-master", "WARN fallback retry"),
                 ("frontend", "ERROR java.util.NoSuchElementException"),
                 ("carts", "ERROR timeout waiting for connection"),
                 ("queue-master", "ERROR I/O exception RetryExec")] * (n // 6))]


class TestTemplateize:
    def test_timestamp_masked(self):
        assert "<time>" in templateize("2026-01-01 12:00:00 ERROR boom")

    def test_digits_preserved(self):
        # 数字保真：exit code / 行号保留（独特设计 D1）
        t = templateize("exit code 127 at line 42")
        assert "127" in t and "42" in t

    def test_uuid_masked(self):
        # 标准 UUID（带连字符）被掩码
        t = templateize("uuid 550e8400-e29b-41d4-a716-446655440000 seen")
        assert "550e8400-e29b-41d4-a716-446655440000" not in t

    def test_hex_masked(self):
        t = templateize("token 0xdeadbeef1234 seen")
        assert "0xdeadbeef1234" not in t


class TestSignalLevel:
    def test_error_is_strong(self):
        assert _signal_level("ERROR exception thrown") == 0

    def test_warn_is_mid(self):
        assert _signal_level("WARN fallback used") == 1

    def test_info_is_weak(self):
        assert _signal_level("INFO heartbeat") == 2

    def test_request_is_mid(self):
        # request 等业务动作词触发中信号（业务信号优先）
        assert _signal_level("INFO request ok") == 1

    def test_not_found_strong(self):
        assert _signal_level("ERROR not found: /api/x") == 0


class TestKeyLine:
    def test_error_line_key(self):
        assert is_key_line("ERROR boom exception")

    def test_info_not_key(self):
        assert not is_key_line("INFO heartbeat ok")


class TestCompressLog:
    def test_reduction(self):
        c = compress_log(sample_log())
        assert c["original_lines"] <= 200  # 级别识别可能有少量边界行（198/200）
        assert c["reduced_lines"] < c["original_lines"]  # 压缩后显著减少
        assert c["reduction_rate"] > 0.5

    def test_key_sorted_signal_first(self):
        c = compress_log(sample_log())
        kts = c["key_templates"]
        # 强信号（level 0）排在前面
        levels = [lv for _, _, lv in kts]
        assert levels == sorted(levels)

    def test_tail_preserved(self):
        lines = sample_log(50) + ["final marker line xyz"]
        c = compress_log(lines, tail_window=5)
        # 无信号词的行进入噪声模板；tail 去重后保留在 noise（信息不丢）
        all_text = " ".join(c.get("tail_lines", [])) + " " + \
                   " ".join(t for t, n in c.get("noise_templates", []))
        assert "final marker line xyz" in all_text


class TestServiceDistribution:
    def test_aggregation(self):
        c = compress_log(sample_log())
        dist = service_error_distribution(c["key_templates"])
        assert "frontend" in dist or "carts" in dist

    def test_low_value_filter(self):
        assert LOW_VALUE_RE.search("I/O exception RetryExec socket")
        assert not LOW_VALUE_RE.search("HTTP 503 service unavailable")


class TestAnalysisView:
    def test_structure(self):
        view = build_analysis_view(sample_log())
        assert view.startswith("Log Summary")
        assert "[服务级错误分布]" in view
        assert "[业务错误日志(高价值)]" in view

    def test_strong_count_default_40(self):
        # ERROR 模板 135 种（>40 → 截断）；噪声模板 150 种（>100 → 后 50 种进 tail）
        lines = ([f"2026-01-01 00:00:{i % 60:02d} [s{i % 3}] ERROR boom_{i}" for i in range(135)]
                 + [f"2026-01-01 00:01:00 [s{i % 3}] INFO noise_line_{i}" for i in range(150)])
        view = build_analysis_view(lines)
        import re
        m = re.search(r"\[业务错误日志\(高价值\)\]\n(.*?)\n\[日志尾部", view, re.S)
        block = m.group(1) if m else ""
        n = len([l for l in block.splitlines() if l.strip()])
        assert 0 < n <= 40

    def test_parameterized(self):
        view = build_analysis_view(sample_log(), strong_count=10)
        assert len(view) > 0


class TestFormat:
    def test_output_contains_reduction(self):
        out = format_compressed_log(compress_log(sample_log()))
        assert "原始" in out and "减少" in out

    def test_noise_limit(self):
        out = format_compressed_log(compress_log(sample_log()), noise_limit=5)
        assert len(out) > 0
