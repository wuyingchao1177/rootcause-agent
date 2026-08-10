#!/usr/bin/env python3
"""A/B：代码压缩档位对真实业务 case（case_5/6/7）压缩率与准确率的影响。

档位:
  A 当前默认: sig40/key60/ctx2/import25
  B 激进:     sig15/key25/ctx1/import12
  C 超激进:   sig8/key12/ctx0/import8
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from benchmark.horizontal import make_prompt, judge
from common.code_compressor import compress_code
from common.log_compressor import compress_log, format_compressed_log
from locator.agent import get_llm

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(BASE, "samples")

TIERS = {
    "A": dict(sig_limit=40, key_line_limit=60, context_lines=2, import_limit=25),
    "B": dict(sig_limit=15, key_line_limit=25, context_lines=1, import_limit=12),
    "C": dict(sig_limit=8, key_line_limit=12, context_lines=0, import_limit=8),
}
CASES = ["case_5_order_nature", "case_6_cheat_field", "case_7_order_status"]


def ours_prompt(case, tier_params):
    """run_ours 的压缩逻辑 + 指定档位。"""
    compressed = compress_log(case["logs"])
    log_text = format_compressed_log(compressed)
    code_text = ""
    for name, src in case["code_files"].items():
        code_text += compress_code(src, file_path=name, keywords=case["keywords"], **tier_params) + "\n"
    return make_prompt(case, log_text[:12000], code_text[:30000])


def main():
    llm = get_llm()
    rows = []
    for cid in CASES:
        case = json.load(open(os.path.join(SAMPLES, f"{cid}.json"), encoding="utf-8"))
        baseline = make_prompt(case, "\n".join(case["logs"]),
                               "\n".join(f"--- {k} ---\n{v}" for k, v in case["code_files"].items()))
        base_tok = len(baseline) // 2
        for tier, params in TIERS.items():
            prompt = ours_prompt(case, params)
            tok = len(prompt) // 2
            from langchain_core.messages import HumanMessage, SystemMessage
            sys_prompt = ("你是资深 SRE + 后端架构师，根据压缩后的日志模板和关键代码行定位问题根因。"
                          "要求：1. 输出结构：① 根因链② 关键证据③ 定位置信度④ 修复建议 "
                          "2. 每条结论必须引用具体证据 3. 若结论依赖外部配置/规则，标注已/未直接验证与人工核实方式")
            answer = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=prompt)])
            score = judge(llm, answer.content, case["ground_truth"])
            rows.append({"case": cid, "tier": tier, "tokens": tok,
                         "compression": 1 - tok / max(base_tok, 1), "score": score})
            print(f"{cid} [{tier}] tok={tok:,} 压缩率={1-tok/base_tok:.1%} 准确率={score*100:.0f}%", flush=True)
    print("\n=== 汇总 ===")
    for tier in TIERS:
        r = [x for x in rows if x["tier"] == tier]
        avg_c = sum(x["compression"] for x in r) / len(r)
        avg_s = sum(x["score"] for x in r) / len(r)
        print(f"档位 {tier}: 平均压缩率 {avg_c:.1%} | 平均准确率 {avg_s*100:.0f}%")
    out = os.path.join(BASE, "results", "code_tier_ab.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存: {out}")


if __name__ == "__main__":
    main()
