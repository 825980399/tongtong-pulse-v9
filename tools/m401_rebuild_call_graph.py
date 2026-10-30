# -*- coding: utf-8 -*-
"""★401批刀3（P2 / L1）：调用图重建 + 验收（运维工具，可复用）。

为什么需要单独一支工具
----------------------
`CallGraphAnalyzer.analyze()` 约 110s、CPU 峰值明显（烛微401 §风险①），不适合塞进任何
主循环；而落盘口 `dump_graph_snapshot()` 有两道闸（内容判据 + 写盘守卫），
**普通脚本进程会被写盘守卫拒写** —— 直接 `python -c` 跑出来的结果是"看起来跑完了，
其实一个字节都没落"。本工具显式声明自己是运维手（置 `PULSE_FRAMEWORK=1`），
把「跑了」和「落盘了」分开报告。

验收（与 `tests/test_m397_k2_callgraph_blindspot.py` 同口径）
    * 覆盖文件数 `>= --min-files`（默认 409；397刀2 交付读数，**用下限不用等号**）
    * 图内 `stats.total_files` 与扫描口径的差集要能逐条列出来（不是只报一个数）
    * `graph_latest.json` 常驻副本存在且可解析（`--keep-latest` 时）

用法
----
    PULSE_FRAMEWORK=1 python tools/m401_rebuild_call_graph.py            # 重建 + 验收
    python tools/m401_rebuild_call_graph.py --no-dump                    # 只算覆盖，不落盘
    python tools/m401_rebuild_call_graph.py --no-keep-latest             # 不写常驻副本

退出码：0 = 全部达判据；1 = 有判据不达标（明细照常打印，不吞）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _cga_module():
    """取 `CallGraphAnalyzer` **模块**（不是类）。

    ★本项目的坑：`nucleus/self_awareness/__init__.py` 把同名**类**重导出到包属性上，
      `import nucleus.self_awareness.CallGraphAnalyzer as m` 拿到的是类。要模块私有函数
      （`_iter_py_files` / `_scan_dirs`）只能走 `sys.modules`。
    """
    import nucleus.self_awareness.CallGraphAnalyzer  # noqa: F401
    return sys.modules["nucleus.self_awareness.CallGraphAnalyzer"]


def _norm(p) -> str:
    return str(p or "").replace("\\", "/").strip()


def _has_methods(rel: str) -> bool:
    """该文件里有没有**方法/函数**定义（判"无节点"是否合理）。

    方法级调用图只收 def ⇒ 空包 `__init__.py`、纯 Enum/TypedDict 常量模块
    **本来就没有节点**。把它们算成"覆盖缺口"是判据错型，会逼人加无意义的空函数。
    读不出/解析不了 ⇒ 按"应该有"处理（保守：宁可报给人看）。
    """
    try:
        with open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace") as _f:
            _t = ast.parse(_f.read())
    except (OSError, SyntaxError) as _e:
        from nucleus._silent_except import silent_exc as _sx
        _sx(_e, where="m401_rebuild_call_graph::_has_methods")
        return True
    return any(isinstance(_x, (ast.FunctionDef, ast.AsyncFunctionDef))
               for _x in ast.walk(_t))


def main(argv: list | None = None) -> int:
    _ap = argparse.ArgumentParser(
        description="401刀3：调用图重建 + 覆盖验收")
    _ap.add_argument("--keep-latest", dest="keep_latest", action="store_true",
                     default=True,
                     help="另存 graph_latest.json 常驻副本（默认开，不占滚动 7 份配额）")
    _ap.add_argument("--no-keep-latest", dest="keep_latest", action="store_false",
                     help="不写常驻副本（仅调试用）")
    _ap.add_argument("--no-dump", action="store_true", default=False,
                     help="只建图算覆盖，不落盘（不写 data/call_graph）")
    _ap.add_argument("--min-files", type=int, default=409,
                     help="覆盖文件数下限（默认 409 = 397刀2 交付读数，用下限不用等号）")
    _args = _ap.parse_args(argv)

    # 写盘守卫：本工具是运维手，不是测试。不置这个变量 ⇒ dump 静默返回空串。
    if not os.environ.get("PULSE_FRAMEWORK"):
        os.environ["PULSE_FRAMEWORK"] = "1"
        print("[guard] 已置 PULSE_FRAMEWORK=1（否则写盘守卫会拒写生产 data/，"
              "dump 返回空串且不报错）")

    _m = _cga_module()
    from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer

    _t0 = time.time()
    _cg = CallGraphAnalyzer()
    _g = _cg.analyze()
    if not isinstance(_g, dict):
        _g = getattr(_cg, "_graph_data", None)
    _elapsed = time.time() - _t0
    if not isinstance(_g, dict):
        print("[FAIL] analyze() 没返回图（拿到 %r）" % type(_g).__name__)
        return 1

    _nodes = [x for x in (_g.get("nodes") or []) if isinstance(x, dict)]
    _node_files = {_norm(x.get("file")) for x in _nodes if _norm(x.get("file"))}
    _scan_files = [_norm(x) for x in _m._iter_py_files(_m._project_root(),
                                                       _m._scan_dirs())]
    _scan_set = set(_scan_files)
    _missing_all = sorted(_scan_set - _node_files)
    # 缺节点分两类：零方法文件（合理）／有方法却缺（真缺口）
    _missing_real = [_x for _x in _missing_all if _has_methods(_x)]
    _missing_nodef = [x for x in _missing_all if x not in set(_missing_real)]
    _extra = sorted(_node_files - _scan_set)

    _out = {
        "analyze_s": round(_elapsed, 1),
        "nodes": len(_nodes),
        "edges": len(_g.get("edges") or []),
        "stats_total_files": (_g.get("stats") or {}).get("total_files"),
        "scan_files": len(_scan_files),
        "graph_files": len(_node_files),
        "graph_missing_should_have": _missing_real[:50],
        "graph_missing_should_have_n": len(_missing_real),
        "graph_missing_no_method_n": len(_missing_nodef),
        "extra_in_graph": _extra[:50],
        "extra_n": len(_extra),
        "code_version": _g.get("code_version"),
        "criteria_version": _g.get("criteria_version"),
    }

    _dump_path = ""
    _latest_path = ""
    _dumped_meta = {}
    if not _args.no_dump:
        _dump_path = _cg.dump_graph_snapshot(_g, keep_latest=_args.keep_latest)
        _cg_dir = os.path.join(_ROOT, "data", "call_graph")
        _lp = os.path.join(_cg_dir, "graph_latest.json")
        if _args.keep_latest and os.path.isfile(_lp):
            try:
                with open(_lp, encoding="utf-8") as _f:
                    _ld = json.load(_f)
                if isinstance(_ld, dict) and _ld.get("nodes"):
                    _latest_path = _lp
                    # 版本字段只在**落盘 payload** 里（`_graph_data` 本身不带），
                    # 所以从副本读，不从 analyze() 的返回值读（那是 null）。
                    _dumped_meta = {k: _ld.get(k) for k in
                                    ("code_version", "criteria_version",
                                     "edge_types_total", "edge_types_source")}
            except (OSError, ValueError) as _e:
                print("[WARN] graph_latest.json 存在但不可解析: %s: %s"
                      % (type(_e).__name__, _e))
    _out["dump_payload_meta"] = _dumped_meta
    _out["dump_path"] = os.path.relpath(_dump_path, _ROOT) if _dump_path else ""
    _out["graph_latest"] = os.path.relpath(_latest_path, _ROOT) if _latest_path else ""

    # ---- unresolved 全量清单（analyze 内部已落盘） ----
    try:
        from nucleus.self_awareness.unresolved_samples import latest_meta
        _um = latest_meta(_ROOT)
    except Exception as _e:                     # 工具面：报错不吞，直接进结果
        from nucleus._silent_except import silent_exc as _sx
        _sx(_e, where="m401_rebuild_call_graph::main unresolved_meta")
        _um = {"error": "%s: %s" % (type(_e).__name__, _e)}
    _out["unresolved_meta"] = _um

    print(json.dumps(_out, ensure_ascii=False, indent=2))

    # ---- 判据 ----
    _fails = []
    if len(_scan_files) < _args.min_files:
        _fails.append("覆盖 %d < 下限 %d" % (len(_scan_files), _args.min_files))
    if _missing_real:
        _fails.append("有方法却无节点的文件 %d 个（前 5：%s）"
                      % (len(_missing_real), ", ".join(_missing_real[:5])))
    if not _args.no_dump and not _dump_path:
        _fails.append("dump_graph_snapshot 返回空串（写盘守卫拒写或判据不合格）")
    if not _args.no_dump and _args.keep_latest and not _latest_path:
        _fails.append("graph_latest.json 缺失/不可解析")
    if _um.get("truncated"):
        _fails.append("unresolved 清单被截断（rows=%s cap=%s）⇒ 只能算样本"
                      % (_um.get("rows"), _um.get("cap")))

    if _fails:
        print("\n[FAIL] %d 项不达标：" % len(_fails))
        for _x in _fails:
            print("  - " + _x)
        return 1
    print("\n[PASS] 覆盖 %d 文件（>= %d）；图内有节点 %d；零方法文件 %d；图落盘=%s；常驻副本=%s"
          % (len(_scan_files), _args.min_files, len(_node_files), len(_missing_nodef),
             os.path.basename(_dump_path) or "-",
             os.path.basename(_latest_path) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
