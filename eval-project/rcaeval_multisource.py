#!/usr/bin/env python3
"""RCAEval 多源评测（ours）：re2ob/re2ss/RE3（指标+日志+追踪）。

用法: python3 rcaeval_multisource.py --data-dir data/RE2-OB --out results/ours_re2ob.json
数据格式: <data-dir>/<service>_<fault>/<n>/{metrics.csv, logs.csv|logs.parquet, traces.csv|traces.parquet}
"""
import argparse, glob, json, os, re, sys

LOW_VALUE_RE = re.compile(
    r'(?i)(I/O exception|RetryExec|\.sock|DockerSpawner|docker|ProcessingException|'
    r'AFUNIXSocket|Connection refused|ConnectException|execchain|pool-\d|p-nio|'
    r'tomcat-embed|ErrorReportValve)')
SERVICE_RE = re.compile(r'\[([a-zA-Z0-9_.-]+)\]')


def summarize_traces(df, inj_sec: int) -> str:
    try:
        df = df[df["startTimeMillis"] >= float(inj_sec) * 1000]
    except Exception:
        pass
    lines = []
    if "statusCode" in df.columns:
        err = df[df["statusCode"].fillna(0).astype(int) != 0]
        if len(err):
            by_svc = err.groupby("serviceName").size().sort_values(ascending=False)
            lines.append("错误span按服务: " + ", ".join(f"{s}:{n}" for s, n in by_svc.head(6).items()))
            op = err["operationName"].dropna().value_counts().head(4)
            if len(op):
                lines.append("高频错误操作: " + ", ".join(f"{o}({n})" for o, n in op.items()))
    if "duration" in df.columns:
        d = df.dropna(subset=["duration"])
        if len(d):
            p99 = d.groupby("serviceName")["duration"].quantile(0.99).sort_values(ascending=False)
            lines.append("延迟P99前5: " + ", ".join(f"{s}:{v:.0f}ms" for s, v in p99.head(5).items()))
    return "\n".join(lines)


def ours_log_view(log_lines) -> str:
    from common.log_compressor import compress_log, service_error_distribution
    from collections import defaultdict
    c = compress_log(log_lines, max_key_templates=500, tail_window=120)
    kts = c["key_templates"]
    svc_view = service_error_distribution(kts)
    high_value = [kt for kt in kts if not LOW_VALUE_RE.search(kt[0])]
    strong = high_value[:40] if high_value else kts[:40]
    strong_view = "\n".join(f"[x{n}] {t[:120]}" for t, n, lv in strong)
    tail_view = "\n".join(c.get("tail_lines", [])[:40])
    return (f"[服务级错误分布]\n{svc_view}\n\n[业务错误日志]\n{strong_view}\n\n[日志尾部]\n{tail_view}")


def summarize_metrics(csv_path: str, inject_time: str, limit: int = 12) -> str:
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
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from rcaeval_metric import llm_locate  # 复用 LLM 调用
    import pandas as pd

    # RE1-OB/RE2 用 metrics.csv；RE1-SS 用 data.csv（Sock Shop 数据集结构差异）
    cases = sorted(glob.glob(f"{args.data_dir}/*/*/metrics.csv")) or \
            sorted(glob.glob(f"{args.data_dir}/*/*/data.csv"))
    if args.limit:
        cases = cases[:args.limit]
    results, ok = [], 0
    for i, p in enumerate(cases):
        svc_fault, num = p.split("/")[-3], p.split("/")[-2]
        svc = svc_fault.split("_", 1)[0]
        d0 = os.path.dirname(p)
        inj = open(os.path.join(d0, "inject_time.txt")).read().strip()
        # 日志（csv 或 parquet；RE1 指标型无日志文件 → 优雅跳过）
        logs_path = os.path.join(d0, "logs.csv")
        if os.path.exists(logs_path):
            logs = pd.read_csv(logs_path, low_memory=False)
            log_lines = [f"[{r['container_name']}] {str(r['message'])}" for _, r in logs.iterrows()][:80000]
        else:
            logs_path = os.path.join(d0, "logs.parquet")
            if os.path.exists(logs_path):
                logs = pd.read_parquet(logs_path)
                log_lines = [f"[{r['container_name']}] {str(r['message'])}" for _, r in logs.iterrows()]
            else:
                log_lines = []
        log_view = ours_log_view(log_lines) if log_lines else ""
        # 追踪
        trace_text = ""
        for tp in ("traces.csv", "traces.parquet"):
            if os.path.exists(os.path.join(d0, tp)):
                tr = pd.read_csv(os.path.join(d0, tp)) if tp.endswith("csv") else pd.read_parquet(os.path.join(d0, tp))
                trace_text = summarize_traces(tr, inj)
                break
        # 指标
        metric_text = summarize_metrics(p, inj)
        ctx = (f"## 指标异常摘要（根因第一依据）\n{metric_text[:800]}\n\n"
               f"## 追踪错误摘要\n{trace_text[:600]}\n\n"
               f"## 日志摘要\n{log_view[:2500]}\n\n"
               "提示：日志大量错误可能是受害方连锁症状，根因以指标异常为第一依据。")
        pred = llm_locate(ctx, f"RCAEval case={svc_fault}_{num}", args.api_key)
        hit = pred == svc or svc in pred or pred in svc
        ok += 1 if hit else 0
        results.append({"case": f"{svc_fault}_{num}", "gt": svc, "pred": pred, "ok": hit})
        if (i + 1) % 15 == 0:
            print(f"进度 {i+1}/{len(cases)} 当前 {ok/(i+1)*100:.1f}%", flush=True)

    print(f"\n=== ours {os.path.basename(args.data_dir)} ===")
    print(f"AC@1: {ok/len(cases)*100:.1f}% ({ok}/{len(cases)})")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump(results, open(args.out, "w"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
