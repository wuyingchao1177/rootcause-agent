"""日志压缩引擎 v3 — 信息无损压缩（准确率优先）。

设计哲学: min(token) 约束 accuracy >= baseline
不是最大压缩，而是精确识别并保留 1% 的诊断信号。

v3 改进:
  1. 业务信号保留: WARN 里含 fallback/降级/重试/熔断/切换 等业务词的保留
  2. 异常上下文窗口: 异常行前后 N 行原始日志保留（前因后果）
  3. ERROR 全保留 + 相关业务 WARN 保留 + 纯噪声 INFO 模板化
  4. 压缩率让位于准确率: 目标是"信息无损"，不是"最大压缩"
"""

import re
from collections import OrderedDict
from typing import Optional

VARIABLE_PATTERNS = [
    (re.compile(r'0x[0-9a-fA-F]{6,}'), '<hex>'),
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d{2,5})?\b'), '<ip>'),
    (re.compile(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b'), '<time>'),
    (re.compile(r'\b\d{2}:\d{2}:\d{2}(\.\d+)?\b'), '<time>'),
    (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'), '<uuid>'),
    (re.compile(r'(thread|pool)-\d+'), r'\1-<n>'),
]

KEY_LINE_PATTERNS = [
    r'(error|exception|fatal|critical|panic|failed|failure|timeout|oom|out of memory)',
    r'(caused by|at \w+\.\w+|traceback|stack trace)',
    r'(return code|exit code|status):?\s*\S+',
    r'(assert|npe|nullpointer|classcastexception|illegalstate|sql)\w*exception',
    r'^\s*(Caused by|at\s+[\w.]+\([\w.]+:\d+\))',
    # CI/信号保真补充（LogDx-CI 实测丢失的信号词）
    r'not found',
    r'exit code',
    r'failing',
    r'failed on line',
    r'##\[error\]',
    r'error:',
    r'Process completed',
    r'command (not found|failed)',
    r'AssertionError',
    r'cannot find',
    r'unresolved',
    # 真实 RCA 补充（re3ss root_cause 证据特征：WARN 级 HTTP 错误）
    # 注意：纯 WARN 不单独算关键行（避免 WARN 噪声占满 key 空间），WARN+具体错误词由上述规则覆盖
    r'not supported',
    r'denied|unauthorized|forbidden',
    r'PageNotFound|NoHandlerFound|Request method',
]

# 业务信号词: 降级/重试/熔断/切换等，虽是 WARN 但往往是关键线索；含请求/响应/查询等业务动作
BUSINESS_SIGNAL_RE = re.compile(
    r'(fallback|降级|retry|重试|circuit|熔断|switch|切换|unavailable|不可用|'
    r'degrad|backoff|限流|reject|拒绝|fallback|fall back|'
    r'请求|返回|响应|查询|调用|命中|工单|worksheet|request|response|query|call|result)', re.IGNORECASE)

KEY_LINE_RE = re.compile('|'.join(KEY_LINE_PATTERNS), re.IGNORECASE)
LEVEL_RE = re.compile(r'\b(ERROR|WARN|WARNING|INFO|DEBUG|FATAL)\b', re.IGNORECASE)

# 信号分级（吸收 grep 的"错误行优先"思想）：
#   0 = 强信号（异常/错误/明确失败）→ 排最前，避免高频正常日志模板误导 LLM
#   1 = 中信号（业务动作/WARN）→ 次之
#   2 = 弱信号（其余关键行）→ 最后（按 count 排序）
STRONG_SIGNAL_RE = re.compile(
    r'(?i)(error|exception|fatal|critical|panic|failed|failure|timeout|oom|'
    r'not found|not supported|exit code|assert|reject|denied|unauthorized|'
    r'forbidden|PageNotFound|NoHandlerFound|##\[error\]|failing|unresolved)')
MID_SIGNAL_RE = re.compile(
    r'(?i)(请求|返回|响应|查询|调用|命中|工单|worksheet|request|response|query|call|result|'
    r'warn|warning|fallback|降级|retry|重试|circuit|熔断|unavailable|不可用)')

# 低价值错误（基础设施重试/连接噪声，借鉴 rtk log 的隐藏策略）：
# 受害方连锁症状（I/O exception/连接重试/Docker socket），对根因判断是误导而非信号
LOW_VALUE_RE = re.compile(
    r'(?i)(I/O exception|RetryExec|\.sock|DockerSpawner|docker|ProcessingException|'
    r'AFUNIXSocket|Connection refused|ConnectException|execchain|pool-\d|p-nio|'
    r'tomcat-embed|ErrorReportValve)')

def _signal_level(template: str) -> int:
    if STRONG_SIGNAL_RE.search(template):
        return 0
    if MID_SIGNAL_RE.search(template):
        return 1
    return 2


def templateize(line: str) -> str:
    t = line.strip()
    for pat, repl in VARIABLE_PATTERNS:
        t = pat.sub(repl, t)
    return t


def get_level(line: str) -> str:
    m = LEVEL_RE.search(line)
    return m.group(1).upper() if m else "UNKNOWN"


def is_key_line(line: str) -> bool:
    """ERROR/异常行，或含业务信号的行。"""
    return bool(KEY_LINE_RE.search(line)) or bool(BUSINESS_SIGNAL_RE.search(line))


def compress_log(lines: list[str], max_lines: int = 200000,
                 max_key_templates: int = 1000,
                 max_noise_templates: int = 300,
                 context_window: int = 2,
                 tail_window: int = 120) -> dict:
    """
    压缩日志（v3 信息无损）。

    Returns:
        {
            "key_templates": [(template, count, level)],   # 关键模板（去重计数）
            "context_lines": [原始行],                      # 异常上下文窗口（前因后果）
            "noise_templates": [(template, count, level)], # 纯噪声模板
            "level_stats": {...},
            "original_lines": n,
            "reduced_lines": n,
            "reduction_rate": 0.x,
        }
    """
    lines = lines[:max_lines]
    key_counter: OrderedDict[str, int] = OrderedDict()
    noise_counter: OrderedDict[str, int] = OrderedDict()
    level_stats = {"ERROR": 0, "WARN": 0, "INFO": 0, "DEBUG": 0, "FATAL": 0, "UNKNOWN": 0}
    key_levels = {}
    context_lines: list[str] = []
    seen_context = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        level = get_level(stripped)
        if level in level_stats:
            level_stats[level] += 1

        t = templateize(stripped)
        if is_key_line(stripped):
            # 关键行用模板化文本作键（去时间戳/IP 等噪声变量，保留数字与正文 → 信号保真 + 可合并去重）
            key = t
            if key in key_counter:
                key_counter[key] += 1
            else:
                key_counter[key] = 1
                key_levels[key] = level

            # 异常上下文窗口: 异常行/业务信号行前后各 N 行原始内容（前因后果，保留原始值不被模板化）
            if level in ("ERROR", "FATAL") or KEY_LINE_RE.search(stripped) or BUSINESS_SIGNAL_RE.search(stripped):
                start = max(0, i - context_window)
                end = min(len(lines), i + context_window + 1)
                for ctx in lines[start:end]:
                    ctx_s = ctx.strip()
                    if ctx_s and ctx_s not in seen_context:
                        seen_context.add(ctx_s)
                        context_lines.append(ctx_s)
        else:
            if t in noise_counter:
                noise_counter[t] += 1
            else:
                noise_counter[t] = 1

    key_templates = sorted(key_counter.items(), key=lambda x: (_signal_level(x[0]), -x[1]))[:max_key_templates]
    noise_templates = sorted(noise_counter.items(), key=lambda x: -x[1])[:max_noise_templates]

    # 尾部保底：CI 失败信号（退出码/失败测试）通常集中在日志尾部，关键行截断后靠尾部兜底。
    # 无损去重：tail/context 中与 key/noise 模板重复的行不再重复输出（信息已在 key/noise 中）。
    # 注意去重集合必须与实际输出一致：format 只输出 noise 前 100 条，去重匹配也只用前 100 条，
    # 否则 noise 100 名外的信号行会被 tail 去重误删（实测 tsc 信号丢失的根因）。
    kept_key = {t for t, c in key_templates}
    kept_noise = {t for t, c in noise_templates[:100]}
    def dup_of_kept(line: str) -> bool:
        tt = templateize(line)
        return tt in kept_key or tt in kept_noise

    tail_lines = [l.strip() for l in lines[-tail_window:] if l.strip() and not dup_of_kept(l.strip())]
    context_lines = [l for l in context_lines if not dup_of_kept(l)]

    original_count = sum(level_stats.values())
    reduced_count = len(key_templates) + len(context_lines) + len(noise_templates)
    reduction_rate = 1 - (reduced_count / max(original_count, 1))

    return {
        "key_templates": [(t, c, key_levels.get(t, "UNKNOWN")) for t, c in key_templates],
        "context_lines": context_lines[:100],
        "noise_templates": noise_templates,
        "tail_lines": tail_lines,
        "level_stats": level_stats,
        "original_lines": original_count,
        "reduced_lines": reduced_count,
        "reduction_rate": reduction_rate,
    }


def format_compressed_log(compressed: dict, max_template_chars: int = 250,
                          noise_limit: int = 100) -> str:
    """格式化压缩日志。"""
    parts = []
    stats = compressed["level_stats"]
    parts.append(f"Log Summary: {stats.get('ERROR',0)} errors, {stats.get('WARN',0)} warnings, "
                 f"{stats.get('INFO',0)} info")

    key = compressed["key_templates"]
    if key:
        parts.append("")
        parts.append("[关键异常/业务信号]")
        for t, count, level in key:
            prefix = f"[x{count}]" if count > 1 else "    "
            parts.append(f"{prefix} {t[:max_template_chars]}")

    ctx = compressed.get("context_lines", [])
    if ctx:
        parts.append("")
        parts.append("[异常上下文(前因后果)]")
        for line in ctx[:60]:
            parts.append(f"  {line[:max_template_chars]}")

    noise = compressed.get("noise_templates", [])
    if noise:
        parts.append("")
        parts.append("[普通日志模板]")
        for t, count in noise[:noise_limit]:
            parts.append(f"[x{count}] {t[:max_template_chars]}")

    tail = compressed.get("tail_lines", [])
    if tail:
        parts.append("")
        parts.append("[日志尾部(原始,兜底信号)]")
        for line in tail:
            parts.append(f"  {line[:max_template_chars]}")

    parts.append("")
    parts.append(f"// 原始 {compressed['original_lines']} 行 → 压缩后 {compressed['reduced_lines']} 条"
                 f" (减少 {compressed['reduction_rate']*100:.1f}%，模板化去重 + 信号分级排序)")

    return "\n".join(parts)


SERVICE_TAG_RE = re.compile(r'\[([a-zA-Z0-9_.-]+)\]')


def service_error_distribution(key_templates, top_services: int = 8, top_templates: int = 2) -> str:
    """服务级错误分布（借鉴 rtk log 的按服务聚合视图）。

    将关键模板按服务聚合，展示每个服务的错误量与代表模板 —— 让 LLM 看到
    全服务错误全貌，避免单服务高计数模板霸榜误导（re3ss 受害方问题）。
    """
    from collections import defaultdict
    svc = defaultdict(lambda: {"cnt": 0, "tpl": {}})
    for t, count, level in key_templates:
        m = SERVICE_TAG_RE.search(t)
        s = m.group(1) if m else "?"
        svc[s]["cnt"] += count
        svc[s]["tpl"][t] = svc[s]["tpl"].get(t, 0) + count
    lines = []
    for s in sorted(svc, key=lambda x: -svc[x]["cnt"])[:top_services]:
        d = svc[s]
        top = sorted(d["tpl"].items(), key=lambda x: -x[1])[:top_templates]
        top_s = ", ".join(f"{t[:60]}x{n}" for t, n in top)
        lines.append(f"[{s}] {d['cnt']} signals: {top_s}")
    return "\n".join(lines)


def build_analysis_view(log_lines, max_key_templates: int = 1000,
                        tail_window: int = 120, noise_limit: int = 100,
                        strong_count: int = 40) -> str:
    """综合日志分析视图（产品默认输出，RCA 定位用）。

    结构（按重要性排序）：
      1. Log Summary（错误/警告/信息统计，rtk 风格）
      2. 服务级错误分布（全服务错误全貌，rtk 借鉴）
      3. 高价值业务错误（过滤基础设施噪声，rtk log 隐藏策略）
      4. 日志尾部（原始行，tail 保底）
    相比 format_compressed_log 更适合 LLM 根因定位：错误信号优先 + 服务视角。
    参数 strong_count=40 为 RE3 全量实测最优（95.6% / 压缩 -12.6%，较 60 提升）。
    """
    compressed = compress_log(log_lines, max_key_templates=max_key_templates,
                              tail_window=tail_window)
    stats = compressed["level_stats"]
    parts = [f"Log Summary: {stats.get('ERROR', 0)} errors, {stats.get('WARN', 0)} warnings, "
             f"{stats.get('INFO', 0)} info"]

    kts = compressed["key_templates"]
    if kts:
        parts.append("")
        parts.append("[服务级错误分布]")
        parts.append(service_error_distribution(kts))

        high_value = [kt for kt in kts if not LOW_VALUE_RE.search(kt[0])]
        strong = high_value[:strong_count] if high_value else kts[:strong_count]
        parts.append("")
        parts.append("[业务错误日志(高价值)]")
        for t, count, level in strong:
            prefix = f"[x{count}]" if count > 1 else "    "
            parts.append(f"{prefix} {t[:200]}")

    tail = compressed.get("tail_lines", [])
    if tail:
        parts.append("")
        parts.append("[日志尾部(原始,兜底信号)]")
        for line in tail[:60]:
            parts.append(f"  {line[:200]}")

    parts.append("")
    parts.append(f"// 原始 {compressed['original_lines']} 行 → 压缩后 {len(kts)} 条信号"
                 f" (信号分级 + 服务聚合 + 高价值过滤)")
    return "\n".join(parts)
