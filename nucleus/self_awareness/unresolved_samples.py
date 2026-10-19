# -*- coding: utf-8 -*-
"""★318批刀0（R3）：未解析调用样本落盘（滚动保留最新 7 份，与 312 图快照同规格）。

产物：`data/call_graph/unresolved_samples_<ts>.jsonl`，每行一条未解析调用样本：

    {cat, parts, module, file, in_class, caller}

`cat ∈ {self唯一, self多义, obj唯一, obj多义, external, 混合}` ——
与烛微318 §1-维度1 的构成拆解对表（A类可攻 / B类成本极低 / C类缓做）。

**只观测**：样本不参与任何判据、不进 issues、不影响入队；用途是给 R1（resolve 率
攻坚）提供**靶子**，让"解析率提升幅度"可按类别解释。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("UnresolvedSamples")

#: 滚动保留份数（与 312 图快照同规格）
_KEEP = 7
_PREFIX = "unresolved_samples_"


def _dir_of(project_root: str | None = None) -> str:
    return os.path.join(project_root or os.getcwd(), "data", "call_graph")


def dump_samples(samples: list, project_root: str | None = None) -> str:
    """落盘样本（原子写 + 滚动保留 7 份）。返回落盘路径。"""
    _d = _dir_of(project_root)
    os.makedirs(_d, exist_ok=True)
    _p = os.path.join(_d, "%s%s.jsonl" % (_PREFIX, time.strftime("%Y%m%d_%H%M%S")))
    _tmp = _p + ".tmp"
    with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
        for _s in samples:
            _f.write(json.dumps(_s, ensure_ascii=False) + "\n")
    os.replace(_tmp, _p)
    _roll(_d)
    return _p


def _roll(_d: str) -> None:
    """滚动清理：只保留最新 `_KEEP` 份。"""
    try:
        _fs = sorted(f for f in os.listdir(_d)
                     if f.startswith(_PREFIX) and f.endswith(".jsonl"))
    except OSError as _e:
        _module_logger.warning("[318刀0][R3] 样本目录不可读，跳过滚动清理: %s", _e)
        return
    for _old in _fs[:-_KEEP]:
        try:
            os.remove(os.path.join(_d, _old))
        except OSError as _e:
            _module_logger.warning("[318刀0][R3] 旧样本清理失败（不影响本次落盘）: %s", _e)


def latest_samples(project_root: str | None = None) -> list[dict[str, Any]]:
    """读最新一份样本（只读）。"""
    _d = _dir_of(project_root)
    try:
        _fs = sorted(f for f in os.listdir(_d)
                     if f.startswith(_PREFIX) and f.endswith(".jsonl"))
    except OSError as _e:
        _module_logger.warning("[318刀0][R3] 样本目录不可读: %s", _e)
        return []
    if not _fs:
        return []
    _rows: list[dict[str, Any]] = []
    with open(os.path.join(_d, _fs[-1]), encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line:
                continue
            try:
                _rows.append(json.loads(_line))
            except ValueError as _e:
                _module_logger.warning("[318刀0][R3] 样本行解析失败已跳过: %s", _e)
    return _rows


def count_by_cat(rows: list | None = None,
                 project_root: str | None = None) -> dict[str, int]:
    """按类别计数（与烛微 §1-维度1 对表用）。"""
    _rows = rows if rows is not None else latest_samples(project_root)
    _c: dict[str, int] = {}
    for _r in _rows:
        _k = str(_r.get("cat", "?") or "?")
        _c[_k] = _c.get(_k, 0) + 1
    return _c
