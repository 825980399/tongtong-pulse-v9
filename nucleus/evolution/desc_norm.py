# -*- coding: utf-8 -*-
"""desc_norm.py —— desc 动态读数归一（350 批刀6① / 刀7②）

**为什么需要这个模块**（349 刀6 的真凶，实锤）：
`code_review/run_*.json` 的 `desc` 里嵌了 `硬=59.3%/全=66.2%` 这类**覆盖率读数**。
覆盖率每算一份图就变一次（图约 61 分钟重建一份），于是同一条**不变的问题**
在每份快照里长出不同文本。按原文 desc 做 per-item 对差 ⇒ 假漂移 1217 条；
按语义归一身份对差 ⇒ 真实波动 9 条（1.34%）。

**判别原则**：判"这个 token 会不会随时间自己变"，不判"它像不像数字"。
所以：
  · `line=122`、`severity=high` 这类**结构字段**不动（它们不随时间变）；
  · `59.3%`、`heap=2295.1MB`、`2026-10-23 12:09:33` 这类**读数**必抹。

**能力边界**（铁律196，写在前面）：本模块只抹**已知**的动态模式。新型读数
（比如以后加了 `vram=`）不会被自动识别，需要补规则 —— 所以它降低假漂移，
**不能保证**消除假漂移。
"""

import re

#: 归一版本（规则变更必须升版，否则新旧口径的对差无法对表）
DESC_NORM_VERSION = "350.descnorm.v1"

#: 动态读数的占位符
PLACEHOLDER = "<动态>"

#: 已知动态模式：(标签, 正则)
#: 顺序有意义：**先抹带标签的**（信息更多），再抹裸数字（更宽松）。
DYNAMIC_PATTERNS = (
    # 带标签的覆盖率/比例读数：硬=59.3% / 全=66.2% / 覆盖率:59.3%
    ("coverage", re.compile(
        r"(?:硬|全|整|软|覆盖率|命中率|自检率|召回率|合格率)\s*[=:：]\s*"
        r"\d+(?:\.\d+)?\s*%?")),
    # 成对读数：59.3/59.4
    ("pair_reading", re.compile(r"\b\d+\.\d+\s*/\s*\d+\.\d+\b")),
    # heap / RSS 读数
    ("heap", re.compile(
        r"\b(?:heap|heap_mb|rss|rss_mb|image_mb)\s*[=:：]\s*"
        r"\d+(?:\.\d+)?\s*(?:MB|mb|GB|gb)?", re.IGNORECASE)),
    # 完整时间戳
    ("timestamp", re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")),
    ("timestamp_short", re.compile(r"\b\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b")),
    # 代次 / 进程
    ("proc_gen", re.compile(r"\bpid\d+@\d{8}-\d{6}\b")),
    ("mtime", re.compile(r"\bmtime\s*[=:：]\s*-?\d+(?:\.\d+)?")),
    # 计数值
    ("count", re.compile(
        r"\b(?:item_count|total_entries|total|count|nodes|lines|files|n)\s*[=:：]\s*\d+")),
    # 裸小数百分比（放在 count 之后，避免把 `n=12` 吃掉后又重复命中）
    ("percent", re.compile(r"\b\d+\.\d+\s*%")),
    # 裸 unix 时间戳（17xxxxxxxx.xxxx）
    ("epoch", re.compile(r"\b1[6-9]\d{8}(?:\.\d+)?\b")),
)


def desc_dynamic_hits(desc) -> list:
    """返回命中的动态读数原文（去重、保序、最多 20 条）。"""
    _s = str(desc or "")
    if not _s:
        return []
    _out: list = []
    _seen = set()
    for _label, _re in DYNAMIC_PATTERNS:
        for _m in _re.finditer(_s):
            _t = _m.group(0).strip()
            if _t and _t not in _seen:
                _seen.add(_t)
                _out.append(_t)
    return _out[:20]


def normalize_desc(desc, *, keep_first_seen: bool = True) -> dict:
    """把 desc 归一到**语义身份**（同一不变量 ⇒ 同一字符串）。

    Returns:
        `{desc, stripped, dynamic, version}` —— `stripped` 是**被抹掉的原文**，
        留着它是为了对差时还能解释"为什么这两条被判定为同一条"。
    """
    _s = str(desc or "")
    _orig = _s
    _stripped: list = []
    _seen = set()
    for _label, _re in DYNAMIC_PATTERNS:
        def _sub(m, _lbl=_label):
            _t = m.group(0)
            if _t not in _seen:
                _seen.add(_t)
                _stripped.append(_t)
            return PLACEHOLDER if keep_first_seen else ""
        _s = _re.sub(_sub, _s)
    # 归一空白：多处 placeholder 相邻时不要留一串空格（否则"条数不同"又会造成差异）
    _s = re.sub(r"(?:%s\s*)+" % re.escape(PLACEHOLDER), PLACEHOLDER, _s)
    _s = re.sub(r"\s{2,}", " ", _s).strip()
    return {"desc": _s, "stripped": _stripped,
            "dynamic": bool(_stripped), "version": DESC_NORM_VERSION,
            "changed": _s != _orig}


def semantic_identity(desc) -> str:
    """★350刀7②：per-item 对差**唯一键**（语义归一身份，禁原文 desc）。

    只取归一后文本；调用方不得再用原文 desc 做键。
    """
    return normalize_desc(desc)["desc"]
