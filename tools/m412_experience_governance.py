# -*- coding: utf-8 -*-
"""412 刀2 工具：经验池治理读数 + 模板标记（`--annotate --write` 才落盘）。

用法::

    python tools/m412_experience_governance.py                 # --stats（默认，只读）
    python tools/m412_experience_governance.py --annotate      # dry-run：只看会标多少条
    python tools/m412_experience_governance.py --annotate --write
    python tools/m412_experience_governance.py --probe         # 隔离根判据（不碰真池）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.observability import experience_governance as eg  # noqa: E402


def _stats():
    _c = eg.content_coverage()
    _t = eg.scan_templates()
    print("=" * 78)
    print("[经验池] content 字段真相（412刀2 先查清再动手）")
    print("  条目总数            : %s" % _c.get("total"))
    print("  有 content 字段     : %s" % _c.get("has_content_field"))
    print("  content 非空        : %s" % _c.get("content_non_empty"))
    print("  summary 非空        : %s" % _c.get("summary_non_empty"))
    print("  raw_summary 非空    : %s" % _c.get("raw_summary_non_empty"))
    print("  compressed 非空     : %s" % _c.get("compressed_summary_non_empty"))
    print("  is_summarized       : %s" % _c.get("is_summarized"))
    print("  结论                : **%s** — %s" % (_c.get("verdict"), _c.get("reason")))
    print()
    print("[模板污染]")
    print("  不同文本数          : %s" % _t.get("distinct_texts"))
    print("  最热模板占比        : %s" % _t.get("template_ratio"))
    print("  已打 is_template    : %s" % _t.get("already_marked"))
    for _k in (_t.get("template_keys") or [])[:5]:
        print("    · %4d 条 (%.2f) %s" % (_k["count"], _k["ratio"], _k["key"][:56]))
    return 0


def _probe():
    """隔离判据（不碰真池）：造一个可控的假池走一遍治理逻辑。"""

    class _Pool:
        def __init__(self, rows):
            self.rows = list(rows)
            self.saved = 0

        def query_experiences(self, limit=10):
            # ★413刀4：复刻**真池**语义 —— 返回 `dict(exp)` 副本。
            #   412 的探针返回原引用，于是把"改副本后 _save() 写不出标记"
            #   这个真 bug 整个放过去了（探针假绿）。
            _src = self.rows[-int(limit):] if limit else self.rows
            return [dict(x) for x in _src]

        def annotate_flags(self, flags):
            """与真池同语义：按 id 改**原对象**，None 值表示删除。"""
            _n = 0
            for _e in self.rows:
                _u = flags.get(str(_e.get("id") or ""))
                if not _u:
                    continue
                _hit = False
                for _k, _v in _u.items():
                    if _v is None:
                        if _k in _e:
                            _e.pop(_k, None)
                            _hit = True
                    elif _e.get(_k) != _v:
                        _e[_k] = _v
                        _hit = True
                if _hit:
                    _n += 1
            return _n

        def _save(self):
            self.saved += 1

    _rows = ([{"id": "t%d" % i, "summary": "模板A", "is_summarized": True} for i in range(5)]
             + [{"id": "u%d" % i, "summary": "各不相同%d" % i} for i in range(3)]
             + [{"id": "n1", "summary": ""}])
    _p = _Pool(_rows)
    _ok = []

    _c = eg.content_coverage(_p)
    _ok.append(("content 无字段+有 summary ⇒ design",
                _c["verdict"] == "design" and _c["has_content_field"] == 0))

    _t = eg.scan_templates(_p, min_dup=3)
    _ok.append(("模板键被抓到", _t["state"] == "ok" and _t["template_keys"]
                and _t["template_keys"][0]["count"] == 5))
    _ok.append(("不同文本数=5（模板A/各不相同×3/空）", _t["distinct_texts"] == 5))

    _r = eg.annotate(_p, dry_run=True)
    _ok.append(("dry-run 不改条目", _r["written"] is False
                and all("is_template" not in x for x in _p.rows)))

    _r2 = eg.annotate(_p, dry_run=False)
    _ok.append(("write 后模板条被标记", _r2["written"] is True
                and sum(1 for x in _p.rows if x.get("is_template")) == 5))
    _ok.append(("读回校验 landed=5 且无 reason（413刀4）",
                _r2.get("landed") == 5 and _r2.get("reason") == ""))
    _ok.append(("非模板条不带标记",
                all(not x.get("is_template") for x in _p.rows
                    if str(x.get("id") or "").startswith("u"))))
    _ok.append(("降权：模板 %.1f / 正常 1.0" % eg.TEMPLATE_WEIGHT,
                eg.retrieval_weight({"is_template": True}) == eg.TEMPLATE_WEIGHT
                and eg.retrieval_weight({}) == 1.0))

    # 空池 / 坏池
    _ok.append(("空池 ⇒ empty", eg.content_coverage(_Pool([]))["state"] == "empty"))

    class _Bad:
        def query_experiences(self, limit=10):
            raise RuntimeError("坏了")

    _ok.append(("坏池 ⇒ unavailable 不崩", eg.content_coverage(_Bad())["state"] == "unavailable"))

    print("=" * 78)
    print("[探针] 隔离判据（不碰真池）")
    _pass = 0
    for _name, _okk in _ok:
        print("  [%s] %s" % ("PASS" if _okk else "FAIL", _name))
        _pass += 1 if _okk else 0
    print("\n[结论] %s（PASS %d / FAIL %d）" % (
        "PASS" if _pass == len(_ok) else "FAIL", _pass, len(_ok) - _pass))
    return 0 if _pass == len(_ok) else 1


def main() -> int:
    _ap = argparse.ArgumentParser(description="412 刀2 经验池治理")
    _ap.add_argument("--stats", action="store_true", help="只读读数（默认）")
    _ap.add_argument("--annotate", action="store_true", help="打 is_template 标记")
    _ap.add_argument("--write", action="store_true", help="真写（默认 dry-run）")
    _ap.add_argument("--probe", action="store_true", help="隔离根判据")
    _a = _ap.parse_args()

    if _a.probe:
        return _probe()
    if _a.annotate:
        _r = eg.annotate(dry_run=not _a.write)
        print(json.dumps(_r, ensure_ascii=False, indent=2))
        if not _a.write:
            print("★dry-run：未落盘。要真写请加 --write")
        return 0
    return _stats()


if __name__ == "__main__":
    raise SystemExit(main())
