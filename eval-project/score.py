#!/usr/bin/env python3
"""RCAEval 结果计分：AC@1 / AC@3 / Avg@5（官方 Evaluator 口径的轻量实现）。

用法: python3 score.py --result-dir /path/to/RCAEval/output/results
结果文件格式: <service>_<metric>_<case>.json 含 {"0": [rank1, rank2, ...]}
"""
import argparse, glob, json, os, sys


def evaluate_results(result_dir: str) -> dict | None:
    files = sorted(glob.glob(os.path.join(result_dir, "*.json")))
    ac1 = ac3 = avg5 = 0
    n = 0
    for f in files:
        name = os.path.basename(f).replace(".json", "")
        root_svc = name.split("_")[0]  # 文件名前缀 = 根因服务
        d = json.load(open(f))
        if "error" in d or "0" not in d:
            continue
        ranks = d["0"]
        n += 1
        r0 = ranks[0].split("_")[0] if ranks else ""
        if r0 == root_svc:
            ac1 += 1
        top3 = [r.split("_")[0] for r in ranks[:3]]
        if root_svc in top3:
            ac3 += 1
        top5 = [r.split("_")[0] for r in ranks[:5]]
        if root_svc in top5:
            avg5 += 1.0 / (top5.index(root_svc) + 1)
    if n == 0:
        return None
    return {"n": n, "AC@1": ac1 / n, "AC@3": ac3 / n, "Avg@5": avg5 / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-dir", required=True)
    args = ap.parse_args()
    sc = evaluate_results(args.result_dir)
    if not sc:
        print("无有效结果文件")
        return 1
    print(f"case 数: {sc['n']}")
    print(f"AC@1:  {sc['AC@1']:.3f}")
    print(f"AC@3:  {sc['AC@3']:.3f}")
    print(f"Avg@5: {sc['Avg@5']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
