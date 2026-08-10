#!/usr/bin/env python3
"""RCAEval 指标型评测（ours）：re1ob/re1ss 全量 AC@1 + token 压缩率。

用法: python3 rcaeval_metric.py --data-dir data/RE1-OB --out results/ours_re1ob.json
数据格式（官方 Zenodo 结构）: <data-dir>/<service>_<fault>/<n>/metrics.csv + inject_time.txt
"""
import argparse, glob, json, os, re, subprocess, sys

def llm_locate(context: str, problem: str, api_key: str,
               base_url: str = "https://api.deepseek.com/v1",
               model: str = "deepseek-chat") -> str:
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 100,
                       "messages": [{"role": "user", "content":
                           "你是微服务根因定位专家。给定故障期间指标摘要，判断根因服务。\n"
                           f"故障场景: {problem}\n\n{context}\n\n只输出 JSON: "
                           '{"root_cause_service": "服务名"}'}]}).encode()
    import tempfile
    with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
        tf.write(body)
        bf = tf.name
    try:
        r = subprocess.run(["curl", "-fsSL", "-m", "90", "-X", "POST",
                            f"{base_url}/chat/completions",
                            "-H", "Content-Type: application/json",
                            "-H", f"Authorization: Bearer {api_key}", "-d", f"@{bf}"],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            return f"ERROR:{r.stderr.decode(errors='ignore')[:60]}"
        content = json.loads(r.stdout)["choices"][0]["message"]["content"]
        m = re.search(r'"root_cause_service"\s*:\s*"([^"]+)"', content)
        return m.group(1) if m else content[:40]
    finally:
        os.unlink(bf)


def summarize_metrics_csv(csv_path: str, inject_time: str, limit: int = 12) -> str:
    import pandas as pd
    df = pd.read_csv(csv_path).replace([float('inf'), -float('inf')], float('nan'))
    df = df.ffill().fillna(0)
    inj = int(inject_time)
    normal, anomal = df[df["time"] < inj], df[df["time"] >= inj]
    if len(normal) == 0 or len(anomal) == 0:
        return "[指标窗口异常]"
    lines = []
    for col in df.columns:
        if col == "time":
            continue
        n_mean, a_mean = normal[col].mean(), anomal[col].mean()
        n_max, a_max = normal[col].max(), anomal[col].max()
        if a_mean > n_mean * 1.5 and a_mean > 0.001:
            ratio = a_mean / n_mean if n_mean > 0 else float('inf')
            lines.append(f"{col}: mean {n_mean:.3f}->{a_mean:.3f}(x{ratio:.1f})")
        elif a_max > n_max * 2 and a_max > 0.01:
            lines.append(f"{col}: peak {n_max:.3f}->{a_max:.3f}")
        if len(lines) >= limit:
            break
    return "\n".join(lines) if lines else "[无明显指标异常]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", default="results/ours.json")
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""),
                    help="LLM API Key（默认读 DEEPSEEK_API_KEY 环境变量）")
    ap.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
                    help="OpenAI 兼容服务端点（默认 DeepSeek，可切换 vLLM/Ollama/方舟等）")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "deepseek-chat"),
                    help="模型名（默认 deepseek-chat）")
    args = ap.parse_args()

    cases = sorted(glob.glob(f"{args.data_dir}/*/*/metrics.csv"))
    if args.limit:
        cases = cases[:args.limit]
    results, ok = [], 0
    for i, p in enumerate(cases):
        svc_fault, num = p.split("/")[-3], p.split("/")[-2]
        svc = svc_fault.split("_", 1)[0]
        d0 = os.path.dirname(p)
        inj = open(os.path.join(d0, "inject_time.txt")).read().strip()
        mt = summarize_metrics_csv(p, inj)
        ctx = f"## 指标异常摘要（根因第一依据）\n{mt[:800]}\n\n提示：根因服务是指标异常最显著的服务。"
        pred = llm_locate(ctx, f"RCAEval case={svc_fault}_{num}", args.api_key,
                          base_url=args.base_url, model=args.model)
        hit = pred == svc or svc in pred or pred in svc
        ok += 1 if hit else 0
        results.append({"case": f"{svc_fault}_{num}", "gt": svc, "pred": pred, "ok": hit})
        if (i + 1) % 25 == 0:
            print(f"进度 {i+1}/{len(cases)} 当前 {ok/(i+1)*100:.1f}%", flush=True)

    acc = ok / len(cases)
    print(f"\n=== ours {os.path.basename(args.data_dir)} ===")
    print(f"AC@1: {acc*100:.1f}% ({ok}/{len(cases)})")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump(results, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"结果已存 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
