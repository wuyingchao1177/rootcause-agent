#!/usr/bin/env python3
"""构建真实业务 case_7（order_status_name 字段溯源：Apollo 枚举映射，traceId 0ab688896a797486aa55d190d44c4102）。

复用 case_5/case_6 的 trace 日志构建逻辑；新增 order_status 相关字段保真 + 代码片段。
ground_truth：人工字段溯源结论（order_status=5 → orderEnum 从 Apollo ark_order_config 查 key "5" → 订单完成）。
"""
import json
import os
import sys

BASE = os.path.expanduser("~/claudecodeAndHermessAndObsidian/Skills & Tools/rootcause-agent")

# 1. 日志：trace JSON → 行（含 order_status 相关字段）
trace = json.load(open("/tmp/trace_0ab688896a797486aa55d190d44c4102.txt"))
log_lines = []
log_lines.append(f"TRACE {trace['trace_id']} spans={trace['span_count_total']} window={trace['date_window']}")
for i, s in enumerate(trace["spans"]):
    log_lines.append(f"[span{i}] uri={s.get('uri')} app={s.get('appname')} status={s.get('status')} "
                     f"duration={s.get('duration')} logs={s.get('log_count')} host={s.get('host')}")
    ri = s.get("request_in")
    if ri:
        if isinstance(ri, str):
            log_lines.append(f"[span{i}req] {ri[:600]}")
        else:
            log_lines.append(f"[span{i}req] {json.dumps(ri, ensure_ascii=False)[:600]}")
    hc = s.get("http_calls")
    if hc:
        for h in hc[:5]:
            if isinstance(h, dict):
                log_lines.append(f"[span{i}call] {h.get('method','')} {h.get('url', h.get('uri',''))} status={h.get('status','')}")
                # 响应体关键业务字段（order_status/order_status_name 等）
                for kf in ["order_status", "order_status_name", "cheat", "level_type", "type_name",
                           "assign_type", "nature_name", "order_id", "extra_type", "long_rent_type"]:
                    ht = json.dumps(h, ensure_ascii=False)
                    idx = ht.find(kf)
                    if idx >= 0:
                        log_lines.append(f"[span{i}call] 字段[{kf}]: {ht[max(0, idx-60):idx+100]}")

# 2. 代码片段（完整文件压缩：orderStatusName 字段定义 + orderEnum 映射逻辑）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from common.code_compressor import compress_code

code_files = {}
paths = [
    ("OrderInfoVo.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/common/vo/order/OrderInfoVo.java"),
    ("BwhOrderServiceImpl.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/common/service/impl/tenant/bwh/BwhOrderServiceImpl.java"),
]
for name, path in paths:
    if os.path.exists(path):
        src = open(path, encoding="utf-8").read()
        code_files[name] = compress_code(src, file_path=name,
                                         keywords=["order_status", "orderEnum", "orderStatusName", "ApolloConfigUtil"])
    else:
        code_files[name] = f"// {name} 未找到（{path}）"

# 3. ground_truth（人工字段溯源结论）
ground_truth = {
    "root_cause": "order_status_name=订单完成 是因为 dos/getOrderInfo 返回订单原始数据 order_status=5，BwhOrderServiceImpl.orderEnum('5','order_status') 从 Apollo 配置 ark_module/ark_order_config 的 order_status 项取 JSON map（{'5':'订单完成',...}），按 key '5' 查到映射值'订单完成'设置到 orderStatusName。属于典型的 Apollo 枚举映射路径（路径②：TRANSFORMED）",
    "evidence": [
        "代码：OrderInfoVo.java:292-293（@JSONField(name='order_status_name') private String orderStatusName）",
        "代码：BwhOrderServiceImpl.java:869-871（order_status 非空时 orderEnum(order_status,'order_status') 赋值）",
        "代码：BwhOrderServiceImpl.java:1351-1364（orderEnum 从 Apollo ark_module/ark_order_config 的 type 项取 JSON map，按 channel 查 key）",
        "数据：trace 中 order_status=5（dos 返回的订单原始状态）",
        "数据：trace 中 order_status_name=订单完成（映射后输出值）",
    ],
    "confidence": "高",
    "note": "Apollo 配置原文（{'5':'订单完成'}）不在 trace 中（Apollo SDK 缓存读取不产生独立 HTTP span），但 orderEnum 方法逻辑明确；5->订单完成 是滴滴订单系统标准定义",
}

case = {
    "id": "case_7_order_status",
    "problem": "接口 kefu/sail/workbench/order/getOrderInfo（traceId 0ab688896a797486aa55d190d44c4102，入参 order_id=70409418871768, business_type=7）返回的订单状态 order_status_name 为什么是'订单完成'？该状态是如何映射出来的？",
    "keywords": ["order_status", "order_status_name", "orderEnum", "ApolloConfigUtil", "getOrderInfo", "ark_order_config"],
    "logs": log_lines,
    "code_files": code_files,
    "ground_truth": ground_truth,
    "source": {"trace_id": "0ab688896a797486aa55d190d44c4102", "time_window": "2026-08-07 14:00 前后", "platform": "滴滴客服工作台订单查询"},
}

out = os.path.join(BASE, "samples", "case_7_order_status.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(case, f, ensure_ascii=False, indent=1)
print(f"✅ 已生成 {out}")
print(f"   日志行数: {len(log_lines)}")
print(f"   代码文件: {list(code_files.keys())}")
raw = "\n".join(log_lines)
print(f"   日志含 order_status: {raw.count('order_status')} 处")
