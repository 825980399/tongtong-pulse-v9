# -*- coding: utf-8 -*-
"""412 刀4 工具：O3 删死代码放行 **dry-run 报表**（只统计，不 apply）。

用法::

    python tools/m412_o3_delete_dryrun.py            # 真问题池 + 真调用图（慢，一天一次）
    python tools/m412_o3_delete_dryrun.py --probe    # 替身图判据自测（快，不碰真图）
    python tools/m412_o3_delete_dryrun.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution import o3_delete_dryrun as dd  # noqa: E402


class _FakeGraph:
    """调用图替身（只实现报表用到的两个口）。"""

    def __init__(self, iso, nodes=None):
        self._iso = list(iso)
        self._nodes = list(nodes or [])

    def get_isolated_functions(self):
        return list(self._iso)

    def _ensure(self):
        return {"nodes": list(self._nodes)}


def _probe():
    _ok = []
    _idx = dd.isolated_index(_FakeGraph(
        [{"id": "a.b::m1", "name": "m1", "file": "nucleus/x.py", "line": 10,
          "type": "method", "public_api": False, "entry_like": False}],
        nodes=[{"file": "nucleus/x.py"}, {"file": "nucleus/y.py"}]))
    _ok.append(("索引 ok", _idx["state"] == "ok" and ("nucleus/x.py", "m1") in _idx["orphans"]))
    _ok.append(("覆盖文件集", _idx["files"] == {"nucleus/x.py", "nucleus/y.py"}))

    _good = {"id": "1", "issue_type": "dead_code", "status": "open",
             "file": "nucleus/x.py", "method": "m1", "line": 10}
    _j = dd.judge_entry(_good, _idx)
    _ok.append(("三者全满足 ⇒ eligible", _j["eligible"] is True and not _j["reasons"]))

    _bad_type = dict(_good, issue_type="long_method")
    _ok.append(("非 O3 判死族 ⇒ 否", dd.judge_entry(_bad_type, _idx)["eligible"] is False))

    _bad_st = dict(_good, status="claimed")
    _ok.append(("非 open ⇒ 否", dd.judge_entry(_bad_st, _idx)["eligible"] is False))

    _not_in_graph = dict(_good, file="nucleus/zzz.py")
    _ok.append(("文件不在图内 ⇒ 否", dd.judge_entry(_not_in_graph, _idx)["eligible"] is False))

    _pub = dict(_good, method="public_one")
    _idx2 = dd.isolated_index(_FakeGraph(
        [{"name": "public_one", "file": "nucleus/x.py", "line": 1,
          "public_api": True, "entry_like": False}], nodes=[{"file": "nucleus/x.py"}]))
    _ok.append(("public_api ⇒ 否", dd.judge_entry(_pub, _idx2)["eligible"] is False))

    _core = dict(_good, file="config.py")
    _ok.append(("核心文件 ⇒ 否", dd.judge_entry(_core, _idx)["eligible"] is False))

    _bad_idx = {"state": "unavailable", "orphans": {}, "files": None, "reason": "x"}
    _ok.append(("图不可用 ⇒ None（不是 False）",
                dd.judge_entry(_good, _bad_idx)["eligible"] is None))
    _idx_nofiles = {"state": "ok", "orphans": _idx["orphans"], "files": None}
    _ok.append(("覆盖不可判 ⇒ None",
                dd.judge_entry(_good, _idx_nofiles)["eligible"] is None))

    _r = dd.report(entries=[_good, _bad_type, _not_in_graph], graph=_FakeGraph(
        [{"name": "m1", "file": "nucleus/x.py", "line": 10,
          "public_api": False, "entry_like": False}], nodes=[{"file": "nucleus/x.py"}]))
    _ok.append(("报表 eligible=1", _r["eligible"] == 1 and _r["o3_total"] == 2))
    _ok.append(("报表 dry_run=True 且 applied=0",
                _r["dry_run"] is True and _r["applied"] == 0))
    _ok.append(("报表带样本与拒因", _r["samples"] and _r["rejected"]))

    _r2 = dd.report(entries=[_good], graph=_FakeGraph([], nodes=[]))
    _ok.append(("图空 ⇒ eligible=None（不报 0）",
                _r2["eligible"] is None and _r2["graph"]["state"] == "empty"))
    _ok.append(("条目源不可用 ⇒ 有 reason",
                dd.report(entries=[], graph=_FakeGraph([]))["o3_total"] == 0))

    print("=" * 78)
    print("[探针] 替身图判据（不碰真调用图）")
    _p = 0
    for _name, _okk in _ok:
        print("  [%s] %s" % ("PASS" if _okk else "FAIL", _name))
        _p += 1 if _okk else 0
    print("\n[结论] %s（PASS %d / FAIL %d）" % (
        "PASS" if _p == len(_ok) else "FAIL", _p, len(_ok) - _p))
    return 0 if _p == len(_ok) else 1


def main() -> int:
    _ap = argparse.ArgumentParser(description="412 刀4 O3 delete_only dry-run 报表")
    _ap.add_argument("--probe", action="store_true", help="替身图判据自测")
    _ap.add_argument("--json", action="store_true", help="机器可读输出")
    _ap.add_argument("--sample", type=int, default=8, help="报表里列几条样本")
    _a = _ap.parse_args()

    if _a.probe:
        return _probe()

    _r = dd.report(sample=_a.sample)
    if _a.json:
        print(json.dumps(_r, ensure_ascii=False, indent=2))
        return 0
    print("=" * 78)
    print("[O3 delete_only dry-run] 将放行 %s 条（样本 %d 条）｜**不真 apply**"
          % (_r["eligible"], len(_r["samples"])))
    print("  问题池：%s（%s 条，其中 O3 判死族 %d 条）"
          % (_r["source"], _r["scanned"], _r["o3_total"]))
    print("  调用图：state=%s 孤儿 %d 个 / 覆盖文件 %s"
          % (_r["graph"]["state"], _r["graph"]["orphans"], _r["graph"]["files"]))
    print("  不可判：%d 条" % _r["undecidable"])
    if _r["reason"]:
        print("  说明：%s" % _r["reason"])
    print("  拒因分布：%s" % json.dumps(_r["rejected"], ensure_ascii=False))
    print("  样本：")
    for _s in _r["samples"]:
        print("    · %-24s %s ::%s (L%s)" % (_s["id"], _s["file"], _s["method"], _s["line"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
