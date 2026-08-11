#!/usr/bin/env python3
"""从原始 trace 日志（222 条/48 span）构建脱敏 case 日志行。

等价 log_search trace_detail 的过滤 + 结构化提取（通用版）：
  1. 记录级过滤：空业务标识（uri=[URI not found]/空）的 span 整组跳过（48→31）
  2. 结构化提取：从原始日志文本抽业务字段（_msg/interface/body/result/Apollo 配置），丢弃 headers/cookie/token 敏感区
输出：samples/case_*_full_logs.json（logs 字段替换为原始版）
"""
import json
import os
import re
import sys

BASE = os.path.expanduser("~/claudecodeAndHermessAndObsidian/Skills & Tools/rootcause-agent")
SRC = "/private/tmp/trace_full_logs_0ab688896a797486aa55d190d44c4102.txt"

# 敏感键（headers/cookie/token 区不进入 case）
_SENSITIVE_KEYS = ("cookie", "ssoTicket", "odin_jwt_token", "secdd-authentication",
                   "secdd-challenge", "wsgsig", "sec-ch-ua", "user-agent", "x-forwarded",
                   "clientIp", "client_ip", "x-real-ip", "x-real-port")


# 关键业务字段（出现时保留字段上下文，防截断丢失；
# 通用高频字段表 —— 字段提取不写死具体业务字段名，聚焦证据/字段汇总
# 从行内出现的任意字段自动提取（见 build_analysis_view 的字段值汇总）
_KEY_FIELDS = ["cheat", "assign_type", "nature_name", "order_status", "order_status_name",
               "level_type", "type_name", "extra_type", "order_id", "long_rent_type",
               "business_type", "is_driver_detour_fraud"]


def extract_business(line: str, span_idx: int) -> str | None:
    """从一条原始日志文本提取业务内容（脱敏），返回 None 表示无可提取内容。"""
    # _msg= 后的业务内容
    msg_idx = line.find("_msg=")
    msg = line[msg_idx + 5:] if msg_idx >= 0 else line
    # 去掉时间戳/类名前缀
    msg = re.sub(r'^\[INFO\]\[[^\]]*\]\[[^\]]*\]\s*', '', msg)
    # headers= 整段剔除（值含嵌套 {}、转义引号与 | → 匹配到 }|| 或行尾）
    msg = re.sub(r'headers=\{.*?\}\s*\|\|', 'headers=<redacted>||', msg)
    msg = re.sub(r'headers=\{.*\}', 'headers=<redacted>', msg)
    msg = re.sub(r'headers=\S+', 'headers=<redacted> ', msg)
    # 敏感键值剔除（cookie/ssoTicket/token 等，键允许带后缀如 x-forwarded-for）
    for k in _SENSITIVE_KEYS:
        msg = re.sub(rf'({k}[^=]*=)[^\s|}}]+', rf'\1<redacted>', msg)
    # 内网/公网 IP 脱敏（url/host 等字段；保留手机号段外的纯 IP）
    msg = re.sub(r'(?<!\d)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\d)', '<ip>', msg)
    # 手机号脱敏（11 位 1[3-9] 开头；order_id 等 14 位长数字不受影响）
    msg = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '<phone>', msg)
    # 关键业务字段：宽松匹配（容忍 JSON 转义分隔符）；值排除操作符开头
    # （assign_type != null / == 1 等是 QLE 配置表达式，非订单字段值 —— 不收，防污染）
    field_parts = []
    for f in _KEY_FIELDS:
        for m in re.finditer(re.escape(f) + r'[^0-9A-Za-z]{0,4}[:=][^0-9A-Za-z]{0,4}([^\",}\s!&|=<>\\][^\",}]{0,80})', msg):
            field_parts.append(f"字段[{f}]: {f}{m.group(0)[len(f):]}")
    # 过滤 QLE 表达式片段（assign_type != null / == 1 等配置表达式，非订单字段值 —— 防污染）
    field_parts = [p for p in field_parts
                   if not re.search(r'(!=|==|!= null|== null|&&|\|\|)\s*\S|null', p)]
    # 同字段去重（保留每个字段首个值 —— 重复提取会挤掉后续字段）
    _seen_f, field_parts2 = set(), []
    for _p in field_parts:
        _k = _p.split(":")[0]
        if _k not in _seen_f:
            _seen_f.add(_k)
            field_parts2.append(_p)
    field_parts = field_parts2
    # 字段值汇总（一行全景 —— 帮助 LLM 关联同一对象的字段值；
    # 全字段不截断 —— 所有字段值可见）
    _summary, _seen = [], set()
    for _p in field_parts:
        _key = _p.split(":")[0]
        if _key not in _seen:
            _seen.add(_key)
            _summary.append(_p.split(":", 1)[-1].strip())
    # 原始内容完整保留（不硬截断 —— 字段提取(field_parts)已附加保真）
    msg = msg.strip()
    if field_parts:
        msg = msg + " || " + " | ".join(field_parts[:8])
    if _summary:
        msg = msg + " || 字段汇总: " + " | ".join(_summary)
    if not msg or msg in ("<redacted>", "null"):
        return None
    return msg


