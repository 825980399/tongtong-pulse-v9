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
import ast
import importlib.util
import json
import os
import logging
import sys

_LOG = logging.getLogger("tools.ci.m365_silent_ledger")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # tools/ci ⇒ 仓库根（两级）
_LEDGER = os.path.join(_HERE, "baselines", "m365_silent_except_ledger.json")
_SCANNER = os.path.join(_ROOT, "tools", "scan_silent_except_dual.py")

if _ROOT not in sys.path:  # ★399刀1：给退役归因用（直接跑本脚本时也能 import nucleus）
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

#: ★399刀1：366 N3（`da1f0ad1`）把台账升成 v2（加了 manual_review / exemption），
#:   但**只在 json 里手改了 `schema` 字段**，工具这个常量一直是 v1
#:   ⇒ 一旦 `--emit` 就退版，`test_m365_b3::test_01` 当场红。
#:   现在把常量补成 v2（结构确实是 v2），从此 emit 不会再把版本号打回去。
SCHEMA = "m365.silent_except_ledger.v2"


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


#: ★366刀N3：人工复核写入台账后，**重算不得抹掉**的字段（按身份键保真）
PRESERVE_FIELDS = ("manual_review", "exemption")


def _load_existing_meta(path: str | None = None) -> dict:
    """读既有台账的身份 → 元数据映射（用于 `--emit` 时保真，避免覆盖人工结论）。"""
    _p = path or _LEDGER
    if not os.path.isfile(_p):
        return {}
    try:
        with open(_p, encoding="utf-8") as f:
            _old = json.load(f)
    except (OSError, ValueError) as _e:
        _LOG.warning("[N3] 既有台账不可读（人工元数据本次不保真） %s: %s: %s",
                     _p, type(_e).__name__, _e)
        return {}
    _out = {}
    for _r in (_old.get("rows") or []):
        _k = identity_of(_r)
        _meta = {k: _r[k] for k in PRESERVE_FIELDS if k in _r}
        if _meta:
            _out[_k] = _meta
    return _out


# ---------------------------------------------------------------- ★399刀1 退役归档
def _load_ledger_raw(path: str | None = None) -> dict:
    """读既有台账原文（rows + retired），读不到就给空壳。与 `_load_existing_meta` 分开：
    那个只关心"要保真的元数据"，这个还要管"已经退役的行别丢"。"""
    _p = path or _LEDGER
    if not os.path.isfile(_p):
        return dict()
    try:
        with open(_p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="m365_silent_ledger::_load_ledger_raw")
        return dict()


def _find_func_node(tree, qualname: str):
    """按 qualname 找函数节点（支持 `Class.method`；找不到就退回同名叶子）。"""
    _leaf = str(qualname or "").split(".")[-1]
    _exact, _loose = [], []

    def _walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _q = (prefix + "." + ch.name) if prefix else ch.name
                if _q == qualname:
                    _exact.append(ch)
                elif ch.name == _leaf:
                    _loose.append(ch)
                _walk(ch, _q)
            elif isinstance(ch, ast.ClassDef):
                _walk(ch, (prefix + "." + ch.name) if prefix else ch.name)

    _walk(tree, "")
    if _exact:
        return _exact[0]
    return _loose[0] if _loose else None


def _calls_name(fn, name: str) -> bool:
    for _n in ast.walk(fn):
        if isinstance(_n, ast.Call):
            _f = _n.func
            if isinstance(_f, ast.Name) and _f.id == name:
                return True
            if isinstance(_f, ast.Attribute) and _f.attr == name:
                return True
    return False


#: 退役原因 → 写进 `retired_by` 的中文说明（★沿用 371A5 已有的 `retired_by` 约定，
#: 例：`"371A5第1笔（break→continue＋WARNING，已非静默 handler）"`）
_REASON_CN = {
    "fixed": "已补 silent_exc 留痕，不再是静默 handler",
    "func_gone": "函数已不存在",
    "no_longer_silent": "静默 handler 已被删除",
    "identity_drift": "函数体变化导致身份漂移，已按新身份重新入册",
    "unknown": "归因失败，待人看",
}


