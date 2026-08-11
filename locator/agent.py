"""问题定位 Agent — 分层递进定位 + Token 压缩。

借鉴: Holmes (Retrieve-Explore-Reason) + Microsoft RCA Agent + Headroom 压缩。

分层策略（每层只喂必要上下文，token 随深入逐步增加）:
  L0 问题解析: 从告警/异常消息提取关键词（几行）
  L1 日志压缩: 压缩日志 → 定位异常发生的时间/服务/线程（几十行）
  L2 代码定位: 从异常栈定位代码文件 → 压缩代码片段（几十行）
  L3 根因分析: 结合运行时数据 + 代码逻辑 → LLM 输出根因链（最终分析）

Token 节省: 相比把全量日志+全部代码直接喂 LLM，预计减少 70-95%
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common.log_compressor import compress_log, format_compressed_log, build_analysis_view
from common.code_compressor import compress_code

# 通用证据词（规则/配置/表达式/映射类 —— 项目无关，可配置扩展；
# 具体字段名（如 assign_type）从问题文本提取，不写死）
_EVIDENCE_KEYWORDS = (
    "rule", "expression", "config", "pattern", "apollo", "enum",
    "if(", "return ", "策略", "规则", "配置", "表达式", "枚举", "映射",
)


def retrieve_evidence(log_lines: list[str], keywords: list[str],
                      max_lines: int = 40, ctx_lines: int = 1) -> list[str]:
    """检索聚焦证据：按关键词从日志行提取相关行（含前后 1 行上下文）。

    借鉴 field-source-tracing 的检索聚焦思想（分步检索，每步只看相关小块，
    无长输入注意力稀释）。关键词 = 问题提取的字段名（不写死）+ 通用证据词。
    max_lines 是检索结果的输出上限（聚焦产物，非对原文的硬截断）。
    """
    keys = [k.lower() for k in keywords if k] + list(_EVIDENCE_KEYWORDS)
    idxs = set()
    for i, line in enumerate(log_lines):
        low = line.lower()
        if any(k.lower() in low for k in keys):
            for j in range(max(0, i - ctx_lines), min(len(log_lines), i + ctx_lines + 1)):
                idxs.add(j)
    hits = [f"[L{i}] {log_lines[i]}" for i in sorted(idxs)]
    # 同类行去重（前 120 字符相同视为同模板，只留 1 条）
    seen_t, dedup = set(), []
    for h in hits:
        t = h[:120]
        if t not in seen_t:
            seen_t.add(t)
            dedup.append(h)
    return dedup[:max_lines]


def get_llm():
    """创建 LLM 实例（OpenAI 兼容）。

    配置（环境变量优先，兼容旧的文件配置）：
      DEEPSEEK_API_KEY / LLM_API_KEY  — API Key（默认兼容 ~/.hermes/deepseek_key 文件）
      LLM_BASE_URL                    — 服务端点（默认 https://api.deepseek.com/v1，
                                        可切换 OpenAI/vLLM/Ollama/火山方舟等任意 OpenAI 兼容服务）
      LLM_MODEL                       — 模型名（默认 deepseek-chat）
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
    return ChatOpenAI(model=model, temperature=0, max_tokens=2048,
                      api_key=key, base_url=base_url)


# ─── L0: 问题解析 ────────────────────────────────────────────────

def parse_problem(problem_text: str) -> dict:
    """从问题描述提取关键词（异常类型、服务名、时间等）。"""
    keywords = set()
    # 异常类型
    for m in re.finditer(r'\b([A-Z][A-Za-z]+(?:Exception|Error|Failure))\b', problem_text):
        keywords.add(m.group(1))
    # 常见关键词
    for kw in ["timeout", "超时", "OOM", "out of memory", "null", "连接", "失败", "reject"]:
        if kw.lower() in problem_text.lower():
            keywords.add(kw)
    # 引号中的标识符
    for m in re.finditer(r'["\']([\w.]+)["\']', problem_text):
        keywords.add(m.group(1))
    # camelCase / snake_case 标识符（字段溯源类问题：getOrderInfo/nature_name/assign_type 等）
    # 只收原始标识符；token 拆分与停用词过滤由 locate_code 内部处理
    for m in re.finditer(r'\b[a-z][a-zA-Z0-9_]{2,}\b', problem_text):
        ident = m.group(0)
        if any(c.isupper() for c in ident) or "_" in ident:  # 驼峰或下划线才收
            keywords.add(ident)
    return {
        "problem": problem_text,  # 完整问题描述（不硬截断 —— 可能含关键字段/上下文）
        "keywords": list(keywords)[:20],
        "stack_trace": "",
    }


