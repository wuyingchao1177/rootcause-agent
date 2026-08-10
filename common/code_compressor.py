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
    """用 AST 提取类和方法签名（Python）；非 Python 源码 AST 失败时返回空（调用方降级）。"""
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
                        })
    except (SyntaxError, ValueError):
        return []  # 非 Python（如 Java）→ 由 Java 签名提取降级
    return result


# Java 方法签名正则：修饰符 + 返回类型 + 方法名(参数)（支持泛型/数组/注解修饰）
_JAVA_METHOD_RE = re.compile(
    r'^\s*(?:public|private|protected|static|final|synchronized|default|@\w[\w.]*)\s+'
    r'(?:[\w<>\[\]?,\s]+\s+)?[\w<>\[\]?,\s]+[\w]\s*\([^;{}]*\)\s*(?:throws\s+[\w,.\s]+)?\s*\{?'
)
_JAVA_CLASS_RE = re.compile(r'^\s*(?:public|abstract|final)?\s*(?:class|interface|enum|@interface)\s+(\w+)')


def extract_java_signatures(source: str) -> list[dict]:
    """Java 代码的正则签名提取（不依赖 AST，零依赖）。"""
    result = []
    cur_class = ""
    for i, line in enumerate(source.split("\n")):
        cm = _JAVA_CLASS_RE.match(line)
        if cm:
            cur_class = cm.group(1)
            result.append({"kind": "class", "class": cur_class, "name": cur_class, "args": [], "line": i + 1})
            continue
        if _JAVA_METHOD_RE.match(line):
            mname = re.search(r'(\w+)\s*\(', line)
            if mname:
                result.append({
                    "kind": "method", "class": cur_class, "name": mname.group(1),
                    "args": [], "line": i + 1,
                })
    return result


def compress_code(source: str, file_path: str = "",
                  keywords: Optional[list[str]] = None,
                  focus_lines: Optional[list[int]] = None,
                  max_lines: int = 120,
                  sig_limit: int = 40,
                  key_line_limit: int = 60,
                  context_lines: int = 2,
                  import_limit: int = 25) -> str:
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

    # 1. 提取方法签名（Python AST 优先；Java/其他语言降级为正则）
    signatures = extract_method_signatures(source)
    if not signatures:
        signatures = extract_java_signatures(source)

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

    # import/依赖区（保留外部 SDK/包名 —— 是系统依赖的关键线索，如 eternalpose 配置平台）
    imports = [l for l in lines[:60] if l.strip().startswith(("import ", "from ", "package "))]
    if imports:
        out.append("## 依赖/import")
        for imp in imports[:import_limit]:
            out.append(imp.strip())
        out.append("")

    # 签名表
    if signatures:
        out.append("## 方法签名")
        for s in signatures[:sig_limit]:
            deco = f" @{','.join(s.get('decorators', []))}" if s.get("decorators") else ""
            cls = f"{s['class']}." if s["class"] else ""
            out.append(f"L{s['line']}: {cls}{s['name']}({', '.join(s['args'])}){deco}")
        out.append("")

    # 关键行 + 上下文
    if key_line_idx:
        out.append("## 关键代码行")
        shown = set()
        for idx in sorted(key_line_idx)[:key_line_limit]:
            for j in range(max(0, idx - context_lines), min(len(lines), idx + context_lines + 1)):
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