def _retire_reason(row: dict, live_pq=None) -> str:
    """一条台账行为什么不在差集里了。

    ★只用于**归档说明**（写进 `retired_by`），不参与任何判据。
    取值：`fixed`（函数还在且已补 `silent_exc`）／`func_gone`（qualname 找不到）／
    `no_longer_silent`（函数在、无 silent_exc，静默 handler 本身被删）／
    `identity_drift`（同 (path, qualname) 仍有在册行，只是 sha16 变了）。
    """
    if live_pq and (str(row.get("path") or ""), str(row.get("qualname") or "")) in live_pq:
        return "identity_drift"
    _p = os.path.join(_ROOT, str(row.get("path") or ""))
    try:
        with open(_p, encoding="utf-8-sig", errors="replace") as f:
            _src = f.read()
    except OSError as _e:
        # 文件都没了 ⇒ 函数必然不在（这是 func_gone 最常见的原因，别归成 unknown）
        silent_exc(_e, where="m365_silent_ledger::_retire_reason::open")
        return "func_gone"
    try:
        _t = ast.parse(_src)
    except (SyntaxError, ValueError) as _e:
        silent_exc(_e, where="m365_silent_ledger::_retire_reason::parse")
        return "unknown"
    _fn = _find_func_node(_t, str(row.get("qualname") or ""))
    if _fn is None:
        return "func_gone"
    if _calls_name(_fn, "silent_exc"):
        return "fixed"
    return "no_longer_silent"


def build_ledger(preserve: bool = True, batch: str | None = None) -> dict:
    """重算「新判据多抓到的条目」并组装台账。

    ★366刀N3：`preserve=True` 时把既有台账里的人工元数据（manual_review/exemption）
    按身份键搬过来 —— 否则每次 `--emit` 都会把人工作出的结论冲掉（那就没人敢往里写）。

    ★399刀1：三件事
      ① 差集两侧必须用**同一个**键函数（见下面 `_old_cmp` 注释）；
      ② `preserve=True` 时以**既有全集**为底重排：仍活跃的行用现算字段覆盖，
         已离开差集的行**原地**标 `retired_by` / `retired_reason`。
         ★沿用 371A5 已有的 `retired_by` 约定（首行就是那样写的），
         不另开一个 `retired` 数组 —— 两份名单迟早分叉；
      ③ 幂等：已退役的行不会被重复标、也不会被"复活"。
    """
    _m = _load_scanner()
    _prev = _load_ledger_raw() if preserve else dict()
    _meta = _load_existing_meta() if preserve else {}
    _files, _old, _new, _parse_err = _m.scan("prod")
    # ★399刀1 修：两侧必须用**同一个**键函数。原代码左边用 `handler_identity`
    #   （5 元组，含 tname/kind；371A5 把 nth 从身份里去掉了），右边用 `_cmp_key`
    #   （4 元组，含 nth、不含 tname/kind）⇒ 两个元组永远不相等 ⇒ 差集恒等于全集
    #   （399 实测 552 = 全部 `_new`，台账被迫退化成"所有新判据命中"）。
    #   `_cmp_key` 自己的注释写明它就是"跨判据可比键（同一 handler 的两副面孔）"，
    #   所以两侧都用它 —— 只有它保证"同一个 handler 的两副判据面孔"能对上。
    _old_cmp = set(_m._cmp_key(x) for x in _old)
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
    # ★366刀N3：按身份把人工元数据搬回来（只在对应身份仍存在时）
    if _meta:
        for _r in _rows:
            _m2 = _meta.get(identity_of(_r))
            if _m2:
                _r.update(_m2)
    _by_kind: dict = {}
    _by_grade: dict = {}
    # ★399刀1：以既有全集为底做「在册 / 退役」重排。
    #   为什么不能像第一版那样另开一个 `retired` 数组：那样一来 `rows`（待办）与
    #   `retired`（结案）是两份名单，retired 里那 1 条 A 档人工结论就脱离了 `test_01`
    #   能看见的范围；而且 369 那批判据早就按 `retired_by` 字段在过滤（371A5 起就有 1 条），
    #   再开一套只会分叉。**沿用既有约定：退役 = 行的 `retired_by` 非空，行本身不删。**
    if preserve and _prev.get("rows"):
        _live_by_id = {identity_of(r): r for r in _rows}
        _live_pq = {(str(r.get("path") or ""), str(r.get("qualname") or ""))
                    for r in _rows}
        _merged = []
        for _r0 in _prev.get("rows") or []:
            _k = identity_of(_r0)
            if _k in _live_by_id:
                _r1 = dict(_live_by_id.pop(_k))     # 仍活跃：用现算字段（行号/body 会更新）
                _r1.pop("retired_by", None)
                _r1.pop("retired_reason", None)
                _merged.append(_r1)
            else:
                _r1 = dict(_r0)                      # 已离开差集：原地退役（幂等，不覆盖已有标注）
                if not _r1.get("retired_by"):
                    _why = _retire_reason(_r0, _live_pq)
                    _r1["retired_reason"] = _why
                    _r1["retired_by"] = "%s（%s）" % (
                        str(batch or "unspecified"), _REASON_CN.get(_why, _why))
                _merged.append(_r1)
        _merged.extend(_live_by_id.values())         # 真正的全新条目
        _rows = sorted(_merged, key=lambda r: (str(r.get("path") or ""),
                                               str(r.get("qualname") or ""),
                                               int(r.get("nth") or 0)))
    _live_rows = [r for r in _rows if not r.get("retired_by")]
    for _r in _rows:
        _by_kind[_r["kind"]] = int(_by_kind.get(_r["kind"], 0)) + 1
        _by_grade[_r["grade"]] = int(_by_grade.get(_r["grade"], 0)) + 1
    _by_kind_live: dict = {}
    for _r in _live_rows:
        _by_kind_live[_r["kind"]] = int(_by_kind_live.get(_r["kind"], 0)) + 1
    return {
        "schema": SCHEMA,
        "judge_version": "v2 (five-tuple + cont/break)",
        "basis": "tools/scan_silent_except_dual.py（旧判据直接调 ci_common 权威实现）",
        "note": "本台账只留痕；判据切换（主体）后置 366+，切前须人工带元数据分档",
        # ★399刀1：`total` 是**全集**（含已退役），`live_n` 才是当前待办数。
        #   别把 total 当待办看 —— 399 实测全集 68 / 在册 10。
        "total": len(_rows),
        "live_n": len(_live_rows),
        "by_kind": _by_kind,
        "by_grade": _by_grade,
        "by_kind_live": _by_kind_live,
        # ★399刀1：这两个计数原先**是手工写进 json 的**，`--emit` 一跑就变 None
        #   （实测 ⇒ test_01/test_02 当场红，而人只看到"台账没零漂移"）。
        #   改由 rows 现场派生，从此不会再漂。
        "manual_review_n": sum(1 for r in _rows if r.get("manual_review")),
        "control_flow_exempt_n": sum(1 for r in _rows if r.get("exemption")),
        "parse_err": [list(p) for p in _parse_err],
        "retired_note": ("退役约定（★371A5 起）：行的 `retired_by` 非空＝已结案/已毕业，"
                         "**行本身不删**，`manual_review` 元数据原样保留。"
                         "`retired_reason` 取值：fixed（已补 silent_exc）/ func_gone（qualname 不在了）"
                         "/ no_longer_silent（静默 handler 被删）/ identity_drift（body 变了、"
                         "同一 (path, qualname) 已按新身份重新入册）。"),
        "retired_n": len(_rows) - len(_live_rows),
        "rows": _rows,
    }


