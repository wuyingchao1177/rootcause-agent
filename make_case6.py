#!/usr/bin/env python3
"""构建真实业务 case_6（cheat 字段溯源：反作弊接口未调用，traceId 0ab688896a797486aa55d190d44c4102）。

复用 case_5 的 trace 日志构建逻辑；新增 cheat 相关字段保真 + 代码片段。
ground_truth：人工字段溯源结论（反作弊接口未被调用，levelType==101 前置条件未满足）。
"""
import json
import os
import sys

BASE = os.path.expanduser("~/claudecodeAndHermessAndObsidian/Skills & Tools/rootcause-agent")

# 1. 日志：trace JSON → 行（与 case_5 相同 + cheat 相关字段）
trace = json.load(open("/tmp/trace_0ab688896a797486aa55d190d44c4102.txt"))
log_lines = []
log_lines.append(f"TRACE {trace['trace_id']} spans={trace['span_count_total']} window={trace['date_window']}")
for i, s in enumerate(trace["spans"]):
    log_lines.append(f"[span{i}] uri={s.get('uri')} app={s.get('appname')} status={s.get('status')} "
                     f"duration={s.get('duration')} logs={s.get('log_count')} host={s.get('host')}")
    ri = s.get("request_in")
    if ri:
        if isinstance(ri, str):
            log_lines.append(f"[span{i}req] {ri}")
        else:
            log_lines.append(f"[span{i}req] {json.dumps(ri, ensure_ascii=False)}")
    hc = s.get("http_calls")
    if hc:
        for h in hc[:5]:
            if isinstance(h, dict):
                log_lines.append(f"[span{i}call] {h.get('method','')} {h.get('url', h.get('uri',''))} status={h.get('status','')}")
                # 响应体关键业务字段（cheat/level_type/type_name 等）
                for kf in ["cheat", "level_type", "type_name", "assign_type", "nature_name",
                           "order_id", "extra_type", "long_rent_type"]:
                    ht = json.dumps(h, ensure_ascii=False)
                    idx = ht.find(kf)
                    if idx >= 0:
                        log_lines.append(f"[span{i}call] 字段[{kf}]: {ht[max(0, idx-60):idx+100]}")

# 2. 代码片段（完整文件压缩：cheat 字段定义 + 赋值条件 + 反作弊调用）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from common.code_compressor import compress_code

code_files = {}
paths = [
    ("BaseOrderVo.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/common/vo/order/BaseOrderVo.java"),
    ("BwhOrderServiceImpl.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/common/service/impl/tenant/bwh/BwhOrderServiceImpl.java"),
    ("OrderCheatInfoServiceImpl.java", "/Users/didi/IdeaProjects/sail2026/common-service/src/main/java/com/xiaoju/sail/workbench/common/service/impl/OrderCheatInfoServiceImpl.java"),
    ("BwhBaseGetOrderInfoCustomizeComponent.java", "/Users/didi/IdeaProjects/sail2026/workbench-customize/src/main/java/com/xiaoju/sail/workbench/customize/provider/order/getOrderInfo/impl/bwh/baseBusiness/BwhBaseGetOrderInfoCustomizeComponent.java"),
]
for name, path in paths:
    if os.path.exists(path):
        src = open(path, encoding="utf-8").read()
        code_files[name] = compress_code(src, file_path=name,
                                         keywords=["cheat", "checkOrderCheatInfo", "levelType", "LEVEL_TYPE_TEKUAI"])
    else:
        code_files[name] = f"// {name} 未找到（{path}）"

# 3. ground_truth（人工字段溯源结论）
ground_truth = {
    "root_cause": "cheat=false 不是因为反作弊接口判断无作弊，而是反作弊接口根本没有被调用：fillOrderType 中 checkOrderCheatInfo 有前置条件 levelType==101（LEVEL_TYPE_TEKUAI 特快单），本订单 level_type=0（实时单|普通型），条件不满足，cheat 保持 boolean 默认值 false。trace 中 is_driver_detour_fraud 与 order.cheat.check 出现 0 次佐证反作弊下游未调用",
    "evidence": [
        "代码：BaseOrderVo.java:218（boolean cheat 默认 false）",
        "代码：BwhOrderServiceImpl.java:706-714 / BwhBaseGetOrderInfoCustomizeComponent.java:197-204（fillOrderType 中 Objects.equals(LEVEL_TYPE_TEKUAI=101, levelType) 才调 checkOrderCheatInfo）",
        "代码：OrderCheatInfoServiceImpl.checkOrderCheatInfo（调 GigaFactory 配置 order.cheat.check URL，is_driver_detour_fraud==1 才返回 true）",
        "数据：trace 中 level_type=0，type_name=实时单|普通型（非特快单）",
        "数据：trace 中 is_driver_detour_fraud 出现 0 次、order.cheat.check 出现 0 次（反作弊接口未调用）",
        "数据：trace 中 \"cheat\": false（boolean 默认值）",
    ],
    "confidence": "高",
    "note": "前置条件判定链完整（levelType!=101 → 不查作弊 → 默认 false），trace 佐证完整（反作弊关键词 0 命中）",
}

case = {
    "id": "case_6_cheat_field",
    "problem": "接口 kefu/sail/workbench/order/getOrderInfo（traceId 0ab688896a797486aa55d190d44c4102，入参 order_id=70409418871768, business_type=7）返回的订单\"是否作弊嫌疑\"（cheat）字段为什么是 false？该订单是否真的被判定无作弊？",
    "keywords": ["cheat", "checkOrderCheatInfo", "levelType", "LEVEL_TYPE_TEKUAI", "is_driver_detour_fraud", "getOrderInfo"],
    "logs": log_lines,
    "code_files": code_files,
    "ground_truth": ground_truth,
    "source": {"trace_id": "0ab688896a797486aa55d190d44c4102", "time_window": "2026-08-07 14:00 前后", "platform": "滴滴客服工作台订单查询"},
}

out = os.path.join(BASE, "samples", "case_6_cheat_field.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(case, f, ensure_ascii=False, indent=1)
print(f"✅ 已生成 {out}")
print(f"   日志行数: {len(log_lines)}")
print(f"   代码文件: {list(code_files.keys())}")
print(f"   日志含 cheat: {'cheat' in chr(10).join(log_lines)}")
print(f"   日志含 level_type: {'level_type' in chr(10).join(log_lines)}")