# ─── L1: 日志压缩分析 ────────────────────────────────────────────

def analyze_logs(log_lines: list[str]) -> dict:
    """压缩日志 + 提取异常线索（综合视图：信号分级 + 服务聚合 + 高价值过滤）。"""
    compressed = compress_log(log_lines)
    # 找出包含 error/exception 的模板
    error_clues = []
    for t, count, level in compressed.get("key_templates", []):
        if re.search(r'(?i)\b(error|exception|fail|timeout|oom)\b', t):
            error_clues.append((t, count))
    return {
        "compressed": compressed,
        "formatted": format_compressed_log(compressed),
        "analysis_view": build_analysis_view(log_lines),  # RCA 定位用综合视图
        "error_clues": error_clues[:30],
    }


# ─── L2: 代码定位 ────────────────────────────────────────────────

def parse_stack_focus(stack_trace: str) -> list[dict]:
    """从异常栈提取 (文件路径, 行号) 焦点。"""
    focus = []
    for m in re.finditer(r'File "([^"]+\.py)", line (\d+)', stack_trace):
        focus.append({"path": m.group(1), "line": int(m.group(2))})
    for m in re.finditer(r'([\w/]+\.\w+):(\d+)', stack_trace):
        focus.append({"path": m.group(1), "line": int(m.group(2))})
    return focus


def locate_code(repo_root: str, keywords: list[str], stack_trace: str = "") -> list[dict]:
    """根据关键词和栈定位相关代码文件并压缩。"""
    found = []
    root = Path(repo_root)
    if not root.exists():
        return found

    focus = parse_stack_focus(stack_trace)

    candidates = []
    # 栈中焦点文件
    for f in focus:
        p = Path(f["path"])
        if p.exists():
            candidates.append((p, f["line"]))
        else:
            # 相对仓库根查找
            rel = root / f["path"]
            if rel.exists():
                candidates.append((rel, f["line"]))

    # 按关键词搜文件（支持 Python/Java/Go/Kotlin 等常见语言）
    # 两轮：①文件名含完整关键词（强相关）优先 ②camelCase 拆分 token（排除通用词）
    kw_lower = [k.lower() for k in keywords]
    _STOP = {"service", "impl", "vo", "enum", "entity", "mapper", "context", "response",
             "request", "data", "config", "model", "base", "util", "utils", "common",
             "manager", "handler", "node", "param"}
    kw_tokens = set()
    for k in keywords:  # 用原始大小写拆分（camelCase 边界依赖大写）
        for tok in re.split(r"[_\-\.]+|(?<=[a-z0-9])(?=[A-Z])", k):
            t = tok.lower()
            if len(t) >= 4 and t not in _STOP:
                kw_tokens.add(t)
    skip_dirs = {".git", "target", "build", "out", "node_modules", ".venv", "venv",
                 "__pycache__", ".idea", ".gradle", "dist", "vendor"}
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".java", ".kt", ".go", ".scala"):
            continue
        if p.name.startswith("test_") or p.name.endswith("Test.java"):
            continue
        if any(part in skip_dirs for part in p.parts):
            continue
        pname = p.name.lower()
        strong = any(k in pname for k in kw_lower)          # 完整关键词命中
        weak = any(tok in pname for tok in kw_tokens)       # 拆分 token 命中
        if strong or weak:
            # 命中数打分：完整关键词命中权重 3，token 命中权重 1
            score = sum(3 for k in kw_lower if k in pname) + sum(1 for tok in kw_tokens if tok in pname)
            candidates.append((p, None, strong, score))
    # 强相关优先 + 命中数降序，取前 8
    candidates.sort(key=lambda x: (not x[2], -x[3]))

    seen = set()
    for p, focus_line, _strong, _score in candidates[:5]:
        if str(p) in seen:
            continue
        seen.add(str(p))
        try:
            src = p.read_text()
        except Exception:
            continue
        compressed = compress_code(src, file_path=str(p), keywords=keywords,
                                   focus_lines=[focus_line] if focus_line else None)
        found.append({
            "path": str(p),
            "compressed_code": compressed,
            "tokens_approx": len(compressed) // 2,
        })
    return found


