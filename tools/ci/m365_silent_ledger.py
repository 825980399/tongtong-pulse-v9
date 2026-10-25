# -*- coding: utf-8 -*-
"""★365刀B3：CI 静默 except「三形态」68 条**渐进留痕**台账（裁决丙）。

本刀只做留痕，不切判据：
  * 不切五元组指纹结构（`ci_common.handler_fingerprint` 一字不动）；
  * 不翻三档判据、不动既有 40 条豁免、不销账 363 已核查的 6 条指纹；
  * 仅把「新判据比旧判据多抓到的条目」逐条落台账（命中位/档位建议/证据位置/分级）。

为什么先留痕：364 前置对比实测新判据是旧判据**严格超集**（removed=0），生产面 +68
（cont 64 / break 4）；直接切会让 CI 当场多 68 条红灯，且启发式分档实测 6 条里误判 5 条
⇒ 三档归类不能自动化，只能人工带元数据。先观察一轮再切主体（366+）。

台账字段
--------
path / qualname / nth / tname / kind / sha16  —— 五元组指纹＋形态（与判据同源）
lineno / body                               —— 证据位置（行号会漂，身份不靠它）
tier_suggestion                             —— A/B/C 启发式建议（**非终裁**，误判率高）
grade                                       —— A⇒WARNING（必改），B/C⇒INFO（观察）

用法
----
    python tools/ci/m365_silent_ledger.py --emit    # 重算并落台账
    python tools/ci/m365_silent_ledger.py --check   # 核对台账与现仓是否一致
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # tools/ci ⇒ 仓库根（两级）
_LEDGER = os.path.join(_HERE, "baselines", "m365_silent_except_ledger.json")
_SCANNER = os.path.join(_ROOT, "tools", "scan_silent_except_dual.py")

SCHEMA = "m365.silent_except_ledger.v1"


def _load_scanner():
    if not os.path.isfile(_SCANNER):
        raise FileNotFoundError(_SCANNER)
    spec = importlib.util.spec_from_file_location("m365_dual_scan", _SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def grade_of(tier: str) -> str:
    """分档分级：A（必改）⇒ WARNING；B/C（观察）⇒ INFO。"""
    return "WARNING" if str(tier or "").upper() == "A" else "INFO"


def build_ledger() -> dict:
    """重算「新判据多抓到的条目」并组装台账。"""
    _m = _load_scanner()
    _files, _old, _new, _parse_err = _m.scan("prod")
    _old_cmp = set(_m.handler_identity(x) for x in _old)
    _added = [x for x in _new if _m._cmp_key(x) not in _old_cmp]
    _rows = []
    for _x in sorted(_added, key=lambda r: (r["path"], r["lineno"], r["qualname"])):
        _rows.append({
            "path": _x["path"],
            "qualname": _x["qualname"],
            "nth": _x["nth"],
            "tname": _x["tname"],
            "kind": _x["kind"],
            "sha16": _x["sha16"],
            "lineno": _x["lineno"],
            "body": _x.get("body", ""),
            "tier_suggestion": _x.get("tier"),
            "grade": grade_of(_x.get("tier")),
            "evidence_anchor": "%s:%s" % (_x["path"], _x["lineno"]),
        })
    _by_kind: dict = {}
    _by_grade: dict = {}
    for _r in _rows:
        _by_kind[_r["kind"]] = int(_by_kind.get(_r["kind"], 0)) + 1
        _by_grade[_r["grade"]] = int(_by_grade.get(_r["grade"], 0)) + 1
    return {
        "schema": SCHEMA,
        "judge_version": "v2 (five-tuple + cont/break)",
        "basis": "tools/scan_silent_except_dual.py（旧判据直接调 ci_common 权威实现）",
        "note": "本台账只留痕；判据切换（主体）后置 366+，切前须人工带元数据分档",
        "total": len(_rows),
        "by_kind": _by_kind,
        "by_grade": _by_grade,
        "parse_err": [list(p) for p in _parse_err],
        "rows": _rows,
    }


def identity_of(row: dict) -> tuple:
    return (row.get("path"), row.get("qualname"), int(row.get("nth") or 0),
            str(row.get("tname") or ""), str(row.get("kind") or ""),
            str(row.get("sha16") or ""))


def diff_ledger(old_rows: list, new_rows: list) -> dict:
    """按身份（**不含行号**）比对台账与现仓：行号漂移不算变化。"""
    _o = {identity_of(r) for r in (old_rows or [])}
    _n = {identity_of(r) for r in (new_rows or [])}
    return {"added": sorted(_n - _o), "removed": sorted(_o - _n),
            "same": len(_o & _n)}


def main():
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--emit", action="store_true")
    _ap.add_argument("--check", action="store_true")
    _args = _ap.parse_args()
    if not (_args.emit or _args.check):
        _ap.print_help()
        return 0
    _new = build_ledger()
    if _args.emit:
        os.makedirs(os.path.dirname(_LEDGER), exist_ok=True)
        _tmp = _LEDGER + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_new, f, ensure_ascii=False, indent=1)
        os.replace(_tmp, _LEDGER)
        with open(_LEDGER, encoding="utf-8") as f:
            _back = json.load(f)
        assert int(_back.get("total") or 0) == len(_back.get("rows") or []), "读回不一致"
        print("[365B3] 台账已落盘 %s total=%d by_kind=%s by_grade=%s"
              % (_LEDGER, _back["total"], _back["by_kind"], _back["by_grade"]))
        return 0
    # --check
    if not os.path.isfile(_LEDGER):
        print("[365B3] 台账缺失：%s" % _LEDGER)
        return 2
    with open(_LEDGER, encoding="utf-8") as f:
        _old = json.load(f)
    _d = diff_ledger(_old.get("rows") or [], _new["rows"])
    print("[365B3] 台账核对：same=%d added=%d removed=%d"
          % (_d["same"], len(_d["added"]), len(_d["removed"])))
    for _t in _d["added"][:10]:
        print("  + %s::%s[nth=%s] %s kind=%s" % (_t[0], _t[1], _t[2], _t[3], _t[4]))
    for _t in _d["removed"][:10]:
        print("  - %s::%s[nth=%s] %s kind=%s" % (_t[0], _t[1], _t[2], _t[3], _t[4]))
    return 0 if not (_d["added"] or _d["removed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
