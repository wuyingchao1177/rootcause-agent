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


# ─── 记录级过滤（采集层压缩的通用复刻）───────────────────────────────
# 思想（源自 log_search trace_detail）：无业务载荷/无业务标识的记录不产生 token。
# 通用实现：记录 = 前缀标记行（[span3]/[log2]/[req1] 等任意「前缀+序号」标记）及其子行；
#           业务标识 = 行内 uri/url/interface/api/path/method 等标识键。
# 换日志格式时只需调整前缀/标识键正则（不写死具体格式）。

_RECORD_PREFIX_RE = re.compile(r'^\s*\[([A-Za-z]+)(\d+)[A-Za-z]*\]')
_IDENT_KEY_RE = re.compile(
    r'([A-Za-z_]*uri[A-Za-z_]*|[A-Za-z_]*url[A-Za-z_]*|[A-Za-z_]*interface[A-Za-z_]*|'
    r'[A-Za-z_]*api[A-Za-z_]*|[A-Za-z_]*path[A-Za-z_]*|[A-Za-z_]*method[A-Za-z_]*'
    r'|[A-Za-z_]*标识[A-Za-z_]*)\s*=')


def _record_id(line: str) -> str | None:
    """提取行所属记录前缀（如 [span3req] → span3）；非记录行返回 None。"""
    m = _RECORD_PREFIX_RE.match(line)
    return f"{m.group(1)}{m.group(2)}" if m else None


def _record_has_identifier(line: str) -> bool:
    """记录行是否含业务标识键（uri/url/interface/api/path/method 等）。"""
    return bool(_IDENT_KEY_RE.search(line))


def _record_identifier_empty(line: str) -> bool:
    """标识键的值是否为空（= 后紧跟空白/标点/结束 → 空值，等效 log_search 的 'uri 为空跳过'）。"""
    for m in _IDENT_KEY_RE.finditer(line):
        after = line[m.end():]
        if not after or after[0] in (' ', '\t', ',', '}', ']', '[', '|'):
            return True
    return False


def _drop_empty_identifier_records(lines: list[str]) -> list[str]:
    """记录级过滤：业务标识为空的记录（及其子行）整组删除。
    效果对齐 log_search trace_detail：无 uri 的桥接 span 及其日志不产生 token。"""
    empty_groups = set()
    for line in lines:
        rid = _record_id(line)
        if rid and _record_has_identifier(line) and _record_identifier_empty(line):
            empty_groups.add(rid)
    if not empty_groups:
        return lines
    return [l for l in lines if _record_id(l) not in empty_groups]


def _is_placeholder_row(line: str) -> bool:
    """通用占位行检测：去掉常见前缀（时间戳/级别/序号）后，剩余仅占位符或为空。"""
    s = re.sub(r'^\s*[\d\-:T.\s]+', '', line)                       # 时间戳
    s = re.sub(r'^\s*(ERROR|WARN|INFO|DEBUG|FATAL|UNKNOWN|TRACE)\s*', '', s, flags=re.I)
    s = re.sub(r'^\s*\[[A-Za-z]+\d+[A-Za-z]*\]\s*', '', s)          # 记录前缀
    s = s.strip().strip('|,;: ').strip()
    if not s:
        return True
    tokens = re.findall(r'\[[^\]]*\]|[\w.:/-]+', s)
    if not tokens:
        return False  # 有符号但无 token —— 保守不删
    _PH = {"null", "none", "n/a", "na", "-", "--", "[uri not found]",
           "[not found]", "[]", "{}"}
    # 注意：有内容的 [xxx]（如 [front-end] [ undefined, undefined ]）不算占位 ——
    # 是真实业务日志（Sock Shop 数据缺失的故障信号），误删会破坏异常上下文窗口
    return all(t.lower() in _PH for t in tokens)


def is_key_line(line: str) -> bool:
    """ERROR/异常行，或含业务信号的行。"""
    # 字段标记行（字段[xxx]: value —— 溯源/构建工具提取的业务字段值）强制为关键行
    if re.search(r'字段\[[^\]]+\]', line):
        return True
    return bool(KEY_LINE_RE.search(line)) or bool(BUSINESS_SIGNAL_RE.search(line))


