#!/usr/bin/env python3
"""生成比赛文档的 Excalidraw 手绘风格配图（.excalidraw JSON v2）。

用法: python3 make_excalidraw_diagrams.py
输出: docs/competition-diagrams/ 下 4 个 .excalidraw 文件（可用 excalidraw.com / Obsidian Excalidraw 插件打开，导出 PNG/SVG）。
"""
import json
import os
import random
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "competition-diagrams")
os.makedirs(OUT, exist_ok=True)

_STROKE = "#1e1e1e"
_ACCENT = "#e03131"
_BLUE = "#1971c2"
_GREEN = "#2f9e44"
_ORANGE = "#e8590c"
_GRAY = "#868e96"


def _el(eid, etype, x, y, w, h, **kw):
    """构造 excalidraw 元素（手绘风格 roughness=1）。"""
    base = {
        "id": eid,
        "type": etype,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "angle": 0,
        "strokeColor": kw.get("stroke", _STROKE),
        "backgroundColor": kw.get("fill", "transparent"),
        "fillStyle": "hachure",
        "strokeWidth": kw.get("sw", 1),
        "strokeStyle": "solid",
        "roughness": kw.get("roughness", 1),
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": {"type": 3} if etype in ("rectangle", "diamond") else None,
        "seed": kw.get("seed", random.randint(10000, 99999)),
        "version": 1,
        "versionNonce": random.randint(10**8, 10**9 - 1),
        "isDeleted": False,
        "boundElements": None,
        "updated": int(time.time() * 1000),
        "link": None,
        "locked": False,
    }
    if etype == "text":
        base["text"] = kw.get("text", "")
        base["fontSize"] = kw.get("fontSize", 16)
        base["fontFamily"] = 1
        base["textAlign"] = "left"
        base["verticalAlign"] = "top"
        base["containerId"] = None
        base["originalText"] = base["text"]
        base["lineHeight"] = 1.25
    elif etype == "arrow":
        base["points"] = kw.get("points", [[0, 0], [1, 1]])
        base["startBinding"] = None
        base["endBinding"] = None
        base["lastCommittedPoint"] = None
        base["startArrowhead"] = None
        base["endArrowhead"] = "arrow"
        base["elbowed"] = False
    elif etype == "line":
        base["points"] = kw.get("points", [[0, 0], [1, 1]])
    return base


def _text(eid, x, y, text, size=16, color=_STROKE, w=None):
    """文本元素（w 估算：中文约 1em/字）。"""
    w = w or (len(text) * size + 10)
    return _el(eid, "text", x, y, w, size + 6, text=text, fontSize=size, stroke=color)


def _box(eid, x, y, w, h, fill="transparent", stroke=_STROKE):
    return _el(eid, "rectangle", x, y, w, h, fill=fill, stroke=stroke)


def _arrow(eid, x1, y1, x2, y2, color=_STROKE, label=None, label_x=0, label_y=0):
    """箭头 + 可选标签。"""
    pts = [[0, 0], [x2 - x1, y2 - y1]]
    arr = _el(eid, "arrow", x1, y1, abs(x2 - x1), abs(y2 - y1),
              points=pts, stroke=color)
    elems = [arr]
    if label:
        elems.append(_text(f"{eid}_lbl", x1 + label_x, y1 + label_y, label, 13, _GRAY))
    return elems


def _save(name, elements):
    doc = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {
            "gridSize": None,
            "viewBackgroundColor": "#ffffff",
        },
        "files": {},
    }
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"✅ {name} ({len(elements)} 元素)")


