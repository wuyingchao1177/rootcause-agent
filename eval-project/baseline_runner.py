#!/usr/bin/env python3
"""RCAEval 官方 baseline 跑批（baro/nsigma/circa/dummy）+ 计分。

用法:
  python3 baseline_runner.py --rcaeval-dir /path/to/RCAEval --dataset re1-ob [--methods baro,nsigma]
  python3 score.py --result-dir /path/to/RCAEval/output/results
"""
import argparse, glob, json, os, shutil, subprocess, sys


def evaluate_results(result_dir: str) -> dict | None:
    files = sorted(glob.glob(os.path.join(result_dir, "*.json")))
    ac1 = ac3 = avg5 = 0
    n = 0
    for f in files:
        name = os.path.basename(f).replace(".json", "")
        root_svc = name.split("_")[0]
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
    ap.add_argument("--rcaeval-dir", required=True)
    ap.add_argument("--dataset", required=True, help="re1-ob/re1-ss/re2-ob/re2-ss 等")
    ap.add_argument("--methods", default="dummy,baro,nsigma,circa",
                    help="逗号分隔的方法列表")
    ap.add_argument("--out", default="results/baselines.json")
    args = ap.parse_args()

    result_dir = os.path.join(args.rcaeval_dir, "output/results")
    out = {}
    for m in args.methods.split(","):
        shutil.rmtree(result_dir, ignore_errors=True)
        r = subprocess.run([sys.executable, "main.py", "--method", m, "--dataset", args.dataset],
                           capture_output=True, text=True, cwd=args.rcaeval_dir, timeout=1800)
        sc = evaluate_results(result_dir)
        if sc:
            out[m] = sc
            print(f"{m:<8} AC@1 {sc['AC@1']:.3f}  AC@3 {sc['AC@3']:.3f}  Avg@5 {sc['Avg@5']:.3f}  (n={sc['n']})")
        else:
            out[m] = {"error": r.stderr[-150:]}
            print(f"{m:<8} 失败: {r.stderr[-100:]}")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump(out, open(args.out, "w"), indent=1)
    print(f"结果已存 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
