"""横向对比 Benchmark — 我们的方案 vs 开源工具。

同一批 case、同一个 LLM:
  - 每个压缩器产出压缩 prompt
  - 统计 token
  - LLM-as-judge 评分（与 baseline 全量对比）
  - 输出横向对比表
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.competitors import COMPRESSORS
from common.log_compressor import compress_log, format_compressed_log
from common.code_compressor import compress_code


def get_llm():
    from langchain_openai import ChatOpenAI
    key_path = os.path.expanduser("~/.hermes/deepseek_key")
    key = ""
    if os.path.exists(key_path):
        with open(key_path) as f:
            key = f.read().strip()
    return ChatOpenAI(model="deepseek-chat", temperature=0, max_tokens=1024,
                      api_key=key, base_url="https://api.deepseek.com/v1")


def make_prompt(case: dict, log_text: str, code_text: str) -> str:
    return f"## 问题\n{case['problem']}\n\n## 日志\n{log_text}\n\n## 代码\n{code_text}"


def run_ours(case: dict) -> dict:
    """我们的方案：日志模板化 + 代码 AST 压缩。"""
    compressed = compress_log(case["logs"])
    log_text = format_compressed_log(compressed)

    code_text = ""
    for cf in case["code_files"][:2]:
        try:
            src = Path(cf).read_text()
            code_text += compress_code(src, file_path=cf, keywords=case["keywords"]) + "\n"
        except Exception:
            pass

    prompt = make_prompt(case, log_text[:6000], code_text[:6000])
    return {"prompt": prompt, "input_chars": len(prompt), "method": "ours",
            "log_reduction": compressed["reduction_rate"]}


def run_full(case: dict) -> dict:
    """Baseline: 全量输入。"""
    log_text = "\n".join(case["logs"])
    code_text = ""
    for cf in case["code_files"][:2]:
        try:
            code_text += f"--- {cf} ---\n{Path(cf).read_text()}\n"
        except Exception:
            pass
    prompt = make_prompt(case, log_text, code_text)
    return {"prompt": prompt, "input_chars": len(prompt), "method": "baseline"}


def call_llm(llm, prompt: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    sys_prompt = (
        "你是资深 SRE，根据日志和代码定位问题根因。"
        "日志中 [xN] 表示模板出现 N 次，<*> 是变量占位符。\n"
        "输出格式:\n根因: <一句话>\n证据: <日志行/代码行引用>\n修复建议: <具体建议>"
    )
    try:
        result = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=prompt)])
        return result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


def judge(llm, answer: str, ground_truth: dict) -> float:
    """LLM-as-judge 评分 0~1。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    judge_prompt = f"""你是评分裁判。判断 AI 的根因分析是否正确。

真实根因: {ground_truth.get("root_cause", "")}
根因关键词: {", ".join(ground_truth.get("keywords", []))}

AI 的分析:
{answer[:1500]}

只输出 JSON: {{"score": 0.0~1.0}}"""
    try:
        result = llm.invoke([
            SystemMessage(content="你是根因分析评分裁判，只输出 JSON。"),
            HumanMessage(content=judge_prompt),
        ])
        content = result.content if hasattr(result, "content") else str(result)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return float(json.loads(content.strip()).get("score", 0.0))
    except Exception:
        return 0.0


def run_horizontal_benchmark(cases: list[dict], methods: list[str]) -> dict:
    """跑横向对比。"""
    llm = get_llm()
    # baseline 每个 case 只算一次（作为 token 基准）
    baseline_prompts = [run_full(c) for c in cases]
    baseline_tokens = [p["input_chars"] // 2 for p in baseline_prompts]

    # 我们的方案（只跑一次，不重复）
    ours_prompts = [run_ours(c) for c in cases]

    rows = []  # 每个 method × case 一行

    # 预生成所有压缩 prompt
    all_prompts = {"ours": ours_prompts, "baseline": baseline_prompts}
    for m in methods:
        if m in COMPRESSORS:
            all_prompts[m] = [COMPRESSORS[m](c["logs"], c["code_files"]) for c in cases]

    for i, case in enumerate(cases):
        print(f"\n[{i+1}/{len(cases)}] {case['id']}")
        for method in ["baseline", "ours"] + methods:
            if method not in all_prompts:
                continue
            prompt = all_prompts[method][i]["prompt"]
            tokens_in = len(prompt) // 2

            print(f"  ⏳ {method} ({tokens_in} tok in)...")
            answer = call_llm(llm, prompt)
            score = judge(llm, answer, case["ground_truth"])

            rows.append({
                "case_id": case["id"],
                "method": method,
                "tokens_in": tokens_in,
                "tokens_out": len(answer) // 2,
                "tokens_total": tokens_in + len(answer) // 2,
                "score": score,
                "reduction_vs_baseline": 1 - (tokens_in / max(baseline_tokens[i], 1)),
                "answer_preview": answer[:200],
            })

    # 汇总: 每个 method 的平均
    summary = {}
    for method in ["baseline", "ours"] + methods:
        if method not in all_prompts:
            continue
        mrows = [r for r in rows if r["method"] == method]
        if not mrows:
            continue
        avg_tokens = sum(r["tokens_total"] for r in mrows) / len(mrows)
        avg_score = sum(r["score"] for r in mrows) / len(mrows)
        avg_reduction = sum(r["reduction_vs_baseline"] for r in mrows) / len(mrows)
        summary[method] = {
            "avg_tokens": avg_tokens,
            "avg_score": avg_score,
            "avg_reduction": avg_reduction,
            "per_case": mrows,
        }

    return {"summary": summary, "rows": rows}


def print_horizontal_report(result: dict):
    s = result["summary"]
    methods = list(s.keys())

    print("\n" + "═" * 78)
    print("横向对比 Benchmark 报告")
    print("═" * 78)
    print(f"{'方法':<14} {'Token/总':<10} {'减少%':<8} {'准确率':<8} {'评分≥baseline'}")
    print("-" * 78)

    baseline_score = s.get("baseline", {}).get("avg_score", 0)
    baseline_tokens = s.get("baseline", {}).get("avg_tokens", 0)

    for m in methods:
        d = s[m]
        name = f"{m} (ours)" if m == "ours" else m
        marker = ""
        if m != "baseline":
            marker = "✅" if (d["avg_reduction"] >= 0.5 and d["avg_score"] >= baseline_score - 0.05) else "❌"
        print(f"{name:<14} {d['avg_tokens']:<10.0f} {d['avg_reduction']*100:<8.1f} "
              f"{d['avg_score']*100:<8.1f} {marker}")
    print("-" * 78)
    print("✅ = token 减少≥50% 且 准确率不降 (vs baseline)")
