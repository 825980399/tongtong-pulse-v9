# -*- coding: utf-8 -*-
"""未解析调用落盘（★318批刀0 建立 / ★401批刀3 扩为「全量清单」）。

产物（同一目录 `data/call_graph/`）：

  * `unresolved_samples_<ts>.jsonl` —— 每次 analyze 一份快照，**滚动保留 7 份**（318 规格不变）；
  * `unresolved_full_YYYYMMDD.jsonl` —— ★401刀3 新增：**当日全量清单**，当天覆盖写，
    保留最近 `_KEEP_DAYS` 个自然日，用于「解析率回归」逐条对表。

行结构（首行是 meta，其余每行一条）：

    {"cat", "parts", "module", "file", "line", "call_expr", "in_class", "caller"}

`file:line:call_expr` 是**回归三元组**：同一行号、同一调用表达式在不同批次之间必须能对齐，
否则「上一批解析得出、这一批解析不出」这种退化看不见（318 只有 file + caller，缺行号）。

★「全量」的诚实边界
--------------------
内存侧仍有 `_M318_MAX_SAMPLES` 硬上限（30 万条，防长跑进程无界吃内存）。**截断与否写进首行
meta**（`{"_meta": {"truncated": bool, ...}}`），消费方必须先看 meta 再读行 —— 不许把
「样本」当「全量」用（铁律166：没测到 ≠ 是 0）。

**只观测**：清单不参与任何判据、不进 issues、不影响入队；用途是给 R1（resolve 率攻坚）
与批间回归提供靶子。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("UnresolvedSamples")

#: 快照滚动保留份数（318 规格，勿改）
_KEEP = 7
#: ★401刀3：全量清单按**自然日**保留的天数
_KEEP_DAYS = 7
_PREFIX = "unresolved_samples_"
#: ★401刀3：当日全量清单前缀（`unresolved_full_YYYYMMDD.jsonl`）
_FULL_PREFIX = "unresolved_full_"
_SCHEMA = "unresolved_full/v1"
#: 回归三元组（缺哪个补 None，**绝不填 0**）
_TRIPLE = ("file", "line", "call_expr")


def _dir_of(project_root: str | None = None) -> str:
    return os.path.join(project_root or os.getcwd(), "data", "call_graph")


def _normalize(row: Any) -> dict:
    """把一条未解析记录补成**稳定三元组**（`file` / `line` / `call_expr`）。

    补齐规则（不编造）：
      * `line` 缺失 ⇒ `None`（不是 0：0 是"真在第 0 行"，没有这种行号）；
      * `call_expr` 缺失 ⇒ 由 `parts` 点号拼接（如 `self.lock.acquire`）；
        `parts` 也为空（动态派发 `d["k"]()` / `f()()`）⇒ `"<dynamic:...>"` 标记，不留空串。
    """
    _r = dict(row) if isinstance(row, dict) else {"cat": "?", "raw": str(row)}
    if "line" not in _r:
        _r["line"] = None
    if not _r.get("call_expr"):
        _parts = [str(_p) for _p in (_r.get("parts") or [])]
        if _parts:
            _r["call_expr"] = ".".join(_parts)
        else:
            _r["call_expr"] = "<dynamic:%s>" % (_r.get("kind") or "?")
    return _r


def _write_jsonl(path: str, rows: list, meta: dict | None = None) -> None:
    """原子写（tmp + `os.replace`），行尾固定 `\\n`；`meta` 非空时写首行。"""
    _tmp = path + ".tmp"
    with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
        if isinstance(meta, dict):
            _f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
        for _s in rows:
            _f.write(json.dumps(_s, ensure_ascii=False) + "\n")
    os.replace(_tmp, path)


def dump_samples(samples: list, project_root: str | None = None,
                 cap: int | None = None) -> str:
    """落盘：快照（滚动 7 份）+ 当日全量清单（保留 7 天）。

    Args:
        samples: 未解析记录列表。
        project_root: 项目根（缺省 cwd）。
        cap: 采集侧硬上限（`_M318_MAX_SAMPLES`）。传了就写进 meta 的 `truncated`。

    Returns:
        **快照**路径（兼容 318 调用方 `unresolved_samples_<ts>.jsonl`）。
    """
    _d = _dir_of(project_root)
    os.makedirs(_d, exist_ok=True)
    _rows = [_normalize(_s) for _s in (samples or [])]
    _now = time.time()
    _lt = time.localtime(_now)
    _p = os.path.join(_d, "%s%s.jsonl" % (_PREFIX, time.strftime("%Y%m%d_%H%M%S", _lt)))
    _write_jsonl(_p, _rows)
    _cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else 0
    _meta = {
        "_schema": _SCHEMA,
        "dumped_at": _now,
        "rows": len(_rows),
        "cap": _cap or None,
        # len>=cap ⇒ **可能**被截断（保守判定：宁可标"不全"，不许把样本冒充全量）
        "truncated": bool(_cap and len(_rows) >= _cap),
        "triple": list(_TRIPLE),
    }
    _fd = os.path.join(_d, "%s%s.jsonl" % (_FULL_PREFIX, time.strftime("%Y%m%d", _lt)))
    _write_jsonl(_fd, _rows, meta=_meta)
    _roll(_d)
    _roll_days(_d)
    return _p


def _roll(_d: str) -> None:
    """滚动清理：只保留最新 `_KEEP` 份**快照**（318 规格，勿改）。"""
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


def _roll_days(_d: str, keep: int | None = None) -> list:
    """★401刀3：全量清单按**自然日**保留 `keep` 天（默认 `_KEEP_DAYS`=7）。

    为什么按天而不是按份：312/318 的"7 份"是**按小时**落盘窗口（约 7 小时），
    拿来当"保留 7 天用于回归"是两回事 —— 夜里跑完三小时，前一天的靶子就没了。

    Returns: 被删文件名列表（供测试/报告复算）。
    """
    _k = keep if isinstance(keep, int) and not isinstance(keep, bool) and keep > 0 else _KEEP_DAYS
    try:
        _today = time.localtime()
        _mid = time.mktime((_today.tm_year, _today.tm_mon, _today.tm_mday,
                            0, 0, 0, 0, 0, -1))
        _cut_day = time.strftime("%Y%m%d", time.localtime(_mid - (_k - 1) * 86400))
        _cut = time.mktime(time.strptime(_cut_day, "%Y%m%d"))
    except (ValueError, OverflowError) as _e:
        _module_logger.warning("[401刀3] 保留窗口计算失败，跳过日滚动: %s", _e)
        return []
    try:
        _fs = sorted(f for f in os.listdir(_d)
                     if f.startswith(_FULL_PREFIX) and f.endswith(".jsonl"))
    except OSError as _e:
        _module_logger.warning("[401刀3] 清单目录不可读，跳过日滚动清理: %s", _e)
        return []
    _removed: list = []
    for _f in _fs:
        _stamp = _f[len(_FULL_PREFIX):-len(".jsonl")]
        try:
            _ts = time.mktime(time.strptime(_stamp, "%Y%m%d"))
        except ValueError as _e:
            _module_logger.warning("[401刀3] 清单文件名非日期格式，跳过（不删）: %s (%s)",
                                   _f, _e)
            continue
        if _ts < _cut:
            try:
                os.remove(os.path.join(_d, _f))
                _removed.append(_f)
            except OSError as _e:
                _module_logger.warning("[401刀3] 过期清单清理失败（不影响本次落盘）: %s", _e)
    return _removed


def _latest_path(_d: str, _prefix: str) -> str:
    """目录里以 `_prefix` 开头的最新一份 .jsonl（取不到返回空串）。"""
    try:
        _fs = sorted(f for f in os.listdir(_d)
                     if f.startswith(_prefix) and f.endswith(".jsonl"))
    except OSError as _e:
        _module_logger.warning("[318刀0][R3] 样本目录不可读: %s", _e)
        return ""
    return os.path.join(_d, _fs[-1]) if _fs else ""


def latest_samples(project_root: str | None = None) -> list[dict[str, Any]]:
    """读最新一份清单（**优先当日全量清单**，无则回退到小时快照）。只读。"""
    _d = _dir_of(project_root)
    _p = _latest_path(_d, _FULL_PREFIX) or _latest_path(_d, _PREFIX)
    if not _p:
        return []
    _rows: list[dict[str, Any]] = []
    with open(_p, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line:
                continue
            try:
                _o = json.loads(_line)
            except ValueError as _e:
                _module_logger.warning("[318刀0][R3] 样本行解析失败已跳过: %s", _e)
                continue
            if isinstance(_o, dict) and "_meta" in _o:
                continue                      # ★401刀3：首行 meta 不是样本
            _rows.append(_o)
    return _rows


def latest_meta(project_root: str | None = None) -> dict[str, Any]:
    """★401刀3：读最新清单的**首行 meta**（判「是全量还是被截断」）。取不到返回 `{}`。

    为什么单独给一个函数：`truncated` 这句话只写在文件里，没人读就等于没写；
    报告/验收要能**不问人**地把它读出来。
    """
    _d = _dir_of(project_root)
    _p = _latest_path(_d, _FULL_PREFIX)
    if not _p:
        return {}
    try:
        with open(_p, encoding="utf-8") as _f:
            _first = _f.readline().strip()
    except OSError as _e:
        _module_logger.warning("[401刀3] 清单 meta 读取失败: %s", _e)
        return {}
    if not _first:
        return {}
    try:
        _o = json.loads(_first)
    except ValueError as _e:
        _module_logger.warning("[401刀3] 清单 meta 解析失败: %s", _e)
        return {}
    if isinstance(_o, dict) and isinstance(_o.get("_meta"), dict):
        _m = dict(_o["_meta"])
        _m["path"] = _p
        return _m
    return {}


def count_by_cat(rows: list | None = None,
                 project_root: str | None = None) -> dict[str, int]:
    """按类别计数（与烛微 §1-维度1 对表用）。"""
    _rows = rows if rows is not None else latest_samples(project_root)
    _c: dict[str, int] = {}
    for _r in _rows:
        _k = str(_r.get("cat", "?") or "?")
        _c[_k] = _c.get(_k, 0) + 1
    return _c