# 被过滤记录中的业务信号模式（空标识记录若含这些 → 回收进附加区，不丢关键证据）
_ORPHAN_SIGNAL_RE = re.compile(
    r'(reqApolloAnalysisField|HttpUtil|redis log|GigaFactory|allsparkManagerService|'
    r'order_status|order_type|nature_name|assign_type|cheat|checkOrderCheatInfo|'
    r'level_type|orderEnum)', re.I)


def build_log_lines() -> list[str]:
    d = json.load(open(SRC, encoding="utf-8"))
    lines = []
    orphan = []
    lines.append(f"TRACE {d['trace_id']} spans={d['span_total']} logs={d['log_total']}")
    for s in d["spans"]:
        idx = s["span_index"]
        uri = s.get("uri") or ""
        # 记录级过滤：空业务标识 span 整组跳过（等效 log_search 的 uri 空跳过）
        if not uri or uri == "[URI not found]":
            # 信号回收：空标识记录中含业务信号的行保留为附加证据（不占 span 数）
            for lg in (s.get("logs") or []):
                biz = extract_business(lg, idx)
                if biz and _ORPHAN_SIGNAL_RE.search(biz):
                    orphan.append(f"[orphan{idx}] {biz}")
            continue
        lines.append(f"[span{idx}] uri={uri} host={s.get('host','')[:60]} logs={len(s.get('logs') or [])}")
        for lg in (s.get("logs") or []):
            biz = extract_business(lg, idx)
            if biz:
                lines.append(f"[span{idx}log] {biz}")
    # 附加证据区（被过滤记录的业务信号）
    if orphan:
        lines.append("## 附加证据（被过滤记录的信号回收）")
        lines.extend(orphan[:60])
    return lines


def main():
    lines = build_log_lines()
    kept_spans = len(set(re.findall(r'\[span(\d+)\]', "\n".join(lines))))
    out = os.path.join(BASE, "samples", "case_full_logs.json")
    json.dump({"trace_id": "0ab688896a797486aa55d190d44c4102", "logs": lines},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 构建完成: {out}")
    print(f"   日志行数: {len(lines)} | 保留 span: {kept_spans}（48 → {kept_spans}）")
    print(f"   示例:")
    for l in lines[1:4]:
        print(f"   {l[:130]}")
    # 验证关键业务信号在保留 span 中
    full = "\n".join(lines)
    for kw in ["order_status", "assign_type", "reqApolloAnalysisField", "order_type",
               "cheat", "getOrderInfo"]:
        print(f"   含 {kw}: {kw in full}")


if __name__ == "__main__":
    main()
