#!/usr/bin/env python3
"""真实业务 Demo：加载真实故障 case → 信号压缩 → AI 定位 → 与人工根因对照。

用法:
    export DEEPSEEK_API_KEY=sk-...                # 或 LLM_BASE_URL/LLM_MODEL 切换服务
    python3 examples/demo_real.py --case case_5   # 跑单个 case
    python3 examples/demo_real.py --all           # 跑全部真实 case

输出: 问题 → 压缩对比（行数/token）→ AI 七节报告 → 人工根因对照评分
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common.log_compressor import build_analysis_view, compress_log
from locator.agent import locate_root_cause

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(BASE, "samples")

# 每个 case 的关键证据词（用于 AI vs 人工一致性评分）
CASE_EVIDENCE = {
    "case_5_order_nature": ["QLE", "assign_type", "invokeStrategy", "拼接", "规则", "策略"],
    "case_6_cheat_field": ["checkOrderCheatInfo", "levelType", "未调用", "默认值", "特快", "101"],
    "case_7_order_status": ["orderEnum", "order_status", "Apollo", "映射", "订单完成"],
}


def load_cases(only: str | None):
    """加载 samples/case_*.json（真实业务 case）。"""
    if only:
        key = only.replace("case_", "")
        cand = os.path.join(SAMPLES, f"case_{key}*.json")
    else:
        cand = os.path.join(SAMPLES, "case_[567]*.json")
    files = sorted(glob.glob(cand))
    if not files and not only:
        files = sorted(glob.glob(os.path.join(SAMPLES, "case_*.json")))
    cases = []
    for f in files:
        c = json.load(open(f, encoding="utf-8"))
        if "ground_truth" in c and "code_files" in c:
            cases.append(c)
    return cases


def evaluate_match(ai_text: str, evidence_words: list[str]) -> dict:
    """AI 结论 vs 人工根因：关键证据词命中率。"""
    hits = [w for w in evidence_words if w in ai_text]
    return {
        "hits": hits,
        "missed": [w for w in evidence_words if w not in ai_text],
        "score": len(hits) / len(evidence_words) if evidence_words else 0,
    }


def run_case(case: dict, repo_root: str = "/Users/didi/IdeaProjects/sail2026") -> dict:
    cid = case["id"]
    print("=" * 74)
    print(f"📋 CASE: {cid}")
    print("=" * 74)
    print(f"❓ 问题: {case['problem'][:150]}...\n")

    # ① 压缩对比
    comp = compress_log(case["logs"])
    view = build_analysis_view(case["logs"])
    reduction = 1 - comp["reduced_lines"] / max(comp["original_lines"], 1)
    print(f"🧬 信号压缩: {comp['original_lines']} 行 → {comp['reduced_lines']} 行 "
          f"(压缩率 {reduction*100:.1f}%) | 视图 {len(view)} chars\n")

    # ② AI 定位
    print("🤖 AI 定位中（LLM 调用，约 30-60s）...")
    cf = case["code_files"]
    if isinstance(cf, dict):
        rd = "\n\n".join(f"===== {k} =====\n{v}" for k, v in cf.items())
    else:  # 兼容旧格式（list）
        rd = "\n\n".join(str(x)[:2000] for x in cf)
    r = locate_root_cause(
        problem_text=case["problem"],
        log_lines=case["logs"],
        repo_root=repo_root,
        runtime_data=rd,
    )
    ai_text = r["root_cause"]

    # 提取一句话结论（⑦ 节）
    m7 = re.search(r"### ⑦.*?\n\n(.*)", ai_text, re.S)
    one_liner = m7.group(1).strip() if m7 else ai_text[:200]
    print("\n🎯 AI 一句话结论:")
    print(f"   {one_liner[:300]}\n")
    print("📄 完整七节报告已生成（可在返回结果中查看）\n")

    # ③ 人工对照
    gt = case.get("ground_truth", {})
    gt_text = gt.get("root_cause", "")
    ev = evaluate_match(ai_text, CASE_EVIDENCE.get(cid, []))
    print("🧑‍🔧 人工根因（ground_truth）:")
    print(f"   {gt_text[:220]}...\n")
    print(f"📊 一致性评分: {ev['score']*100:.0f}%  "
          f"（命中 {ev['hits']} | 未提 {ev['missed'] or '无'}）\n")
    print(f"⚡ 元信息: 代码文件 {r['compression_stats']['code_files']} 个 | "
          f"短路 {r['short_circuited']} | 输入 ~{r['tokens_consumed']['input']} tok\n")
    return {"case": cid, "score": ev["score"], "hits": ev["hits"], "missed": ev["missed"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None, help="case 名（如 case_5 / case_6）")
    ap.add_argument("--all", action="store_true", help="跑全部真实 case")
    ap.add_argument("--repo-root", default="/Users/didi/IdeaProjects/sail2026", help="代码仓库根目录")
    args = ap.parse_args()

    cases = load_cases(args.case if not args.all else None)
    if not cases:
        print("❌ 未找到 case 文件（samples/case_*.json）")
        sys.exit(1)

    print(f"共 {len(cases)} 个真实业务 case: {[c['id'] for c in cases]}\n")
    results = []
    for c in cases:
        try:
            results.append(run_case(c, args.repo_root))
        except Exception as e:
            print(f"❌ {c['id']} 失败: {str(e)[:120]}")
    if len(results) > 1:
        print("\n" + "=" * 74)
        print("📈 汇总")
        print("=" * 74)
        for r in results:
            print(f"  {r['case']}: 一致性 {r['score']*100:.0f}% | 命中 {r['hits']} | 未提 {r['missed'] or '无'}")


if __name__ == "__main__":
    main()
