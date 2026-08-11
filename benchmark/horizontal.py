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
    """创建 LLM 实例（OpenAI 兼容）。

    配置（环境变量优先，兼容旧的文件配置）：
      DEEPSEEK_API_KEY / LLM_API_KEY — API Key（默认兼容 ~/.hermes/deepseek_key 文件）
      LLM_BASE_URL — 服务端点（默认 https://api.deepseek.com/v1，可切换任意 OpenAI 兼容服务）
      LLM_MODEL    — 模型名（默认 deepseek-chat）
    """
    from langchain_openai import ChatOpenAI
    key = os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
    if not key:
        key_path = os.path.expanduser("~/.hermes/deepseek_key")
        if os.path.exists(key_path):
            with open(key_path) as f:
                key = f.read().strip()
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    return ChatOpenAI(model=model, temperature=0, max_tokens=1024,
                      api_key=key, base_url=base_url)


def make_prompt(case: dict, log_text: str, code_text: str) -> str:
    return (f"## 问题\n{case['problem']}\n\n"
            f"## 日志\n{log_text}\n\n"
            f"## 代码\n{code_text}\n\n"
            f"请给出根因分析（结构）：\n"
            f"① 根因链（触发条件→直接原因→根本原因）② 关键证据（引用具体日志行/代码行）"
            f"③ 定位置信度（高/中/低，推断与有据结论分开）④ 修复建议\n"
            f"每条结论必须引用具体证据，禁止无证据断言；若结论依赖外部配置/规则"
            f"（QLE 表达式、策略配置等），标注哪些已直接验证、哪些未直接验证、如何人工核实")


def _cf_sources(code_files) -> list[tuple[str, str]]:
    """兼容 code_files 两种格式（dict: {文件名: 内容} / list: [路径]）。"""
    if isinstance(code_files, dict):
        return list(code_files.items())
    out = []
    for p in code_files:
        try:
            out.append((Path(p).name, Path(p).read_text()))
        except Exception:
            pass
    return out


def run_ours(case: dict) -> dict:
    """我们的方案：日志模板化 + 代码 AST 压缩。"""
    compressed = compress_log(case["logs"])
    log_text = format_compressed_log(compressed)

    code_text = ""
    # dict 格式（已压缩片段）传全部；list 格式（原始路径）限前 2 防超
    sources = _cf_sources(case["code_files"])
    if isinstance(case["code_files"], dict):
        sources = sources[:6]
    else:
        sources = sources[:2]
    for name, src in sources:
        try:
            code_text += compress_code(src, file_path=name, keywords=case["keywords"]) + "\n"
        except Exception:
            pass

    prompt = make_prompt(case, log_text, code_text)
    return {"prompt": prompt, "input_chars": len(prompt), "method": "ours",
            "log_reduction": compressed["reduction_rate"]}


def run_full(case: dict) -> dict:
    """Baseline: 全量输入。"""
    log_text = "\n".join(case["logs"])
    code_text = ""
    sources = _cf_sources(case["code_files"])
    if isinstance(case["code_files"], dict):
        sources = sources[:6]
    else:
        sources = sources[:2]
    for name, src in sources:
        code_text += f"--- {name} ---\n{src}\n"
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
    """LLM-as-judge 评分 0~1（rubric 锚点 + 截断 6000）。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    judge_prompt = f"""你是评分裁判。判断 AI 的根因分析是否正确。

真实根因: {ground_truth.get("root_cause", "")}
根因关键词: {", ".join(ground_truth.get("keywords", []))}

AI 的分析:
{answer[:6000]}

评分规则（严格）:
- 1.0: 机制/结论与真实根因一致（关键证据链命中）
- 0.5: 方向部分正确（机制有出入或缺关键环节）
- 0.0: 无关/错误结论

只输出 JSON: {{"score": 0.0 或 0.5 或 1.0}}"""
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

            # 3 次生成 × 判分 → 中位数（抑制 LLM 生成非确定性导致的判分波动）
            print(f"  ⏳ {method} ({tokens_in} tok in) ×3...")
            scores, last_answer = [], ""
            for _round in range(3):
                answer = call_llm(llm, prompt)
                last_answer = answer
                scores.append(judge(llm, answer, case["ground_truth"]))
            scores.sort()
            score = scores[1]  # 中位数

            rows.append({
                "case_id": case["id"],
                "method": method,
                "tokens_in": tokens_in,
                "tokens_out": len(last_answer) // 2,
                "tokens_total": tokens_in + len(last_answer) // 2,
                "score": score,
                "scores": scores,
                "reduction_vs_baseline": 1 - (tokens_in / max(baseline_tokens[i], 1)),
                "answer_preview": last_answer[:200],
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
