# -*- coding: utf-8 -*-
"""338 刀5-①：`edge_types.typed_infer` 护栏告警线（CI 门禁）。

为什么要有这条线
----------------
337 刀9 把 `typed_infer` 从**默认开**改成**默认关**，并登记了 4 个只能用类型推断
建边的方法（`_M337_TYPED_ONLY_METHODS`）。但"默认关"只是一次性动作——如果哪天
有人把开关翻回去、或类型推断悄悄绕过登记表放开，**没有任何东西会发现**。

本门禁只做一件事：把最新一张图的 `edge_types.typed_infer` 与基线对表，
超线即 FAIL。它不看代码、不猜意图，只看产物里的那个数。

口径纪律
--------
* **跨 code_version 禁比**（禁用清单 v3 · A-6）：图的 `code_version` 与基线不同 ⇒
  直接 `UNAVAILABLE` 跳过，不拿两把尺子量出来的数做比较。
* **重锚必须带归因**：`--emit` 必须同时给 `--attr`，且归因文件里的
  `expected_count` 必须**严格等于**实测值——否则任何人都能把红线往后挪一格。
* 读数自带身份：每次输出都带 `graph=` / `mtime=` / `code_version=` / `proc_gen=`（A-5）。

用法
----
    python tools/ci/check_typed_infer_guard.py            # 校验（CI 用）
    python tools/ci/check_typed_infer_guard.py --graph X  # 指定图（注入测试用）
    python tools/ci/check_typed_infer_guard.py --emit --attr attr.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_BASELINE = os.path.join(_ROOT, "tools", "ci", "baselines", "typed_infer_baseline.json")


def _log(level: str, msg: str) -> None:
    """CI 脚本自身的留痕口（不依赖业务 logger，避免引入框架）。

    名字 `_log` 在 `tools/ci/ci_common.LOG_FUNCS` 白名单内，
    否则本脚本自身会被 cw2 定为“静默 handler”（CI 脚本也必须过门禁）。
    """
    sys.stderr.write("[338刀5-typed_infer][%s] %s\n" % (level, msg))


def _proc_gen() -> str:
    try:
        from nucleus.proc_gen import proc_gen_str

        return proc_gen_str()
    except Exception as _e:
        # psutil 不可用/框架未就绪：记 unknown，**不填 0**
        _log("warning", "proc_gen 取数失败，记 unknown: %s: %s"
             % (type(_e).__name__, _e))
        return "unknown"


def _load_graph(path: str | None) -> tuple:
    """返回 (graph_dict, path, mtime, why)。取不到时 graph=None 并给 why。"""
    if path:
        if not os.path.exists(path):
            return None, path, None, "指定图不存在"
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f), path, os.path.getmtime(path), ""
        except Exception as e:
            _log("error", "指定图不可读: %s: %s" % (type(e).__name__, e))
            return None, path, None, "%s: %s" % (type(e).__name__, e)
    try:
        from nucleus.data.call_graph_guard import select_latest_graph

        _r = select_latest_graph(os.path.join(_ROOT, "data", "call_graph"))
        _g = _r.get("graph") or {}
        _p = _r.get("path") or ""
        if not _g:
            return None, _p, None, "无合格图（select_latest_graph 返回空）"
        return _g, _p, (os.path.getmtime(_p) if _p and os.path.exists(_p) else None), ""
    except Exception as e:
        _log("error", "取最新图失败: %s: %s" % (type(e).__name__, e))
        return None, "", None, "%s: %s" % (type(e).__name__, e)


def _typed_infer_count(graph: dict) -> int | None:
    """取 typed_infer 计数；字段缺失返回 None（**不填 0**）。"""
    _et = graph.get("edge_types")
    if not isinstance(_et, dict):
        return None
    _v = _et.get("typed_infer")
    if _v is None:
        return None
    try:
        return int(_v)
    except (TypeError, ValueError) as _e:
        # 字段在但不是数：记 None（**不填 0**），且必须可查
        _log("warning", "edge_types.typed_infer 非整数: %r (%s)"
             % (_v, type(_e).__name__))
        return None


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(add_help=True)
    _ap.add_argument("--graph", default=None, help="指定图文件（缺省取最新合格图）")
    _ap.add_argument("--emit", action="store_true", help="重锚基线（必须同时给 --attr）")
    _ap.add_argument("--attr", default=None, help="归因 JSON：{reason, expected_count}")
    _args = _ap.parse_args(argv)

    _g, _gp, _mt, _why = _load_graph(_args.graph)
    _pg = _proc_gen()
    _idline = "graph=%s mtime=%s code_version=%s proc_gen=%s" % (
        os.path.basename(_gp) if _gp else "-",
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_mt)) if _mt else "-",
        (_g or {}).get("code_version") or "-",
        _pg)

    if _g is None:
        # 取不到图 ≠ 通过：如实记 UNAVAILABLE 并以 0 退出（缺图是上游问题，不阻断提交）
        print("[338刀5-typed_infer] UNAVAILABLE 取不到图: %s | %s" % (_why, _idline))
        return 0

    _cnt = _typed_infer_count(_g)
    if _cnt is None:
        print("[338刀5-typed_infer] UNAVAILABLE 图中无 edge_types.typed_infer 字段 | %s" % _idline)
        return 0

    try:
        with open(_BASELINE, encoding="utf-8") as f:
            _base = json.load(f)
    except Exception as e:
        print("[338刀5-typed_infer] FAIL 基线不可读（门禁类禁静默降级）: %s: %s"
              % (type(e).__name__, e))
        return 1

    _max = _base.get("max_typed_infer")
    _bv = _base.get("code_version")
    _gv = _g.get("code_version")

    if _bv and _gv and _bv != _gv:
        # A-6：跨版本禁比
        print("[338刀5-typed_infer] UNAVAILABLE 跨 code_version 禁比（基线=%s 图=%s）| %s"
              % (_bv, _gv, _idline))
        return 0

    if _args.emit:
        if not _args.attr or not os.path.exists(_args.attr):
            print("[338刀5-typed_infer] FAIL --emit 必须带存在的 --attr（禁止无归因重锚）")
            return 1
        try:
            with open(_args.attr, encoding="utf-8") as f:
                _attr = json.load(f)
        except Exception as e:
            print("[338刀5-typed_infer] FAIL 归因文件不可解析: %s: %s" % (type(e).__name__, e))
            return 1
        _reason = str(_attr.get("reason") or "").strip()
        _expected = _attr.get("expected_count")
        if not _reason:
            print("[338刀5-typed_infer] FAIL 归因缺 reason")
            return 1
        if _expected != _cnt:
            # 算术自洽：申报值必须等于实测值，否则等于把红线按想象挪
            print("[338刀5-typed_infer] FAIL 归因 expected_count=%r ≠ 实测 %d"
                  "（重锚不得与实测脱节）" % (_expected, _cnt))
            return 1
        _new = dict(_base)
        _new.update({"max_typed_infer": _cnt, "code_version": _gv,
                     "reanchored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "reanchor_reason": _reason, "proc_gen": _pg})
        _hits = _new.setdefault("reanchor_history", [])
        _hits.append({"at": _new["reanchored_at"], "from": _max, "to": _cnt,
                      "reason": _reason, "proc_gen": _pg})
        with open(_BASELINE + ".tmp", "w", encoding="utf-8", newline="") as f:
            json.dump(_new, f, ensure_ascii=False, indent=2)
        os.replace(_BASELINE + ".tmp", _BASELINE)
        print("[338刀5-typed_infer] PASS 已重锚 %r -> %d（reason=%s）| %s"
              % (_max, _cnt, _reason, _idline))
        return 0

    if _max is None:
        print("[338刀5-typed_infer] FAIL 基线缺 max_typed_infer（门禁类禁静默降级）")
        return 1
    if _cnt > int(_max):
        print("[338刀5-typed_infer] FAIL typed_infer=%d > 上限 %d"
              "（登记表外的类型推断边出现了？查 _M337_TYPED_ONLY_METHODS 与 ENABLE_TYPED_INFER）"
              " | %s" % (_cnt, int(_max), _idline))
        return 1
    print("[338刀5-typed_infer] PASS typed_infer=%d ≤ %d | %s" % (_cnt, int(_max), _idline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
