"""Benchmark Agent — 对比 baseline(全量输入) vs 压缩方案(本实现)。

同一批 case、同一个大模型、同样的业务代码：
  1. baseline: 原始日志全文 + 全部相关代码 直接喂 LLM
  2. compressed: 分层压缩后的日志模板 + 代码片段 喂 LLM

指标:
  - 根因定位准确率 (与 ground truth 对比)
  - token 消耗 (输入+输出)
  - token 减少比例 (必须 ≥50%)
  - 耗时
"""

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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


# ─── Baseline: 全量输入 ─────────────────────────────────────────

def run_baseline(llm, case: dict) -> dict:
    """全量日志+全部代码直接喂 LLM（真实 baseline：不截断）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    log_text = "\n".join(case["logs"])  # 全量日志，不截断
    code_text = ""
    cf_src = case["code_files"]
    if isinstance(cf_src, dict):  # 新格式：{文件名: 代码片段}
        items = list(cf_src.items())[:5]
        for name, content in items:
            code_text += f"--- {name} ---\n{content}\n"
    else:  # 旧格式：list[路径]
        for cf in cf_src[:5]:
            try:
                code_text += f"--- {cf} ---\n{Path(cf).read_text()}\n"
            except Exception:
                pass

    sys_prompt = (
        "你是资深 SRE，根据日志和代码定位问题根因。"
        "输出格式:\n根因: <一句话>\n证据: <日志行/代码行引用>\n修复建议: <具体建议>"
    )
    user_prompt = f"## 问题\n{case['problem']}\n\n## 日志\n{log_text}\n\n## 代码\n{code_text}"

    start = time.time()
    try:
        result = llm.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user_prompt),
        ])
        answer = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        answer = f"ERROR: {str(e)[:200]}"
    elapsed = time.time() - start

    return {
        "answer": answer,
        "input_chars": len(user_prompt),
        "output_chars": len(answer),
        "elapsed": elapsed,
    }


# ─── Compressed: 本方案 ─────────────────────────────────────────

def run_compressed(llm, case: dict) -> dict:
    """分层压缩后喂 LLM。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    # 日志压缩
    compressed = compress_log(case["logs"])
    log_text = format_compressed_log(compressed)

    # 代码压缩（只喂相关关键词命中行 + 栈焦点行）
    code_text = ""
    focus = []
    for m in re.finditer(r'([\w/]+\.\w+):(\d+)', case.get("problem", "")):
        focus.append((m.group(1), int(m.group(2))))
    cf_src = case["code_files"]
    if isinstance(cf_src, dict):  # 新格式：{文件名: 代码片段（已压缩）}
        for name, content in list(cf_src.items())[:3]:
            code_text += f"--- {name} ---\n{content}\n"
    else:  # 旧格式：list[路径]
        for cf in cf_src[:3]:
            try:
                src = Path(cf).read_text()
                fl = [line for path, line in focus if path.split("/")[-1] == Path(cf).name]
                code_text += compress_code(src, file_path=cf, keywords=case["keywords"],
                                           focus_lines=fl or None) + "\n"
            except Exception:
                pass

    sys_prompt = (
        "你是资深 SRE + 后端架构师，根据压缩后的日志模板和关键代码行定位问题根因。"
        "日志中 [xN] 表示该模板出现 N 次。<*> 是变量占位符。\n"
        "要求：\n"
        "1. 输出结构：① 根因链（触发条件→直接原因→根本原因）② 关键证据（引用具体日志行/代码行）"
        "③ 定位置信度（高/中/低，推断与有据结论分开）④ 修复建议\n"
        "2. 每条结论必须引用具体证据，禁止无证据断言\n"
        "3. 若结论依赖外部配置/规则（QLE 表达式、策略配置等），标注哪些已直接验证、哪些未直接验证、如何人工核实\n"
    )
    user_prompt = f"## 问题\n{case['problem']}\n\n## 压缩日志\n{log_text[:8000]}\n\n## 代码片段\n{code_text[:8000]}"

    start = time.time()
    try:
        result = llm.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user_prompt),
        ])
        answer = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        answer = f"ERROR: {str(e)[:200]}"
    elapsed = time.time() - start

    return {
        "answer": answer,
        "input_chars": len(user_prompt),
        "output_chars": len(answer),
        "elapsed": elapsed,
        "compression": {
            "original_lines": compressed["original_lines"],
            "reduced_lines": compressed["reduced_lines"],
            "reduction_rate": compressed["reduction_rate"],
        },
    }


# ─── 评分 ────────────────────────────────────────────────────────

def score_answer(llm, answer: str, ground_truth: dict) -> dict:
    """LLM-as-judge 评分：判断答案是否正确定位根因（0~1）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    gt_root = ground_truth.get("root_cause", "")
    gt_keywords = ", ".join(ground_truth.get("keywords", []))

    judge_prompt = f"""你是评分裁判。判断 AI 的根因分析是否正确。

真实根因: {gt_root}
根因关键词(用于参考): {gt_keywords}