# ─── L3: 根因分析 (LLM) ─────────────────────────────────────────

def analyze_root_cause(problem: dict, log_analysis: dict,
                       code_contexts: list[dict], runtime_data: str = "") -> str:
    """让 LLM 综合所有压缩上下文输出根因分析。

    自研护栏设计：
    - 证据链：每条结论引用具体日志/代码证据
    - 置信度分级：结论标注 高/中/低，推断与有据结论显式分开
    - errno 语义：空结果先看 errno（0=业务空，非0=故障），禁止把"业务空"误判为"接口故障"
    - 七节报告：固定结构输出，便于复查
    """
    llm = get_llm()
    from langchain_core.messages import HumanMessage, SystemMessage

    sys_prompt = (
        "你是一名资深 SRE + 后端架构师，负责从压缩后的日志、代码片段和运行时数据中定位问题根因。\n"
        "要求：\n"
        "1. 按七节模板输出根因分析：① 根因链（触发条件→直接原因→根本原因）② 关键证据 ③ 定位置信度 ④ 修复建议 ⑤ 仍缺的数据 ⑥ 排除的假设 ⑦ 一句话结论\n"
        "1b. 字段溯源/规则计算类问题（字段值由规则/策略/映射生成时）：① 必须给出**完整代码路径**（每个方法名+行号+每步作用，如："
        "orderProxyService.getOrderInfo(L824) 返回订单原始数据 → propertyQueryService.query(\"bwh.order\")(L825) 拉取规则配置 → "
        "invokeStrategyService.invokeStrategy 执行规则 → response.toJavaObject 映射字段），并标注字段**来源类型**"
        "（TRANSFORMED=规则/策略计算 / DB=直接映射 / 透传=上游返回）；② 必须展开拼接/计算逻辑（循环每个规则、命中条件、"
        "返回值、分隔符与拼接方式，如 StringUtils.join(tags, separatorName)）；③ 必须列出规则**输入字段值**"
        "（来自字段汇总行/字段[xxx]行，如 assign_type=2）并逐条说明其如何触发规则\n"
        "2. 每条结论必须引用具体证据（日志行、代码行），禁止无证据断言\n"
        "3. 置信度分级：高（证据直接命中）/ 中（多处间接证据）/ 低（推断）；推断必须与有据结论显式分开标注\n"
        "4. errno 语义护栏：遇到返回空/空列表/空串时，先看 errno/statusCode —— "
        "errno=0 且空 = 业务事实空（用户确实无此数据，接口正常）；errno≠0 且空 = 接口/配置故障；"
        "禁止把'业务空'误判为'接口故障'，反之亦然\n"
        "5. 信息不足时明确指出还需要什么数据，标注 UNKNOWN，不要臆测\n"
        "6. 验证边界（字段溯源/配置类问题必做）：若结论依赖外部配置/规则（如 QLE 表达式、策略配置、"
        "数据源配置），先检查代码 import/依赖线索（运行时数据中 '依赖/import' 区与文件头）推断配置存储位置，"
        "并显式标注：哪些已直接验证、哪些未直接验证（⚠️）、如何人工核实（登录哪个平台/查哪个 key）\n"
        "7. 字段溯源/策略命中类问题：日志中的 '字段汇总' 行与 '字段[xxx]:' 行是订单/请求的核心字段值，"
        "也是 QLE 表达式/策略判定的输入 —— 分析策略为何命中时，必须先列出相关字段值（如 assign_type=2）"
        "再对照 QLE 条件判断，禁止跳过字段值直接下结论\n"
    )

    context = []
    context.append("## 问题描述")
    context.append(problem["problem"])
    context.append("")
    context.append("## 关键词")
    context.append(", ".join(problem["keywords"]))
    context.append("")

    # 聚焦证据区（检索相关行 —— 借鉴 field-source-tracing 分步检索：
    # 关键词来自问题（不写死），只呈现相关小块，无注意力稀释）
    try:
        focus_lines = retrieve_evidence(log_lines, problem["keywords"])
        if focus_lines:
            context.append("## 聚焦证据（按关键词检索的相关日志行）")
            context.extend(focus_lines)
            context.append("")
    except Exception:
        pass

    context.append("## 压缩后的日志（关键行 + 高频模板）")
    context.append(log_analysis["formatted"])  # 完整视图（信号已压缩，不硬截断 —— 硬截断会丢字段值证据）
    context.append("")

    if code_contexts:
        context.append("## 相关代码片段")
        for cc in code_contexts:
            context.append(cc["compressed_code"])  # 完整（代码已压缩，不硬截断）
            context.append("---")
        context.append("")

    if runtime_data:
        context.append("## 运行时数据（业务字段/代码片段，含 import 依赖线索）")
        context.append(runtime_data)  # 完整（字段值/依赖线索不硬截断）
        context.append("")

    user_prompt = "\n".join(context) + "\n\n请给出根因分析。"

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt),
    ]

    try:
        result = llm.invoke(messages)
        return result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        return f"根因分析失败: {str(e)[:300]}"