# ─────────────────────────────────────────────────────────
# 图 1：架构总览（flowchart）
# ─────────────────────────────────────────────────────────
def diagram_architecture():
    els = []
    # 节点（x, y, w, h, 颜色）
    nodes = [
        ("a_input", 0, 180, 170, 60, _GRAY, "告警/工单输入\n问题描述"),
        ("b_log", 260, 0, 180, 70, _BLUE, "L1 日志压缩\n信号保真+分级+去重"),
        ("c_code", 260, 140, 180, 70, _BLUE, "L2 代码压缩\nAST 轻量压缩"),
        ("d_multi", 260, 280, 180, 70, _BLUE, "L3 多源融合\n日志+追踪+指标"),
        ("e_view", 530, 0, 180, 70, _GREEN, "分析视图\n10万行→数百行"),
        ("f_code", 530, 140, 180, 70, _GREEN, "代码上下文\n类/方法/异常签名"),
        ("g_view2", 530, 280, 180, 70, _GREEN, "多源分析视图"),
        ("h_llm", 800, 150, 180, 70, _ACCENT, "L5 LLM 定位\nOpenAI兼容·温度0"),
        ("i_short", 1070, 40, 180, 70, _ORANGE, "短路求值\n跳过代码定位·省token"),
        ("j_guard", 1070, 230, 180, 80, _ORANGE, "L6 推理护栏\n置信度+七节+errno"),
        ("k_report", 1340, 150, 180, 70, _GREEN, "根因报告\n证据链+置信度"),
        ("l_ws", 1610, 150, 170, 60, _GRAY, "工单预诊断\n告警附注"),
    ]
    for eid, x, y, w, h, fill, label in nodes:
        els.append(_box(eid, x, y, w, h, fill="#ffffff", stroke=fill))
        lines = label.split("\n")
        for i, ln in enumerate(lines):
            els.append(_text(f"{eid}_t{i}", x + 12, y + 14 + i * 22, ln, 14, _STROKE))
    # 箭头
    els += _arrow("ar1", 170, 210, 260, 40)      # A→B
    els += _arrow("ar2", 170, 210, 260, 175)     # A→C
    els += _arrow("ar3", 170, 210, 260, 315)     # A→D
    els += _arrow("ar4", 440, 35, 530, 35)       # B→E
    els += _arrow("ar5", 440, 175, 530, 175)     # C→F
    els += _arrow("ar6", 440, 315, 530, 315)     # D→G
    els += _arrow("ar7", 710, 35, 800, 165)      # E→H
    els += _arrow("ar8", 710, 175, 800, 175)     # F→H
    els += _arrow("ar9", 710, 315, 800, 205)     # G→H
    els += _arrow("ar10", 890, 150, 1070, 80, label="是", label_x=20, label_y=-15)   # H→I
    els += _arrow("ar11", 890, 220, 1070, 270, label="否", label_x=20, label_y=-10)  # H→J
    els += _arrow("ar12", 1250, 75, 1340, 160)   # I→K
    els += _arrow("ar13", 1250, 270, 1340, 190)  # J→K
    els += _arrow("ar14", 1520, 185, 1610, 180)  # K→L
    _save("01-架构总览.excalidraw", els)


# ─────────────────────────────────────────────────────────
# 图 2：定位时序（sequenceDiagram）
# ─────────────────────────────────────────────────────────
def diagram_sequence():
    els = []
    parts = [("U", 0, "告警系统"), ("C", 340, "压缩引擎 L1-L4"), ("L", 680, "LLM 定位 L5"), ("R", 1020, "工单/监控系统")]
    for px, (pid, x, name) in enumerate(parts):
        els.append(_box(f"{pid}_box", x, 0, 150, 40, stroke=_BLUE))
        els.append(_text(f"{pid}_t", x + 8, 10, name, 14))
        els.append(_el(f"{pid}_line", "line", x + 75, 40, 0, 760, points=[[0, 0], [0, 760]], stroke=_GRAY, sw=1))
    y = 90
    msgs = [
        ("U", "C", "告警消息 + 10万行日志 + 代码目录", 0),
        ("C", "C", "信号保真压缩（毫秒级·零LLM）", 1),
        ("C", "L", "压缩视图（数百行信号·服务级错误分布）", 0),
        ("L", "L", "根因推理（温度0·证据链约束）", 1),
    ]
    for src, dst, label, solid in msgs:
        sx = parts[[p[0] for p in parts].index(src)][1] + 75
        dx = parts[[p[0] for p in parts].index(dst)][1] + 75
        y += 55
        els.append(_text(f"m{y}_lbl", sx + 12, y - 8, label, 13))
        if src == dst:
            els.append(_el(f"m{y}", "arrow", sx + 8, y, 40, 30, points=[[0, 0], [0, 30], [40, 30]], stroke=_STROKE))
        else:
            els.append(_el(f"m{y}", "arrow", sx, y, abs(dx - sx) - 2, 16, points=[[0, 0], [dx - sx, 16]], stroke=_STROKE))
        y += 8
    # alt 分支
    y += 25
    els.append(_text("alt_t", 40, y, "alt：错误信号直接命中（如 RedisTimeoutException）", 13, _ORANGE))
    els.append(_arrow("al1", 75, y + 40, 755, y + 40, label="短路输出·秒级", label_x=100, label_y=-18))
    y += 75
    els.append(_text("else_t", 40, y, "else：需代码级定位（请求压缩代码 → 完整七节报告）", 13, _ORANGE))
    els.append(_arrow("al2", 755, y + 30, 75, y + 30, label="代码上下文", label_x=300, label_y=-18))
    els.append(_arrow("al3", 755, y + 60, 1095, y + 60, label="完整根因报告", label_x=200, label_y=-18))
    y += 90
    els.append(_arrow("al4", 1095, y, 75, y, label="工单自动附根因预诊断", label_x=250, label_y=-18))
    _save("02-定位时序.excalidraw", els)