def compress_log(lines: list[str], max_lines: int = 200000,
                 max_key_templates: int = 1000,
                 max_noise_templates: int = 300,
                 context_window: int = 2,
                 tail_window: int = 120,
                 drop_placeholder_rows: bool = True) -> dict:
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
    if drop_placeholder_rows:
        lines = [l for l in lines if not _is_placeholder_row(l)]
        lines = _drop_empty_identifier_records(lines)
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
                          noise_limit: int = 100, tail_chars: int = 120) -> str:
    """格式化压缩日志。长行截断时截头保尾（业务字段值常在 JSON 长行尾部，如 level_type/cheat）。"""
    def _clip(s: str) -> str:
        if len(s) <= max_template_chars:
            return s
        head = s[:max_template_chars]
        # 尾部含业务字段值特征才保留（字段标记 或 键=数字值，等号形式）；
        # JSON 冒号键值（"timestamp":1732...）与异常堆栈尾部不保留 —— 避免受害方连锁症状误导
        tail = s[-tail_chars:]
        if re.search(r'字段\[|=\s*-?\d+', tail):
            return f"{head} ...{tail}"
        return head

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
            parts.append(f"{prefix} {_clip(t)}")

    ctx = compressed.get("context_lines", [])
    if ctx:
        parts.append("")
        parts.append("[异常上下文(前因后果)]")
        for line in ctx[:60]:
            parts.append(f"  {_clip(line)}")

    noise = compressed.get("noise_templates", [])
    if noise:
        parts.append("")
        parts.append("[普通日志模板]")
        for t, count in noise[:noise_limit]:
            parts.append(f"[x{count}] {_clip(t)}")

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


def _clip_line(s: str, max_chars: int = 250, tail_chars: int = 120) -> str:
    """长行截头保尾：头部 max_chars + 尾部 tail_chars（尾部含业务字段值特征才保留）。

    业务字段值（字段[xxx]: 或 键=数字）常在 JSON 长行尾部；异常堆栈尾部（java.lang.xxx）
    与 JSON 冒号键值（timestamp:123）不保留 —— 避免受害方连锁症状误导。
    """
    if len(s) <= max_chars:
        return s
    head = s[:max_chars]
    tail = s[-tail_chars:]
    if re.search(r'字段\[|=\s*-?\d+', tail):
        return f"{head} ...{tail}"
    return head


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

    # 字段值汇总区块（前置 —— 订单/请求核心字段值，规则判定输入，LLM 第一眼可见）
    field_rows = [kt[0] for kt in compressed["key_templates"] if re.search(r'字段\[[^\]]+\]', kt[0])]
    if field_rows:
        seen, summary = set(), []
        for row in field_rows:
            for m in re.finditer(r'字段\[([^\]]+)\]:\s*([^\|]{1,60})', row):
                fname, fval = m.group(1), m.group(2).strip()
                # 值清洗：去掉字段名前缀重复与转义引号（assign_type":"2 → 2）
                fval = re.sub(rf'^{re.escape(fname)}\s*[":=\\]*\s*', '', fval)
                fval = fval.replace('\\"', '').replace('\\', '').strip('"').strip()
                if fname not in seen:
                    seen.add(fname)
                    summary.append(f"{fname}={fval}")
        if summary:
            parts.append("")
            parts.append("[字段值汇总(规则判定输入)] " + " | ".join(summary[:15]))

    kts = compressed["key_templates"]
    if kts:
        parts.append("")
        parts.append("[服务级错误分布]")
        parts.append(service_error_distribution(kts))

        high_value = [kt for kt in kts if not LOW_VALUE_RE.search(kt[0])]
        # 字段标记模板（字段[xxx]: value —— 业务字段值/溯源提取的关键证据）优先，
        # 防止 [x1] 单次字段行被高 count 噪声行挤出 strong 窗口
        field_kts = [kt for kt in high_value if re.search(r'字段\[[^\]]+\]', kt[0])]
        rest = [kt for kt in high_value if not re.search(r'字段\[[^\]]+\]', kt[0])]
        strong = (field_kts + rest)[:strong_count]
        parts.append("")
        parts.append("[业务错误日志(高价值)]")
        for t, count, level in strong:
            prefix = f"[x{count}]" if count > 1 else "    "
            # 字段标记行保留到最后一个字段标记（业务字段值全保留，防中部字段被截断）；
            # 但配置全文（如 81K 的 qleExpression JSON）加合理上限 —— 截头保尾 2500，
            # 防超长行撑爆视图导致 LLM 注意力失效（信息过载与硬截断是两个极端）
            if re.search(r'字段\[[^\]]+\]', t):
                last = t.rfind('字段[')
                parts.append(f"{prefix} {_clip_line(t[:last+150], max_chars=2500, tail_chars=400)}")
            else:
                parts.append(f"{prefix} {_clip_line(t, max_chars=200)}")

    tail = compressed.get("tail_lines", [])
    if tail:
        parts.append("")
        parts.append("[日志尾部(原始,兜底信号)]")
        for line in tail[:60]:
            parts.append(f"  {_clip_line(line, max_chars=200)}")

    parts.append("")
    parts.append(f"// 原始 {compressed['original_lines']} 行 → 压缩后 {len(kts)} 条信号"
                 f" (信号分级 + 服务聚合 + 高价值过滤)")
    return "\n".join(parts)
