"""横向对比：各开源压缩工具在同一批 case 上的效果。

对比对象（同一批 case、同一个 LLM）:
  baseline     — 原始全量输入（无压缩）
  ours         — 本方案（分层压缩）
  rtk          — RTK (68k stars) `rtk log` 去重
  tail200      — LogDx-CI 方法: 只取最后200行
  grep         — LogDx-CI 方法: 只保留 error/exception 行
  drain        — LogPare/Drain3 模板化（用 drain3 库）
  headroom     — headroom-ai (如果装了)
  logdx_hybrid — LogDx-CI 最优混合: grep + tail + rtk

每个工具输出: {"prompt": 压缩后prompt, "input_chars": 大小}
"""

import json
import os
import re
import subprocess
from pathlib import Path


def _run_tool(cmd: list[str], input_text: str = "") -> str:
    """运行外部工具，返回 stdout。"""
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True,
                           text=True, timeout=30, env={**os.environ,
                           "NO_PROXY": "localhost,127.0.0.1"})
        return r.stdout
    except Exception as e:
        return f"[{cmd[0]} error: {str(e)[:100]}]"


# ─── 各压缩器 ──────────────────────────────────────────────────

def compress_rtk(logs: list[str], code_files: list[str]) -> dict:
    """RTK: rtk log 去重 + rtk read 代码。"""
    log_text = "\n".join(logs)
    out = _run_tool(["rtk", "log"], log_text)

    code_parts = []
    for cf in code_files[:2]:
        try:
            src = Path(cf).read_text()
            code_parts.append(f"--- {Path(cf).name} ---\n{src[:4000]}")
        except Exception:
            pass

    prompt = f"{out}\n\n{' '.join(code_parts)[:4000]}"
    return {"prompt": prompt, "input_chars": len(prompt)}


def compress_tail200(logs: list[str], code_files: list[str]) -> dict:
    """LogDx-CI tail-200: 只取最后200行。"""
    log_text = "\n".join(logs[-200:])
    code_text = ""
    for cf in code_files[:2]:
        try:
            code_text += f"--- {Path(cf).name} ---\n{Path(cf).read_text()[:3000]}\n"
        except Exception:
            pass
    prompt = f"{log_text}\n\n{code_text}"
    return {"prompt": prompt, "input_chars": len(prompt)}


def compress_grep(logs: list[str], code_files: list[str]) -> dict:
    """LogDx-CI grep: 只保留 error/exception/关键行。"""
    pat = re.compile(r'(?i)(error|exception|caused by|fatal|timeout|rejected|pool exhausted|at \w+\.\w+)')
    filtered = [l for l in logs if pat.search(l)]
    log_text = "\n".join(filtered[:300])
    code_text = ""
    for cf in code_files[:2]:
        try:
            code_text += f"--- {Path(cf).name} ---\n{Path(cf).read_text()[:3000]}\n"
        except Exception:
            pass
    prompt = f"{log_text}\n\n{code_text}"
    return {"prompt": prompt, "input_chars": len(prompt)}


def compress_drain(logs: list[str], code_files: list[str]) -> dict:
    """LogPare/Drain3 模板化。"""
    try:
        from drain3 import TemplateMiner, TemplateMinerConfig
        config = TemplateMinerConfig()
        config.load("")
        config.profiling_enabled = False
        miner = TemplateMiner(config=config)
        counts = {}
        for line in logs:
            result = miner.add_log_message(line)
            if result and result["change_type"] != "none":
                t = result["template_mined"]
                counts[t] = counts.get(t, 0) + 1
        parts = []
        for t, c in sorted(counts.items(), key=lambda x: -x[1])[:100]:
            parts.append(f"[x{c}] {t}")
        log_text = "\n".join(parts)
    except Exception:
        # drain3 不可用则退回简单去重
        from common.log_compressor import compress_log, format_compressed_log
        log_text = format_compressed_log(compress_log(logs))

    code_text = ""
    for cf in code_files[:2]:
        try:
            code_text += f"--- {Path(cf).name} ---\n{Path(cf).read_text()[:3000]}\n"
        except Exception:
            pass
    prompt = f"{log_text}\n\n{code_text}"
    return {"prompt": prompt, "input_chars": len(prompt)}


def compress_headroom(logs: list[str], code_files: list[str]) -> dict:
    """headroom-ai 库压缩（如果可用）。"""
    try:
        from headroom import compress
        log_text = "\n".join(logs)
        compressed = compress([{"role": "user", "content": log_text}])
        out = ""
        if isinstance(compressed, list) and compressed:
            out = compressed[0].get("content", "")
        elif isinstance(compressed, str):
            out = compressed
        log_text = out or log_text[:3000]
    except Exception:
        log_text = "\n".join(logs[:3000])

    code_text = ""
    for cf in code_files[:2]:
        try:
            code_text += f"--- {Path(cf).name} ---\n{Path(cf).read_text()[:3000]}\n"
        except Exception:
            pass
    prompt = f"{log_text}\n\n{code_text}"
    return {"prompt": prompt, "input_chars": len(prompt)}


def compress_hybrid(logs: list[str], code_files: list[str]) -> dict:
    """LogDx-CI 最优混合: grep 错误行 + tail + 压缩。"""
    pat = re.compile(r'(?i)(error|exception|caused by|fatal|timeout|rejected)')
    filtered = [l for l in logs if pat.search(l)]
    # grep 结果 + 尾部关键行
    parts = filtered[:150]
    parts += logs[-50:]
    log_text = "\n".join(dict.fromkeys(parts))  # 去重保序
    code_text = ""
    for cf in code_files[:2]:
        try:
            code_text += f"--- {Path(cf).name} ---\n{Path(cf).read_text()[:3000]}\n"
        except Exception:
            pass
    prompt = f"{log_text}\n\n{code_text}"
    return {"prompt": prompt, "input_chars": len(prompt)}


# ─── 注册表 ────────────────────────────────────────────────────

COMPRESSORS = {
    "rtk": compress_rtk,
    "tail200": compress_tail200,
    "grep": compress_grep,
    "drain": compress_drain,
    "headroom": compress_headroom,
    "logdx_hybrid": compress_hybrid,
}


def available_compressors() -> list[str]:
    """返回可用的压缩器列表。"""
    result = []
    for name, fn in COMPRESSORS.items():
        try:
            fn(["test line\n"], [])
            result.append(name)
        except Exception:
            pass
    return result
