#!/usr/bin/env python3
"""自建 benchmark 横向对比（7 case，含真实业务）：ours vs rtk/drain3/grep/tail/headroom。

用法: export DEEPSEEK_API_KEY=... && python3 benchmark/run_horizontal.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark.horizontal import run_horizontal_benchmark
from benchmark.competitors import available_compressors

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")

if __name__ == "__main__":
    cases = []
    for f in sorted(glob.glob(os.path.join(SAMPLES, "case_*.json"))):
        c = json.load(open(f, encoding="utf-8"))
        if "ground_truth" in c:
            cases.append(c)
    methods = available_compressors()  # rtk/drain3/grep/tail/headroom（可用的）
    print(f"共 {len(cases)} case: {[c['id'] for c in cases]}")
    print(f"竞品方法: {methods}")
    summary = run_horizontal_benchmark(cases, methods)
    print("\n" + "=" * 78)
    print("横向对比汇总（7 case）")
    print("=" * 78)
    baseline_score = summary.get("baseline", {}).get("avg_score", 1.0)
    print(f"{'方法':<14} {'avg_tokens':<12} {'压缩率':<9} {'准确率':<9} 对比baseline")
    print("-" * 78)
    for m in ["baseline", "ours"] + methods:
        d = summary[m]
        marker = ""
        if m != "baseline":
            drop = baseline_score - d["avg_score"]
            marker = f"{'✅ 保准' if drop <= 0.05 else '❌ 掉分'}"
        print(f"{m:<14} {d['avg_tokens']:<12.0f} {d['avg_reduction']*100:<9.1f} {d['avg_score']*100:<9.1f} {marker}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "horizontal_7case.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存: {out}")
