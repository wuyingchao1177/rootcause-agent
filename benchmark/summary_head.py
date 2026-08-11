#!/usr/bin/env python3
"""实验：ours + 极简摘要层（借鉴 headroom 的 token 级聚焦，不丢证据链）。

摘要头设计（~200 chars，无模型）：
- Top 错误服务（服务级错误分布）
- 高频错误类型关键词（error/exception/timeout/denied 等）
- 关键 token（数字/错误码/服务名）
"""
import re
from collections import Counter

SERVICE_RE = re.compile(r'\[([a-zA-Z0-9_.-]+)\]')
ERROR_KW = re.compile(
    r'(?i)(error|exception|fatal|critical|panic|timeout|out of memory|oom|'
    r'denied|unauthorized|forbidden|not found|failed|reject|refused|unavailable|'
    r'connection|retry|503|500|5\d\d|4\d\d)')


def build_summary_head(log_lines, max_chars: int = 250) -> str:
    """从原始日志行提取极简摘要（Top 服务 / 错误类型 / 关键 token）。"""
    svc_err = Counter()
    svc_total = Counter()
    err_types = Counter()
    key_tokens = Counter()
    for line in log_lines:
        m = SERVICE_RE.search(line)
        svc = m.group(1) if m else "?"
        svc_total[svc] += 1
        em = ERROR_KW.search(line)
        if em:
            svc_err[svc] += 1
            err_types[em.group(1).lower()] += 1
            # 关键 token：错误行中的数字/服务名
            for t in re.findall(r'\b[a-z][a-z0-9_-]{2,}\b', line.lower()):
                if len(t) > 3 and t not in ("error", "exception", "timeout"):
                    key_tokens[t] += 1
    parts = []
    # Top 错误服务（按错误数，标注总数）
    if svc_err:
        top_svc = svc_err.most_common(4)
        parts.append("Top错误服务: " + ", ".join(f"{s}({n}err/{svc_total.get(s,0)}log)" for s, n in top_svc))
    # 高频错误类型
    if err_types:
        parts.append("错误类型: " + ", ".join(f"{k}x{n}" for k, n in err_types.most_common(5)))
    # 关键 token（服务/方法/字段名 top）
    if key_tokens:
        toks = [t for t, n in key_tokens.most_common(20) if n >= 2]
        parts.append("关键token: " + ", ".join(toks[:12]))
    head = " | ".join(parts)
    return head[:max_chars]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/tmp")
    sys.path.insert(0, ".")
    from re3_compare import load_case
    for case in ["re3ss_carts_f4_1", "re3ss_carts_f4_3", "re3ob_adservice_f3_1"]:
        log_lines, _, _ = load_case(case)
        head = build_summary_head(log_lines)
        print(f"=== {case} 摘要头（{len(head)} chars）===")
        print(head)
        print()
