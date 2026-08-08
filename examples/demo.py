#!/usr/bin/env python3
"""一键 demo：压缩示例日志 + 展示分析视图（无需 LLM）。

用法:
    python3 examples/demo.py                 # 用内置示例日志
    python3 examples/demo.py /path/to/app.log  # 用自己的日志文件
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.log_compressor import build_analysis_view


def demo_log():
    """构造一个微服务故障示例日志（Redis 连接池耗尽）。"""
    lines = []
    for i in range(800):
        svc = ["frontend", "carts", "queue-master"][i % 3]
        if i % 9 == 0:
            msg = "ERROR RedisTimeoutException: JedisConnectionException: Could not get a resource from the pool"
        elif i % 13 == 0:
            msg = "ERROR java.util.NoSuchElementException: Pool exhausted"
        elif i % 5 == 0:
            msg = "WARN fallback: redis unavailable, use local cache"
        else:
            msg = f"INFO request ok order_id={i} duration={i % 50}ms"
        lines.append(f"2026-01-01 00:00:{i % 60:02d} [{svc}] {msg}")
    return lines


def main():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        log_lines = open(sys.argv[1], encoding="utf-8", errors="ignore").read().splitlines()
        print(f"读取日志: {sys.argv[1]} ({len(log_lines)} 行)")
    else:
        log_lines = demo_log()
        print(f"使用内置示例日志 ({len(log_lines)} 行)")

    view = build_analysis_view(log_lines)
    print("=" * 60)
    print(f"压缩分析视图（{len(view):,} chars，原始 {len(log_lines):,} 行）")
    print("=" * 60)
    print(view)
    print("=" * 60)
    print("提示: 该视图可直接作为 LLM 根因定位的输入上下文（省 90%+ token）。")
    print("完整定位: from locator.agent import locate_root_cause")


if __name__ == "__main__":
    main()