def identity_of(row: dict) -> tuple:
    return (row.get("path"), row.get("qualname"), int(row.get("nth") or 0),
            str(row.get("tname") or ""), str(row.get("kind") or ""),
            str(row.get("sha16") or ""))


def diff_ledger(old_rows: list, new_rows: list) -> dict:
    """按身份（**不含行号**）比对台账与现仓：行号漂移不算变化。

    ★399刀1：两侧都**跳过已退役行**（`retired_by` 非空）。它们本来就不在现仓差集里，
    拿它们算 `removed` 只会让"零漂移"永远红。退役行的正确性由它们自己的
    `retired_reason` 承担（`test_m369_blade4` / `test_m399_k1` 都在验）。
    """
    _o = {identity_of(r) for r in (old_rows or []) if not r.get("retired_by")}
    _n = {identity_of(r) for r in (new_rows or []) if not r.get("retired_by")}
    return {"added": sorted(_n - _o), "removed": sorted(_o - _n),
            "same": len(_o & _n)}


def main():
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--emit", action="store_true")
    _ap.add_argument("--check", action="store_true")
    _ap.add_argument("--batch", default=None,
                     help="本次退役归属（写进 retired_by 前缀，例：399刀1）")
    _args = _ap.parse_args()
    if not (_args.emit or _args.check):
        _ap.print_help()
        return 0
    _new = build_ledger(batch=_args.batch)
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
