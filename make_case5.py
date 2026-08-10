#!/usr/bin/env python3
"""构建真实业务 case_5（订单性质字段溯源，traceId 0ab688896a797486aa55d190d44c4102）。

- 日志：trace JSON 展开为行（保留完整调用链信息）
- 代码：从 sail2026 提取相关片段（QLE 规则执行链路）
- ground_truth：人工字段溯源结论（滴滴客服业务）
"""
import json
import os
import sys

BASE = os.path.expanduser("~/claudecodeAndHermessAndObsidian/Skills & Tools/rootcause-agent")

# 1. 日志：trace JSON → 行
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
                # 保留响应体中的关键业务字段（字段溯源输入值：assign_type/nature_name/order_id 等）
                for kf in ["assign_type", "nature_name", "order_id", "extra_type", "long_rent_type"]:
                    ht = json.dumps(h, ensure_ascii=False)
                    idx = ht.find(kf)
                    if idx >= 0:
                        log_lines.append(f"[span{i}call] 字段[{kf}]: {ht[max(0, idx-60):idx+100]}")

# 2. 代码片段（QLE 规则执行链路）
code_files = {}
paths = [
    ("BwhOrderServiceImpl.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/common/service/impl/tenant/bwh/BwhOrderServiceImpl.java",
     [(810, 840)]),
    ("InvokeStrategyLocalServiceImpl.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/service/baas/InvokeStrategyLocalServiceImpl.java",
     [(130, 170)]),
]
for name, path, ranges in paths:
    if os.path.exists(path):
        lines = open(path, encoding="utf-8").read().splitlines()
        parts = []
        for a, b in ranges:
            parts.append(f"// {name} 第 {a}-{b} 行")
            parts.extend(lines[a - 1:b])
        code_files[name] = "\n".join(parts)
    else:
        code_files[name] = f"// {name} 未找到（{path}）"

# 3. ground_truth（人工字段溯源结论）
ground_truth = {
    "root_cause": "nature_name 由 QLE 规则引擎计算：getOrderInfo 链路中 orderProxyService.getOrderInfo 返回的订单原始数据（assign_type=\"2\"，滴滴订单系统 assign_type=2 表示指派单）作为 QLE 规则输入，propertyQueryService.query(\"bwh.order\") 拉取 eternalpose 配置平台的规则配置，invokeStrategyService.invokeStrategy 执行 QLE（PropertyType.APPEND 拼接，分隔符 |），assign_type=2 命中'指派订单'标签规则，与另两条规则命中的'排队订单''特价拼车'拼接为 nature_name=|排队订单|指派订单|特价拼车",
    "evidence": [
        "代码：BwhOrderServiceImpl.saasgetOrderInfo 第 824-825 行（orderProxyService.getOrderInfo → propertyQueryService.query → invokeStrategyService.invokeStrategy）",
        "代码：InvokeStrategyLocalServiceImpl 第 143-155 行（PropertyType.APPEND 类型，QLE 返回 true 则追加 strategyManager.getName() 标签，StringUtils.join 用 | 拼接）",
        "数据：trace 中 assign_type=\"2\"（订单原始字段，QLE 规则输入）",
        "数据：响应 nature_name=\"|排队订单|指派订单|特价拼车\"（QLE 输出）",
    ],
    "confidence": "中",
    "note": "QLE 表达式原文在 eternalpose 配置平台（不随 trace 捕获），assign_type=2→指派订单 的判定规则需登录平台核实",
}

case = {
    "id": "case_5_order_nature",
    "problem": "接口 kefu/sail/workbench/order/getOrderInfo（traceId 0ab688896a797486aa55d190d44c4102，入参 order_id=70409418871768, business_type=7）返回的订单性质 nature_name 为什么包含'指派订单'？期望是纯业务订单性质，实际返回 nature_name=|排队订单|指派订单|特价拼车",
    "keywords": ["nature_name", "assign_type", "指派订单", "QLE", "getOrderInfo", "PropertyManager"],
    "logs": log_lines,
    "code_files": code_files,
    "ground_truth": ground_truth,
    "source": {"trace_id": "0ab688896a797486aa55d190d44c4102", "time_window": "2026-08-07 14:00 前后", "platform": "滴滴客服工作台订单查询"},
}

out = os.path.join(BASE, "samples", "case_5_order_nature.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(case, f, ensure_ascii=False, indent=1)
print(f"✅ 已生成 {out}")
print(f"   日志行数: {len(log_lines)}")
print(f"   代码文件: {list(code_files.keys())}")
print(f"   problem: {case['problem'][:80]}...")