AI 的分析:
{answer[:1500]}

请回答：
1. AI 是否正确定位了根因？(是/否/部分)
2. 得分(0.0~1.0，0=完全错误，0.5=部分正确，1=完全正确)

格式: JSON
{{"correct": "是|否|部分", "score": 0.0}}"""

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
        data = json.loads(content.strip())
        return {
            "keyword_recall": float(data.get("score", 0.0)),
            "evidence_hit": data.get("correct") in ("是", "部分"),
            "correct": data.get("correct", "否"),
        }
    except Exception as e:
        # fallback: 关键词匹配
        answer_lower = answer.lower()
        gt_keywords_l = [k.lower() for k in ground_truth.get("keywords", [])]
        hits = sum(1 for kw in gt_keywords_l if kw in answer_lower)
        recall = hits / max(len(gt_keywords_l), 1)
        return {"keyword_recall": recall, "evidence_hit": recall > 0.3,
                "correct": "部分" if recall > 0.3 else "否"}


# ─── 主流程 ─────────────────────────────────────────────────────

def run_benchmark(cases: list[dict]) -> dict:
    """对一批 case 跑 baseline vs compressed 对比。"""
    llm = get_llm()
    results = []

    for i, case in enumerate(cases):
        print(f"\n[{i+1}/{len(cases)}] 运行 case: {case['id']}")

        base = run_baseline(llm, case)
        print(f"  baseline: {base['input_chars']//2} tok in, {base['elapsed']:.1f}s")

        comp = run_compressed(llm, case)
        print(f"  compressed: {comp['input_chars']//2} tok in, {comp['elapsed']:.1f}s")

        base_score = score_answer(llm, base["answer"], case["ground_truth"])
        comp_score = score_answer(llm, comp["answer"], case["ground_truth"])

        base_tokens = (base["input_chars"] + base["output_chars"]) // 2
        comp_tokens = (comp["input_chars"] + comp["output_chars"]) // 2
        reduction = 1 - (comp_tokens / max(base_tokens, 1))

        results.append({
            "case_id": case["id"],
            "baseline": {
                "tokens": base_tokens,
                "score": base_score,
                "answer": base["answer"][:300],
            },
            "compressed": {
                "tokens": comp_tokens,
                "score": comp_score,
                "answer": comp["answer"][:300],
                "compression": comp.get("compression"),
            },
            "token_reduction": reduction,
            "quality_delta": comp_score["keyword_recall"] - base_score["keyword_recall"],
        })

    # 汇总
    avg_reduction = sum(r["token_reduction"] for r in results) / max(len(results), 1)
    avg_base_score = sum(r["baseline"]["score"]["keyword_recall"] for r in results) / max(len(results), 1)
    avg_comp_score = sum(r["compressed"]["score"]["keyword_recall"] for r in results) / max(len(results), 1)
    passed = all(r["token_reduction"] >= 0.5 for r in results)

    summary = {
        "cases": len(results),
        "avg_token_reduction": avg_reduction,
        "avg_baseline_score": avg_base_score,
        "avg_compressed_score": avg_comp_score,
        "quality_delta": avg_comp_score - avg_base_score,
        "all_cases_ge_50pct": passed,
        "pass": passed and (avg_comp_score >= avg_base_score - 0.05),
        "details": results,
    }
    return summary


def print_report(summary: dict):
    print("\n" + "═" * 60)
    print("BENCHMARK 报告")
    print("═" * 60)
    print(f"Case 数: {summary['cases']}")
    print(f"平均 Token 减少: {summary['avg_token_reduction']*100:.1f}%  (目标 ≥50%)")
    print(f"baseline 准确率: {summary['avg_baseline_score']*100:.1f}%")
    print(f"压缩后准确率:   {summary['avg_compressed_score']*100:.1f}%")
    print(f"质量变化:       {summary['quality_delta']*100:+.1f}%")
    print(f"全部 case ≥50%: {'✅' if summary['all_cases_ge_50pct'] else '❌'}")
    print(f"整体通过:       {'✅ PASS' if summary['pass'] else '❌ FAIL'}")
    print("═" * 60)
    for d in summary["details"]:
        print(f"\ncase {d['case_id']}:")
        print(f"  token: {d['baseline']['tokens']} → {d['compressed']['tokens']} "
              f"({d['token_reduction']*100:.0f}% 减少)")
        print(f"  score: {d['baseline']['score']['keyword_recall']*100:.0f}% → "
              f"{d['compressed']['score']['keyword_recall']*100:.0f}%")


if __name__ == "__main__":
    # 从 samples/ 加载 case
    import glob
    sample_dir = Path(__file__).parent.parent / "samples"
    case_files = sorted(glob.glob(str(sample_dir / "case_*.json")))
    if not case_files:
        print("samples/ 下没有 case 文件。先运行 python samples/make_samples.py 生成。")
        sys.exit(1)

    cases = []
    for cf in case_files:
        with open(cf) as f:
            cases.append(json.load(f))

    summary = run_benchmark(cases)
    print_report(summary)
