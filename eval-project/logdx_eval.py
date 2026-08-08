#!/usr/bin/env python3
"""LogDx-CI 评测：信号召回率 + token 压缩率（静态确定性评分）。

用法: python3 logdx_eval.py --logdx-root /path/to/LogDx [--ours-import PATH]
"""
import argparse, glob, os, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdx-root", required=True, help="LogDx 仓库路径（含 cases/ 与 huggingface/metadata/）")
    ap.add_argument("--ours-root", default=os.path.join(os.path.dirname(__file__), ".."),
                    help="rootcause-agent 产品代码路径")
    ap.add_argument("--key-templates", type=int, default=1000, help="关键模板上限")
    ap.add_argument("--tail-window", type=int, default=120, help="尾部保底行数")
    ap.add_argument("--noise-limit", type=int, default=100, help="噪声模板输出上限")
    ap.add_argument("--bm25-weak", type=int, default=0,
                    help=">0 时启用 BM25 弱信号裁剪（最优档 100：召回不变，压缩 +0.3%）")
    args = ap.parse_args()

    os.environ["LOGDX_CI_ROOT"] = args.logdx_root
    sys.path.insert(0, os.path.abspath(args.ours_root))
    from common.log_compressor import compress_log, format_compressed_log, _signal_level
    from common.relevance import BM25Scorer
    import logdx_ci

    _QUERY = "error exception failed failure fatal timeout panic assertion not found exit code denied rejected"
    _scorer = BM25Scorer()

    def reducer(raw_log: str) -> str:
        c = compress_log(raw_log.split("\n"),
                         max_key_templates=args.key_templates,
                         tail_window=args.tail_window)
        if args.bm25_weak:
            kts = c["key_templates"]
            strong = [kt for kt in kts if _signal_level(kt[0]) == 0]
            weak = [kt for kt in kts if _signal_level(kt[0]) != 0]
            if weak:
                scores = _scorer.score_batch([t for t, n, lv in weak], _QUERY)
                ranked = sorted(zip(weak, scores), key=lambda x: -x[1]["score"])
                weak = [kt for kt, s in ranked][:args.bm25_weak]
            c["key_templates"] = strong + weak
        return format_compressed_log(c, noise_limit=args.noise_limit)

    res = logdx_ci.evaluate(reducer=reducer)

    raw_logs = (sorted(glob.glob(f"{args.logdx_root}/cases/*/*/raw.log"))
                + sorted(glob.glob(f"{args.logdx_root}/cases/*/*/*/raw.log")))
    total_raw = sum(len(open(f, encoding="utf-8", errors="ignore").read()) // 2 for f in raw_logs)
    total_out = sum(len(reducer(open(f, encoding="utf-8", errors="ignore").read())) // 2 for f in raw_logs)

    print(f"=== LogDx-CI ({len(raw_logs)} case) ===")
    print(f"信号召回率: {res.score:.4f}")
    print(f"token 压缩率: {1 - total_out/total_raw:.2%} ({total_out:,} / {total_raw:,})")
    return 0 if res.score >= 0.9 else 1

if __name__ == "__main__":
    sys.exit(main())
