# -*- coding: utf-8 -*-
"""JSON 容错解析（主线第68批 T8/P2）。

【姊妹件】nucleus/parsing/JsonRepair.py。
分工：本模块修复
    「结构错配类」故障（数组里放键值对，即声明为 [ ] 却写 "key": "value"）；
    姊妹件修复「格式瑕疵类」故障（缺冒号 / 缺逗号 / 尾随逗号 / 单引号 /
    字符串内未转义引号），面向 PulseStomach 代码分析 JSON。
    两者修复**不同的故障形态**，各含独立的 repair_json_text 实现，故**不合并**，
    仅在此互相标注，避免后续误判为重复实现而错删其一。

背景
----
胃模块（``organs/body/PulseStomach.py``）解析大模型返回的代码分析 JSON 时，
约每 12 分钟出现一次 ``JSONDecodeError``。典型错误是**数组里放键值对**：

    "依赖的外部数据": [ "_xxx_enabled": "一个用于…", "_YYY": "…" ]
                      ^ 声明为数组 [ ]，里面却是 "key": "value"

★依赖现状（T0 实测）：``json5`` / ``demjson3`` **均未安装**，
  因此这里实现**零依赖**的正则修复 + 多策略解析 + 降级字段提取。

设计原则
--------
1. **不掩盖真错误**：只在标准 ``json.loads`` 失败后才进入修复策略；
2. **可回退**：任一策略失败都继续下一策略，全部失败则返回降级提取结果；
3. **可观测**：返回命中的策略名，便于统计“哪类错误最常见”。
"""
import json
import re
from typing import Any, Dict, Tuple

__all__ = [
    "strip_code_fence",
    "repair_json_text",
    "parse_json_fault_tolerant",
    "extract_key_fields",
]

# 需要降级提取的关键字段（即使 JSON 完全解析失败，也尽量保住这些知识）
KEY_FIELDS = ("功能", "关键步骤", "依赖的外部数据", "副作用", "返回值", "summary")


def strip_code_fence(text: str) -> str:
    """去掉 markdown 代码块围栏（```json ... ```），返回内部文本。"""
    if not text:
        return ""
    _m = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.S)
    if _m:
        return _m.group(1).strip()
    return text.strip()


def repair_json_text(text: str) -> str:
    """修复常见非法 JSON 格式（按风险从低到高依次应用）。

    修复项：
      ① 数组里放键值对 → 转为对象（T8 的核心错误模式）
      ② 尾随逗号      → 删除（``[1,2,3,]`` → ``[1,2,3]``）
      ③ 缺失逗号      → 补全（``} {`` / ``} "k"`` / ``] [`` 之间）
      ④ 单引号**键**  → 转双引号（值不动，避免破坏正文里的引号）
    """
    if not text:
        return ""

    # ① 数组中放键值对：匹配以 ["xxx": 或 [ "xxx" : 开头的数组块
    #    例： [ "_a": "b", "_c": "d" ]  ->  { "_a": "b", "_c": "d" ]
    #    这里用「数组起始 + 首个元素是 键: 」作为判据，避免误伤真数组。
    _out = []
    _i = 0
    _n = len(text)
    while _i < _n:
        _ch = text[_i]
        if _ch == "[":
            # 向后扫描到匹配的 ]（考虑嵌套 [] 与 {}，不深入字符串内部转义的极端情形）
            _depth = 0
            _j = _i
            _in_str = False
            _esc = False
            while _j < _n:
                _c = text[_j]
                if _in_str:
                    if _esc:
                        _esc = False
                    elif _c == "\\":
                        _esc = True
                    elif _c == '"':
                        _in_str = False
                else:
                    if _c == '"':
                        _in_str = True
                    elif _c in "[{":
                        _depth += 1
                    elif _c in "]}":
                        _depth -= 1
                        if _depth == 0 and _c == "]":
                            break
                _j += 1
            _body = text[_i + 1:_j]
            # 判据：块内存在 "xxx" : 形式（键值对），且不是 [{...}] 这种对象数组
            if re.match(r'\s*"[^"]+"\s*:', _body) and not _body.lstrip().startswith("{"):
                _out.append("{")
                _out.append(_body)
                _out.append("}" if _j < _n else "]")
                _i = _j + 1
                continue
        _out.append(_ch)
        _i += 1
    text = "".join(_out)

    # ② 尾随逗号：, 后紧跟 } 或 ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # ③ 缺失逗号：} 与 { / } 与 "键" / ] 与 [ 之间
    text = re.sub(r"([}\]])\s*(?=\s*\")", r"\1,", text)
    text = re.sub(r"\}\s*\{", "},{", text)
    text = re.sub(r"\]\s*\[", "],[", text)

    # ④ 单引号键 → 双引号键（只改键，避免破坏值里的引号）
    text = re.sub(r"'([^'\n]{1,64}?)'(\s*:)", r'"\1"\2', text)

    return text


def _try_loads(text: str) -> Tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except Exception:
        return False, None


def parse_json_fault_tolerant(text: str) -> Tuple[bool, Any, str]:
    """多策略容错解析。

    返回 ``(ok, data, strategy)``：
      - ``strategy`` 取值：``direct`` / ``fence`` / ``repaired`` / ``repaired_quotes`` / ``fallback`` / ``failed``
      - 全部失败时 ``ok=False``、``data`` 为 ``None``（调用方决定是否降级提取）
    """
    if not text:
        return False, None, "failed"

    # 策略1：直接解析（标准 JSON，最快）
    _ok, _data = _try_loads(text)
    if _ok:
        return True, _data, "direct"

    # 策略2：去掉 markdown 代码块后解析
    _fenced = strip_code_fence(text)
    _ok, _data = _try_loads(_fenced)
    if _ok:
        return True, _data, "fence"

    # 策略3：正则修复后解析
    _repaired = repair_json_text(_fenced)
    _ok, _data = _try_loads(_repaired)
    if _ok:
        return True, _data, "repaired"

    # 策略4：再放宽一层（对整个原文做修复，含单引号键处理）
    _repaired2 = repair_json_text(text)
    if _repaired2 != _repaired:
        _ok, _data = _try_loads(_repaired2)
        if _ok:
            return True, _data, "repaired_quotes"

    # 策略5：降级——正则提取关键字段（不丢弃全部内容）
    _fields = extract_key_fields(text)
    if _fields:
        return True, _fields, "fallback"

    return False, None, "failed"


def extract_key_fields(text: str) -> Dict[str, Any]:
    """JSON 完全解析失败时的**降级提取**：用正则捞出关键字段。

    支持两种形态：
      - ``"功能": "……"``           → 取字符串值
      - ``"关键步骤": ["a", "b"]``  → 取数组（尽力而为）
    """
    if not text:
        return {}
    _out: Dict[str, Any] = {}
    for _key in KEY_FIELDS:
        # 字符串值
        _m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(_key), text)
        if _m:
            _out[_key] = _m.group(1)
            continue
        # 数组值（尽力提取到匹配的 ]）
        _m2 = re.search(r'"%s"\s*:\s*\[(.*?)\]' % re.escape(_key), text, re.S)
        if _m2:
            _items = re.findall(r'"((?:[^"\\]|\\.)*)"', _m2.group(1))
            _out[_key] = _items
            continue
        # 对象值（形如 { "k": "v" ... } 或非法 [ "k": "v" ]）
        _m3 = re.search(r'"%s"\s*:\s*[\[{](.*?)[\]}]' % re.escape(_key), text, re.S)
        if _m3:
            _pairs = re.findall(r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"',
                                _m3.group(1))
            if _pairs:
                _out[_key] = dict(_pairs)
    return _out
