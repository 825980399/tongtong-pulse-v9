# -*- coding: utf-8 -*-
"""186批刀3 隔离单测：UP031 慢排第一批（% 格式化 -> f-string，47 处）。

验收：
  - 3 文件 47 处 UP031 全部修复（ruff 复检 0）；
  - 模块可正常导入（f-string 语法无误）；
  - 转换规则与 Python `%` 运算符逐字节等价（覆盖 %-14s / %.2f / %.1f%% / %4d / %5d / %-5s / %d / %s）。
"""
from __future__ import annotations

import re
import subprocess

import tools.m102_data_governance  # noqa: F401  (导入即编译校验)
import tools.serp_pollution_analyzer  # noqa: F401
import tools.serp_quality_report  # noqa: F401

FILES = [
    "tools/m102_data_governance.py",
    "tools/serp_pollution_analyzer.py",
    "tools/serp_quality_report.py",
]

RUFF = "D:/Program Files/Python312/Scripts/ruff.exe"


def _build_fmt(spec: str, typechar: str) -> str:
    m = re.match(r"([-+ 0#]*)(\d*)(?:\.(\d+))?", spec)
    flags = set(m.group(1)) if m else set()
    width = m.group(2) if m and m.group(2) else ""
    prec = m.group(3) if m else None
    if typechar == "s":
        return (("<" if "-" in flags else ">") + width) if width else ""
    if typechar in "diu":
        if "0" in flags and width:
            return f"0{width}"
        return (f">{width}") if width else ""
    if typechar in "fegEFG":
        w = width or ""
        p = f".{prec}" if prec is not None else ""
        return f"{w}{p}{typechar.lower()}"
    if typechar == "r":
        return "!r"
    if typechar == "a":
        return "!a"
    if typechar in "xXo":
        return ":" + typechar
    if typechar == "c":
        return "!s"
    return ""


def _fmt_to_fstring_inner(fmt: str, expr_for) -> str:
    out = []
    i = 0
    n = len(fmt)
    idx = 0
    while i < n:
        c = fmt[i]
        if c == "%":
            if i + 1 < n and fmt[i + 1] == "%":
                out.append("%")
                i += 2
                continue
            j = i + 1
            spec_start = j
            while j < n and fmt[j] not in "diouxXeEfFgGcrsa%":
                j += 1
            spec = fmt[spec_start:j]
            typechar = fmt[j]
            j += 1
            fpart = _build_fmt(spec, typechar)
            sep = "" if fpart.startswith("!") else ":"
            out.append("{" + expr_for(idx) + sep + fpart + "}")
            idx += 1
            i = j
        else:
            out.append("{{" if c == "{" else ("}}" if c == "}" else c))
            i += 1
    return "".join(out)


def _sample(typechar: str):
    if typechar in "diuxXo":
        return 42
    if typechar in "fegEFG":
        return 3.14159
    return "ABC"


def test_imports_ok():
    # 导入已在模块顶部完成；若 f-string 有语法错误，导入即失败
    assert tools.m102_data_governance is not None
    assert tools.serp_pollution_analyzer is not None
    assert tools.serp_quality_report is not None


def test_no_up031_remaining():
    for f in FILES:
        out = subprocess.run([RUFF, "check", "--select", "UP031",
                              "--output-format=concise", f],
                             capture_output=True, text=True).stdout
        assert "UP031" not in out, f"{f} 仍有 UP031：\n{out}"


def test_conversion_equivalence():
    """转换器规则与 Python `%` 运算符逐字节等价（代表性格式说明符）。"""
    cases = [
        "%-14s", "%5d", "%4d", "%.2f", "%.1f", "%.3f", "%.4f", "%-5s",
        "%d", "%s", "%.1f%%", "%s %.2f MB / %d",
    ]
    for fmt in cases:
        # 收集顺序占位
        specs = re.findall(r"%\([^)]+\)[a-z]|%[-+ 0#]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]", fmt)
        vals = []
        for sp in specs:
            tc = sp[-1]
            vals.append(_sample(tc))
        orig = fmt % tuple(vals)
        inner = _fmt_to_fstring_inner(fmt, lambda i: repr(vals[i]))
        new = eval('f"' + inner + '"')
        assert orig == new, f"不等价: {fmt!r} orig={orig!r} new={new!r}"
