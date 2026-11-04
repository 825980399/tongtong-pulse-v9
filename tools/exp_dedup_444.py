# -*- coding: utf-8 -*-
"""444 刀3：经验库去重（只删「内容完全相同」的重复，保留每个内容的第一条）。

为什么是去重而不是内容清洗
--------------------------
443 第 8 维测到 `experience_pool` 污染率 59.4%，444 T0 逐条看**原因分布**：

    data/experience/experience_pool.json     总量 353  污染 211  原因 100% =「重复经验（内容完全相同）」
    data/context/reasoning_experience.json   总量 129  污染  28  原因 26 重复 + 2 条含「测试/test」关键词

也就是说「污染」几乎全是**重复写入**，不是内容过时/错误。去重后每个不同内容仍保留
一条，不存在"删掉有效经验"的风险。

判据与 `ExperiencePollutionGuard.scan_library` **完全同源**（复用其 `_text_of` +
前 120 字指纹），保证「工具删掉的」与「扫描判污染的」是同一批条目。

用法::

    python tools/exp_dedup_444.py                   # dry-run，只报告
    python tools/exp_dedup_444.py --apply           # 真写（先备份 .bak_m444_dedup）
    python tools/exp_dedup_444.py --apply --drop-test-keywords   # 顺带删「测试/test」占位条
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import logging

from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.evolution.ExperiencePollutionGuard import _text_of

_logger = logging.getLogger("pulse.module.exp_dedup_444")

DEFAULT_FILES = (
    "data/experience/experience_pool.json",
    "data/context/reasoning_experience.json",
)

#: 与 `scan_library` 相同的指纹长度
_FP_LEN = 120
#: 占位/测试残留关键词（只在这两个库上判定；与 ExperiencePollutionGuard 的 detect 同名单）
_TEST_WORDS = ("测试", "test", "TEST")


def _items_of(data):
    """按 `scan_library` 同款规则取出条目列表与其容器键。"""
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        for _k in ("experiences", "items", "nodes", "records", "data"):
            if isinstance(data.get(_k), list):
                return data[_k], _k
    return None, None


def dedup_file(rel: str, apply: bool = False, drop_test_keywords: bool = False) -> dict:
    """对单个经验库去重。返回统计（不改文件，除非 ``apply=True``）。"""
    _p = os.path.join(_ROOT, rel)
    _out = {"path": rel, "exists": os.path.exists(_p), "before": 0, "after": 0,
            "dup_removed": 0, "test_removed": 0, "written": False, "error": ""}
    if not _out["exists"]:
        return _out
    _data = safe_read_json(_p, default=None)
    if _data is None:
        _out["error"] = "读取失败（safe_read_json 返回 None）"
        return _out
    _items, _key = _items_of(_data)
    if _items is None:
        _out["error"] = "未找到经验列表"
        return _out

    _st0 = os.stat(_p)
    _stamp0 = (int(_st0.st_mtime_ns), int(_st0.st_size))
    _out["before"] = len(_items)
    _seen: dict[str, int] = {}
    _kept: list = []
    for _i, _it in enumerate(_items):
        _txt = _text_of(_it) if isinstance(_it, dict) else ""
        if drop_test_keywords and isinstance(_it, dict):
            _blob = str(_it)
            if any(_w in _blob for _w in _TEST_WORDS):
                _out["test_removed"] += 1
                continue
        if _txt:
            _fp = _txt[:_FP_LEN]
            if _fp in _seen:
                _out["dup_removed"] += 1
                continue
            _seen[_fp] = _i
        _kept.append(_it)

    _out["after"] = len(_kept)
    if not apply:
        return _out

    # ★并发保护：框架进程可能在读与写之间追加条目。写前复核 mtime/size，
    #   变了就放弃（宁可少删一轮，也不拿旧快照覆盖新数据）。
    try:
        _st1 = os.stat(_p)
        if (int(_st1.st_mtime_ns), int(_st1.st_size)) != _stamp0:
            _out["error"] = "读取后有并发写入（mtime/size 变化）⇒ 放弃写盘，请稍后重跑"
            return _out
    except OSError as _e:
        _logger.warning("stat 复核失败，放弃写盘 %s: %s", _p, _e)
        _out["error"] = f"stat 复核失败，放弃写盘: {type(_e).__name__}: {_e}"
        return _out

    # 先备份（就地 .bak_m444_dedup，只保一份；已有则不覆盖 —— 保留**最早**的原始快照）
    _bak = _p + ".bak_m444_dedup"
    if not os.path.exists(_bak):
        try:
            import shutil
            shutil.copy2(_p, _bak)
            _out["backup"] = _bak
        except OSError as _e:
            _logger.warning("备份失败，放弃写盘 %s: %s", _p, _e)
            _out["error"] = f"备份失败，放弃写盘: {type(_e).__name__}: {_e}"
            return _out

    if _key is None:
        _new = _kept
    else:
        _new = dict(_data)
        _new[_key] = _kept
    _out["written"] = bool(safe_write_json(_p, _new, backup=False, indent=2))
    return _out


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="444 刀3 经验库去重")
    _ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run）")
    _ap.add_argument("--drop-test-keywords", action="store_true",
                     help="顺带删含「测试/test」的占位条（默认保留）")
    _ap.add_argument("--json", default="", help="把结果写到该路径（JSON）")
    _args = _ap.parse_args(argv)

    print("=" * 78)
    print("444 刀3 经验库去重  (%s)" % ("APPLY" if _args.apply else "dry-run"))
    print("=" * 78)
    _rows = []
    for _rel in DEFAULT_FILES:
        _r = dedup_file(_rel, apply=_args.apply,
                        drop_test_keywords=_args.drop_test_keywords)
        _rows.append(_r)
        print(f"{_rel:<44s} 前 {_r['before']:3d} → 后 {_r['after']:3d} "
              f"｜重复删 {_r['dup_removed']:3d} ｜占位删 {_r['test_removed']:2d} "
              f"｜写盘 {_r['written']}"
              + (("  ｜" + _r["error"]) if _r["error"] else ""))
    _tb = sum(r["before"] for r in _rows)
    _ta = sum(r["after"] for r in _rows)
    print("-" * 78)
    _pct = (100.0 * (_tb - _ta) / _tb) if _tb else 0.0
    print(f"合计：{_tb} → {_ta}（减少 {_tb - _ta} 条，{_pct:.1f}%）")
    if not _args.apply:
        print("（dry-run：未写盘；加 --apply 才真删）")

    if _args.json:
        import json
        with open(_args.json, "w", encoding="utf-8", newline="\n") as _f:
            json.dump({"at": time.time(), "apply": _args.apply, "rows": _rows},
                      _f, ensure_ascii=False, indent=2)
        print("结果已写:", _args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
