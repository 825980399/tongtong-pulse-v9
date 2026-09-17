# -*- coding: utf-8 -*-
"""JSON 文本修复与解析失败统计（主线第22批 T1 / 技术债务 P2-119）。

【背景】
PulseStomach 消化「肺」返回的代码分析结果时，期望得到形如
    {"功能": "...", "关键步骤": "...", "依赖的外部数据": "...", "潜在风险": "..."}
的 JSON。大模型返回常带格式瑕疵（缺冒号 / 缺逗号 / 尾随逗号 / 单引号 /
字符串内未转义引号），现有「直接解析 + 正则提取」两条策略无法解析，
实测每 3-4 分钟失败 1 次，每次输出 2 条 WARNING（策略1+策略2），
淹没真实告警。

【本模块职责】
1. ``repair_json_text(text)`` -> str | None
   纯**格式**修复：修复后必须能被 ``json.loads`` 解析才返回，否则 None。
   绝不改写语义（不新增/删除键值，不改变字符串内容本身）。
2. ``parse_with_repair(text)`` -> (obj | None, method)
   三策略串联 direct -> extract -> repair；method ∈ direct/extract/repair/none。
3. ``JsonFailureTracker``
   同内容哈希去重（默认 60s 窗口，同内容只告警一次）+ 按错误类型分类计数
   + 每 N 次消化输出 1 次 INFO 摘要。

【铁律】
- 纯 stdlib（hashlib/json/re/threading/time），**零项目内依赖** →
  可被任意模块导入，无循环导入风险，可独立单测。
- 修复失败一律返回 None，由调用方保留原始内容（与改造前行为完全一致）。
- 多候选渐进式修复：每条规则产出一个候选文本，逐个 ``json.loads`` 验证，
  第一个成功的即返回。单条规则在某个样本上误伤不会波及其他候选。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any, Optional

__all__ = [
    "repair_json_text",
    "parse_with_repair",
    "extract_json_block",
    "classify_json_error",
    "JsonFailureTracker",
    "get_json_failure_tracker",
    "reset_json_failure_tracker",
]


# ============================================================
# 一、正则常量
# ============================================================

# ```json ... ``` 代码围栏
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.+?)\s*```", re.DOTALL)
# 尾随逗号：`,\n}` / `, ]`
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
# 单引号包裹的键：`{'a': ...` / `, 'a': ...`
_SQ_KEY_RE = re.compile(r"([{,]\s*)'([^'\n]{1,120})'(\s*:)")
# 单引号包裹的值：`'a': 'v',` / `'a': 'v'}`
_SQ_VAL_RE = re.compile(r"(:\s*)'([^'\n]{0,400})'(\s*[,}\]])")
# 缺逗号：值结束后紧跟下一个键
_MISSING_COMMA_RE = re.compile(
    r'([}\]"\d]|\btrue\b|\bfalse\b|\bnull\b)(\s+)(?="[^"\n]{1,120}"\s*:)'
)
# 缺冒号：`"key" value`，value 以引号/括号/数字/布尔/null 开头
_MISSING_COLON_RE = re.compile(
    r'"([^"\n]{1,120})"(\s+)(?="|\[|\{|[-\d]|true\b|false\b|null\b)'
)
# 无引号键：`{a: 1}` / `, b: 2` 中的裸标识符键（非单引号包裹）
_NO_QUOTE_KEY_RE = re.compile(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:')
# 多余的逗号（中间双/多逗号）：`,,` / `, ,` / `,,,` → 单个逗号（迭代收敛）
_MULTI_COMMA_RE = re.compile(r',\s*,+')

# 错误类型识别表（顺序敏感：先精确后宽泛）
_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("缺冒号", re.compile(r"Expecting ':' delimiter")),
    ("缺逗号", re.compile(r"Expecting ',' delimiter")),
    ("多余数据", re.compile(r"Extra data")),
    ("未终止字符串", re.compile(r"Unterminated string")),
    ("属性名无引号", re.compile(r"Expecting property name")),
    ("非法控制字符", re.compile(r"Invalid control character")),
    ("非法转义", re.compile(r"Invalid \\escape")),
    ("非法值", re.compile(r"Expecting value")),
)


# ============================================================
# 二、纯文本修复工具
# ============================================================

def _strip_code_fence(text: str) -> str:
    """剥离 markdown 代码围栏（```json ... ```）。"""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text


def extract_json_block(text: str) -> Optional[str]:
    """提取最外层大括号平衡块（字符串内的括号已做转义感知跳过）。

    Returns:
        平衡块文本；找不到 ``{`` 或括号不闭合时返回 None。
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def _single_quotes_to_double(text: str) -> str:
    """把成对的单引号字符串整体转为双引号（**状态感知**，跳过双引号内部）。

    与 ``_SQ_KEY_RE`` / ``_SQ_VAL_RE`` 的差别：本函数不要求单引号后紧跟冒号，
    因此能处理 ``{'功能' '分析代码'}`` 这类「单引号 + 缺冒号」的组合瑕疵。
    双引号字符串内部的单引号（如 ``"it's"``）原样保留，不会被误转。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_dq = False
    while i < n:
        c = text[i]
        if in_dq:
            if c == "\\" and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            out.append(c)
            if c == '"':
                in_dq = False
            i += 1
            continue
        if c == '"':
            in_dq = True
            out.append(c)
            i += 1
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:  # 未配对 → 原样保留
                out.append(c)
                i += 1
                continue
            _inner = text[i + 1:j].replace('"', '\\"')
            out.append('"' + _inner + '"')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _escape_inner_quotes(text: str) -> str:
    """转义字符串值内部的未转义引号（``"a": "he said "hi" ok"``）。

    判定规则：字符串内遇到 ``"`` 时向后看第一个非空白字符，
    若为 ``,`` ``}`` ``]`` ``:`` 或文本结尾，视为字符串结束，否则视为内部引号并转义。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        c = text[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
            continue
        # --- 处于字符串内部 ---
        if c == "\\":
            out.append(c)
            if i + 1 < n:
                out.append(text[i + 1])
            i += 2
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            nxt = text[j] if j < n else ""
            if nxt in (",", "}", "]", ":", ""):
                out.append(c)
                in_str = False
            else:
                out.append('\\"')
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _apply_structural_fixes(text: str, *,
                            trailing_comma: bool = True,
                            single_quote: bool = True,
                            single_quote_all: bool = False,
                            missing_comma: bool = False,
                            missing_colon: bool = False,
                            inner_quote: bool = False,
                            no_quote_key: bool = False,
                            multi_comma: bool = False) -> str:
    """按开关组合应用格式修复。所有规则只改格式，不增删键值。"""
    _t = text
    if trailing_comma:
        _prev = None
        while _prev != _t:  # 迭代至收敛（`[1,,]` 类多次尾逗号）
            _prev = _t
            _t = _TRAILING_COMMA_RE.sub(r"\1", _t)
    if single_quote:
        _t = _SQ_KEY_RE.sub(r'\1"\2"\3', _t)
        _t = _SQ_VAL_RE.sub(r'\1"\2"\3', _t)
    if single_quote_all:
        _t = _single_quotes_to_double(_t)
    if missing_comma:
        _t = _MISSING_COMMA_RE.sub(r"\1,\2", _t)
    if missing_colon:
        _t = _MISSING_COLON_RE.sub(r'"\1":\2', _t)
    if inner_quote:
        _t = _escape_inner_quotes(_t)
    if no_quote_key:
        # ★主线第74批 T3：无引号裸键 `{a: 1}` → `{"a": 1}`
        _t = _NO_QUOTE_KEY_RE.sub(r'\1"\2":', _t)
    if multi_comma:
        # ★主线第74批 T3：多余逗号 `{"a":1,, "b":2}` → `{"a":1, "b":2}`（迭代收敛）
        _prev = None
        while _prev != _t:
            _prev = _t
            _t = _MULTI_COMMA_RE.sub(',', _t)
    return _t


def _try_loads(text: str) -> Optional[Any]:
    """尝试解析；成功且为 dict/list 时返回对象，否则 None。"""
    try:
        _obj = json.loads(text)
    except Exception:
        return None
    return _obj if isinstance(_obj, (dict, list)) else None


def repair_json_text(text: str) -> Optional[str]:
    """尝试把**格式有瑕疵**的 JSON 文本修复为可解析文本。

    修复链（逐候选验证，返回第一个 ``json.loads`` 成功的文本）：
        1. 剥离 code fence / 提取最外层平衡块
        2. + 尾随逗号
        3. + 单引号
        4. + 缺逗号
        5. + 缺冒号
        6. + 内部引号转义
        7. 组合（缺冒号后再补缺逗号；全部规则串联）

    Args:
        text: 原始文本（可能是模型返回的一段话，其中嵌着 JSON）。

    Returns:
        可被 ``json.loads`` 解析的文本；无法修复时返回 None。
        保证**返回值非 None 时一定可解析**，调用方无需再 try。
    """
    if not text or not isinstance(text, str):
        return None

    _base = _strip_code_fence(text).strip()
    _block = extract_json_block(_base)
    _candidates_source: list[str] = []
    if _block:
        _candidates_source.append(_block)
    if _base and _base not in _candidates_source:
        _candidates_source.append(_base)

    _seen: set[str] = set()
    for _src in _candidates_source:
        # 候选按「修复力度」递增排列
        _cands = [
            _src,
            _apply_structural_fixes(_src, single_quote=False, missing_comma=False),
            _apply_structural_fixes(_src, missing_comma=False),
            _apply_structural_fixes(_src, missing_comma=True),
            _apply_structural_fixes(_src, missing_comma=True, missing_colon=True),
            # ★主线第74批 T3：无引号裸键 / 多余逗号
            _apply_structural_fixes(_src, missing_comma=True, missing_colon=True,
                                    no_quote_key=True),
            _apply_structural_fixes(_src, missing_comma=True, missing_colon=True,
                                    no_quote_key=True, multi_comma=True),
            # 单引号（粗转）+ 缺冒号 + 缺逗号：覆盖 {'k' 'v'} 组合瑕疵
            _apply_structural_fixes(_src, single_quote=False, single_quote_all=True,
                                    missing_comma=True, missing_colon=True),
            _apply_structural_fixes(_src, missing_comma=True, inner_quote=True),
            _apply_structural_fixes(_src, missing_comma=True, missing_colon=True,
                                    inner_quote=True),
            _escape_inner_quotes(_src),
        ]
        for _c in _cands:
            if not _c or _c in _seen:
                continue
            _seen.add(_c)
            if _try_loads(_c) is not None:
                return _c
    return None


def parse_with_repair(text: str) -> tuple[Optional[Any], str]:
    """三策略串联解析：direct -> extract -> repair。

    Args:
        text: 原始文本。

    Returns:
        ``(obj, method)``；``method`` ∈ ``{"direct", "extract", "repair", "none"}``。
        ``obj`` 为 None 时 method 为 ``"none"``。
    """
    if not text or not isinstance(text, str):
        return None, "none"

    _stripped = text.strip()

    # 策略1：直接解析
    if _stripped.startswith(("{", "[")):
        _obj = _try_loads(_stripped)
        if _obj is not None:
            return _obj, "direct"

    # 策略2：提取平衡块后解析
    _block = extract_json_block(text)
    if _block:
        _obj = _try_loads(_block)
        if _obj is not None:
            return _obj, "extract"

    # 策略3：格式修复后解析
    _repaired = repair_json_text(text)
    if _repaired is not None:
        _obj = _try_loads(_repaired)
        if _obj is not None:
            return _obj, "repair"

    return None, "none"


# ============================================================
# 三、失败分类与统计
# ============================================================

def classify_json_error(exc: BaseException) -> str:
    """把 JSONDecodeError 归类为人类可读的错误类型标签。"""
    _msg = str(exc)
    for _label, _pat in _ERROR_PATTERNS:
        if _pat.search(_msg):
            return _label
    if isinstance(exc, json.JSONDecodeError):
        return "其他解码错误"
    return type(exc).__name__


class JsonFailureTracker:
    """JSON 解析失败统计 + 同内容去重告警。

    - ``should_warn(content)``：同内容（md5）在 ``dedup_window`` 秒内
      只返回一次 True（即只告警一次）。
    - ``record_failure(type)`` / ``record_success()``：分类计数。
    - ``maybe_summary()``：每 ``summary_every`` 次调用返回一次摘要字符串。

    线程安全（内部 RLock）；无外部依赖，可独立单测。
    """

    def __init__(self, dedup_window_sec: float = 60.0, summary_every: int = 100) -> None:
        self._lock = threading.RLock()
        self._dedup_window = max(0.0, float(dedup_window_sec))
        self._summary_every = max(1, int(summary_every))
        self._seen: dict[str, float] = {}
        self._success = 0
        self._fail = 0
        self._by_type: dict[str, int] = {}
        self._call_count = 0

    # ---- 去重 ----
    def _gc_locked(self, now: float) -> None:
        """清理超过 2 倍窗口的陈旧哈希，防内存无界增长。"""
        _deadline = now - self._dedup_window * 2
        if len(self._seen) < 256:
            return
        for _h in [h for h, ts in self._seen.items() if ts < _deadline]:
            self._seen.pop(_h, None)

    def should_warn(self, content: str) -> bool:
        """判断该内容是否应记录 WARNING（窗口内重复内容返回 False）。"""
        _content = content or ""
        _h = hashlib.md5(_content.encode("utf-8", "ignore")).hexdigest()
        _now = time.time()
        with self._lock:
            self._gc_locked(_now)
            _last = self._seen.get(_h)
            if _last is not None and (_now - _last) < self._dedup_window:
                return False
            self._seen[_h] = _now
            return True

    def remember(self, content: str) -> None:
        """仅登记内容（不返回告警判定）。"""
        _h = hashlib.md5((content or "").encode("utf-8", "ignore")).hexdigest()
        with self._lock:
            self._seen[_h] = time.time()

    # ---- 计数 ----
    def record_success(self) -> None:
        with self._lock:
            self._success += 1

    def record_failure(self, error_type: str) -> None:
        with self._lock:
            self._fail += 1
            _k = error_type or "未知"
            self._by_type[_k] = self._by_type.get(_k, 0) + 1

    # ---- 摘要 ----
    def _format_summary_locked(self) -> str:
        _total = self._success + self._fail
        _rate = (self._success / _total * 100.0) if _total else 0.0
        _detail = "/".join(
            f"{k}{v}次"
            for k, v in sorted(self._by_type.items(), key=lambda kv: -kv[1])[:5]
        )
        return (f"JSON解析统计：共{_total}次消化，成功{self._success}次/"
                f"失败{self._fail}次（成功率{_rate:.1f}%）"
                + (f"，失败分类：{_detail}" if _detail else ""))

    def maybe_summary(self) -> Optional[str]:
        """每 ``summary_every`` 次调用返回一次摘要，其余返回 None。"""
        with self._lock:
            self._call_count += 1
            if self._call_count % self._summary_every != 0:
                return None
            return self._format_summary_locked()

    def snapshot(self) -> dict[str, Any]:
        """导出当前统计快照（只读，供测试与诊断）。"""
        with self._lock:
            return {
                "success": self._success,
                "fail": self._fail,
                "total": self._success + self._fail,
                "by_type": dict(self._by_type),
                "dedup_entries": len(self._seen),
                "call_count": self._call_count,
            }

    def reset(self) -> None:
        """清空全部状态（测试用）。"""
        with self._lock:
            self._seen.clear()
            self._success = 0
            self._fail = 0
            self._by_type.clear()
            self._call_count = 0


# ============================================================
# 四、进程级单例
# ============================================================

_TRACKER: Optional[JsonFailureTracker] = None
_TRACKER_LOCK = threading.Lock()


def get_json_failure_tracker() -> JsonFailureTracker:
    """获取进程级 JSON 失败统计单例。"""
    global _TRACKER
    if _TRACKER is None:
        with _TRACKER_LOCK:
            if _TRACKER is None:
                _TRACKER = JsonFailureTracker()
    return _TRACKER


def reset_json_failure_tracker() -> None:
    """重置单例（测试用，避免用例间互相污染）。"""
    global _TRACKER
    with _TRACKER_LOCK:
        _TRACKER = None
