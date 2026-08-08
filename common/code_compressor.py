"""代码上下文压缩 — 借鉴 Headroom 的 AST-aware 思路。

不把整个文件喂给 LLM，而是：
1. 从异常栈/日志里的类名方法名，定位到相关文件
2. 提取方法签名 + 关键行（有异常、有日志打印、有业务判断的行）
3. 保留 import + 类定义骨架，让 LLM 理解上下文

Token 节省来源: 1000行文件 → 只喂 30-80 行相关片段
"""

import ast
import re
from pathlib import Path
from typing import Optional


def extract_method_signatures(source: str) -> list[dict]:
    """用 AST 提取类和方法签名。"""
    result = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in item.args.args]
                        result.append({
                            "kind": "method",
                            "class": node.name,
                            "name": item.name,
                            "args": args,
                            "line": item.lineno,
                            "decorators": [d.id for d in item.decorator_list if isinstance(d, ast.Name)],
                        })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                result.append({
                    "kind": "function",
                    "class": None,
                    "name": node.name,
                    "args": args,
                    "line": node.lineno,
                    "decorators": [d.id for d in node.decorator_list if isinstance(d, ast.Name)],
                })
    except SyntaxError:
        # 非 Python 文件，退化用正则
        for m in re.finditer(r'(?:def|class)\s+(\w+)\s*\(([^)]*)\)', source):
            result.append({
                "kind": "def_or_class",
                "class": None,
                "name": m.group(1),
                "args": [a.strip() for a in m.group(2).split(",") if a.strip()],
                "line": source[:m.start()].count("\n") + 1,
                "decorators": [],
            })
    return result


def compress_code(source: str, file_path: str = "",
                  keywords: Optional[list[str]] = None,
                  focus_lines: Optional[list[int]] = None,
                  max_lines: int = 120) -> str:
    """
    压缩代码文件为 LLM 友好的摘要。

    Args:
        source: 源代码全文
        file_path: 文件路径（用于头部信息）
        keywords: 定位关键词（异常类名/方法名/日志关键字）
        focus_lines: 栈中指出的行号（如 UserServiceImpl.java:42 → [42]）
        max_lines: 输出最大行数

    Returns:
        压缩后的代码文本
    """
    lines = source.split("\n")
    keywords = keywords or []
    kw_lower = [k.lower() for k in keywords]

    # 1. 提取方法签名
    signatures = extract_method_signatures(source)

    # 2. 找出关键行（关键词命中 + 栈 focus 行 + 异常/日志相关）
    key_line_idx = set()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(k in low for k in kw_lower):
            key_line_idx.add(i)
            continue
        if re.search(r'(?i)\b(error|exception|raise|log(ger)?\.\w+|print\(|return None|assert)\b', line):
            key_line_idx.add(i)

    # 栈 focus 行及其上下文
    if focus_lines:
        for fl in focus_lines:
            idx = max(0, fl - 1)
            for j in range(max(0, idx - 5), min(len(lines), idx + 6)):
                key_line_idx.add(j)

    # 3. 组装输出
    out = []
    if file_path:
        out.append(f"# File: {file_path} ({len(lines)} lines)")
    out.append("")

    # 签名表
    if signatures:
        out.append("## 方法签名")
        for s in signatures[:40]:
            deco = f" @{','.join(s['decorators'])}" if s["decorators"] else ""
            cls = f"{s['class']}." if s["class"] else ""
            out.append(f"L{s['line']}: {cls}{s['name']}({', '.join(s['args'])}){deco}")
        out.append("")

    # 关键行 + 上下文
    if key_line_idx:
        out.append("## 关键代码行")
        shown = set()
        for idx in sorted(key_line_idx)[:60]:
            for j in range(max(0, idx - 1), min(len(lines), idx + 2)):
                if j in shown:
                    continue
                shown.add(j)
                out.append(f"{j+1}: {lines[j]}")
        out.append("")

    return "\n".join(out)


def locate_file(repo_root: str, hint: str) -> Optional[Path]:
    """根据关键词在仓库里定位相关文件。"""
    root = Path(repo_root)
    if not root.exists():
        return None
    hint_lower = hint.lower()
    for p in root.rglob("*.py"):
        if hint_lower in p.name.lower():
            return p
    return None