# ─────────────────────────────────────────────────────────
# 图 3：实施计划（gantt）
# ─────────────────────────────────────────────────────────
def diagram_gantt():
    els = []
    # 表头
    els.append(_text("g_head1", 0, 0, "任务", 15, _STROKE, w=140))
    weeks = ["第1周", "第2周"]
    for i, w in enumerate(weeks):
        els.append(_text(f"wk{i}", 220 + i * 190, 0, w, 14, _GRAY, w=120))
    els.append(_el("g_axis", "line", 200, 30, 400, 0, points=[[0, 0], [400, 0]], stroke=_GRAY))
    tasks = [
        ("Demo 场景搭建", 220, 50, 3, 3, _GREEN, True),
        ("竞品对比表生成", 220, 100, 2, 3, _GREEN, True),
        ("真实业务数据接入", 220, 150, 4, 3, _BLUE, True),
        ("方案适配与调优", 220, 200, 3, 3, _BLUE, False),
    ]
    for tid, (name, x0, y, days, gap, color, done) in enumerate(tasks):
        els.append(_text(f"t{tid}_n", 0, y + 8, name, 14))
        w = days * 55 - 15
        els.append(_box(f"t{tid}", x0, y, w, 40, fill="#ffffff", stroke=color))
        state = "✅ 完成" if done else "⏳ 进行中" if tid == 2 else "待启动"
        els.append(_text(f"t{tid}_s", x0 + w + 10, y + 12, state, 12, _GRAY))
    _save("03-实施计划.excalidraw", els)


# ─────────────────────────────────────────────────────────
# 图 4：压缩率对比（柱状图）
# ─────────────────────────────────────────────────────────
def diagram_bars():
    els = []
    els.append(_text("b_title", 0, 0, "各方案 token 压缩率对比（LogDx-CI 实测）", 16, _STROKE))
    # 坐标轴
    els.append(_el("b_axis_x", "line", 60, 340, 700, 0, points=[[0, 0], [700, 0]], stroke=_STROKE))
    els.append(_el("b_axis_y", "line", 60, 40, 0, 300, points=[[0, 0], [0, 300]], stroke=_STROKE))
    for pct, y in [(0, 340), (25, 265), (50, 190), (75, 115), (100, 40)]:
        els.append(_text(f"b_yl{pct}", 20, y - 8, f"{pct}%", 12, _GRAY))
        els.append(_el(f"b_g{pct}", "line", 60, y, 700, 0, points=[[0, 0], [700, 0]], stroke="#dee2e6", sw=1))
    data = [("raw 全量", 0, _GRAY), ("headroom", 5.2, _ORANGE), ("drain3", 84.3, _BLUE),
            ("grep", 96.9, _GREEN), ("ours", 94.9, _ACCENT)]
    bw, gap = 90, 45
    for i, (name, pct, color) in enumerate(data):
        x = 100 + i * (bw + gap)
        h = int(pct * 2.6)
        if h > 4:
            els.append(_box(f"b_{i}", x, 340 - h, bw, h, fill="#ffffff", stroke=color))
            els.append(_text(f"b_{i}_v", x + bw / 2 - 22, 340 - h - 22, f"{pct}%", 14, color))
        els.append(_text(f"b_{i}_n", x - 8, 348, name, 13, _STROKE))
    els.append(_text("b_note", 100, 380, "ours 94.9%（默认档）· 最优档 95.28% · 指标类场景可达 99.98%", 12, _GRAY))
    _save("04-压缩率对比.excalidraw", els)