# ─── 主流程 ─────────────────────────────────────────────────────

def locate_root_cause(problem_text: str, log_lines: list[str],
                      repo_root: str, runtime_data: str = "",
                      stack_trace: str = "") -> dict:
    """
    完整问题定位流程。

    Returns:
        {
            "root_cause": LLM 分析结果,
            "tokens_consumed": 估计消耗 token,
            "compression_stats": 各阶段压缩统计,
        }
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # L0
    problem = parse_problem(problem_text)
    # L1
    log_analysis = analyze_logs(log_lines)
    # 短路求值（最便宜的先跑，命中即收工）：
    # 日志压缩后错误信号直接命中（异常类明确且无代码疑问）→ 跳过 L2 代码定位，直接 L3
    short_circuited = False
    code_contexts = []
    if log_analysis["error_clues"]:
        # 异常信号足够明确（如 RedisTimeoutException/ConnectionRefused 等基础设施类异常）
        first_clue = log_analysis["error_clues"][0][0].lower()
        if any(k in first_clue for k in ("exception", "timeout", "refused", "oom", "panic", "not found")):
            short_circuited = True
    if not short_circuited:
        # L2
        code_contexts = locate_code(repo_root, problem["keywords"], stack_trace)
    # L3
    root_cause = analyze_root_cause(problem, log_analysis, code_contexts, runtime_data)

    # Token 估算
    total_input_chars = (
        len(problem_text) + len(log_analysis["formatted"]) +
        sum(len(cc["compressed_code"]) for cc in code_contexts) +
        len(runtime_data)
    )
    total_output_chars = len(root_cause)

    return {
        "root_cause": root_cause,
        "short_circuited": short_circuited,  # 短路求值是否触发（跳过 L2 代码定位）
        "tokens_consumed": {
            "input": total_input_chars // 2,
            "output": total_output_chars // 2,
            "total": (total_input_chars + total_output_chars) // 2,
        },
        "compression_stats": {
            "logs": {
                "original_lines": log_analysis["compressed"]["original_lines"],
                "reduced_lines": log_analysis["compressed"]["reduced_lines"],
                "reduction_rate": log_analysis["compressed"]["reduction_rate"],
            },
            "code_files": len(code_contexts),
            "keywords": problem["keywords"],
        },
    }


if __name__ == "__main__":
    # 示例运行
    if len(sys.argv) < 4:
        print("Usage: python locator/agent.py '<problem>' <log_file> <repo_root> [runtime_data_file]")
        sys.exit(1)
    problem = sys.argv[1]
    log_path = sys.argv[2]
    repo = sys.argv[3]
    runtime = ""
    if len(sys.argv) > 4 and os.path.exists(sys.argv[4]):
        runtime = Path(sys.argv[4]).read_text()  # 完整（不硬截断）

    with open(log_path) as f:
        logs = f.readlines()

    result = locate_root_cause(problem, logs, repo, runtime)
    print("═" * 60)
    print("根因分析结果")
    print("═" * 60)
    print(result["root_cause"])
    print("═" * 60)
    print("Token 统计:", json.dumps(result["tokens_consumed"], ensure_ascii=False))
    print("压缩统计:", json.dumps(result["compression_stats"], ensure_ascii=False))