# ─────────────────────────────────────────────────────────
# 图 5：评测结果对比（ours vs 最佳竞品，7 维度条形图）
# ─────────────────────────────────────────────────────────
def diagram_eval_compare():
    els = []
    els.append(_text("e_title", 0, 0, "各维度定位准确率：ours vs 最佳竞品（实测）", 16, _STROKE))
    els.append(_el("e_axis_x", "line", 60, 400, 760, 0, points=[[0, 0], [760, 0]], stroke=_STROKE))
    els.append(_el("e_axis_y", "line", 60, 40, 0, 360, points=[[0, 0], [0, 360]], stroke=_STROKE))
    for pct, y in [(0, 400), (25, 310), (50, 220), (75, 130), (100, 40)]:
        els.append(_text(f"e_yl{pct}", 20, y - 8, f"{pct}%", 12, _GRAY))
        els.append(_el(f"e_g{pct}", "line", 60, y, 760, 0, points=[[0, 0], [760, 0]], stroke="#dee2e6", sw=1))
    dims = [
        ("re1ob", 94.4, 73.6, "baro"), ("re1ss", 96.8, 60.8, "nsigma"), ("re2ob", 100.0, 78.9, "nsigma"),
        ("re2ss", 92.2, 85.6, "nsigma"), ("RE3", 95.6, 93.3, "tail"), ("LogDx-CI", 93.0, 84.1, "grep"),
        ("自建", 87.5, 75.0, "drain"),
    ]
    bw, gap = 55, 45
    for i, (name, ours, comp, comp_name) in enumerate(dims):
        x = 75 + i * (2 * bw + gap + 8)
        h_ours = int(ours * 3.4)
        h_comp = max(int(comp * 3.4), 5)
        els.append(_box(f"e_{i}_c", x, 400 - h_comp, bw, h_comp, fill="#ffffff", stroke=_GRAY))
        els.append(_text(f"e_{i}_cv", x + 6, 400 - h_comp - 20, f"{comp}%", 11, _GRAY))
        els.append(_box(f"e_{i}_o", x + bw + 6, 400 - h_ours, bw, h_ours, fill="#ffffff", stroke=_ACCENT))
        els.append(_text(f"e_{i}_ov", x + bw + 10, 400 - h_ours - 20, f"{ours}%", 11, _ACCENT))
        els.append(_text(f"e_{i}_n", x - 6, 408, name, 12, _STROKE))
        els.append(_text(f"e_{i}_cn", x + 4, 422, comp_name, 9, _GRAY))
    els.append(_text("e_leg1", 60, 442, "■ ours", 12, _ACCENT))
    els.append(_text("e_leg2", 140, 442, "■ 最佳竞品（灰色为竞品名）", 12, _GRAY))
    els.append(_text("e_note", 60, 462, "数据来源：RCAEval 565 case + LogDx-CI 35 case 全量实测，官方 baseline 本地复现", 11, _GRAY))
    _save("05-评测对比.excalidraw", els)


# ─────────────────────────────────────────────────────────
# 图 6：业务价值三场景
# ─────────────────────────────────────────────────────────
def diagram_business():
    els = []
    els.append(_text("biz_title", 0, 0, "业务价值落地场景", 16, _STROKE))
    scenes = [
        ("工单自动预诊断", "用户报障工单自动附带\n根因预诊断（类型/服务/\n证据行/置信度），一线\n客服无需等研发排查", _BLUE),
        ("告警辅助定位", "监控告警自动拉起\n日志+追踪+指标三源分析，\n输出根因链与修复建议，\n减少跨团队沟通", _GREEN),
        ("知识沉淀与赋能", "每次故障的信号视图+根因\n自动沉淀为可检索知识库\n（信号→根因模式映射），\n新人快速对齐资深经验", _ORANGE),
    ]
    for i, (title, desc, color) in enumerate(scenes):
        x = i * 320
        els.append(_box(f"biz_{i}", x, 50, 280, 40, fill="#ffffff", stroke=color))
        els.append(_text(f"biz_{i}_t", x + 12, 60, title, 15, _STROKE))
        lines = desc.split("\n")
        for j, ln in enumerate(lines):
            els.append(_text(f"biz_{i}_d{j}", x + 12, 110 + j * 24, ln, 13, _GRAY))
        if i < 2:
            els.append(_arrow(f"biz_{i}_ar", x + 280, 70, x + 320, 70))
    els.append(_text("biz_out", 320, 240, "↓ 重复排查工作量 -50% · MTTR 小时级 → 分钟级 · 经验可复制", 14, _ACCENT))
    _save("06-业务价值.excalidraw", els)


if __name__ == "__main__":
    random.seed(20260810)
    diagram_architecture()
    diagram_sequence()
    diagram_bars()
    diagram_eval_compare()
    diagram_business()
    print(f"\n全部生成到: {OUT}/")