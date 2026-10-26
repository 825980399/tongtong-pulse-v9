# -*- coding: utf-8 -*-
"""372 刀11：进程内注入采样归属 —— 五维分器 + no-gc 对照 + 注入片段生成器。

票 `T-371-进程内注入采样归属-1`（P2）｜裁决：本批**只交付静态通路与探针就绪面**，
活体 ≥30min 长窗 + no-gc 对照并入重启观察窗；归属坐实后另立修复票。

## 与既有件的关系（复用，不复制一套判定）
- 365 B2 `tools/memory_attribution_probe.py`：进程内分器的**第一版**（GC/类型/线程）。
- 371 刀1 `tools/ci/probe_memory_attribution_371.py::sample_inproc`：两档口径分离的**第二版**。
- 本件 = 第三版：**只补维度与对照分支**，取数逻辑直接 `import` 371 的 `sample_inproc`
  （如果它不可用则退化为本件自带实现，并在 `err` 里如实标注，不静默降级）。

## 为什么要有"注入片段生成器"
本仓**没有命令面**（`grep command_bus / COMMAND_FILE / COMMAND_DIR` 全零命中，
`CompanionBridge` 也无 command/dispatch/handle 方法）⇒ 任务书说的"挂到活体命令面"
**无处可挂**。因此本件改成：输出一段**可直接粘贴进活体进程**（IDE 调试器 Console /
临时 REPL / py-spy dump 后的 attach 会话）的代码，由操作者在重启窗执行。
**不新起定时器、不进心跳链、不被生产代码 import**。

用法：
  python -X utf8 tools/ci/memprobe_372_inject.py --emit-inject-snippet
  python -X utf8 tools/ci/memprobe_372_inject.py --self --no-gc --out-json tmp/x.json
"""
from __future__ import annotations

import argparse
import gc
import io
import json
import logging
import os
import sys
import threading
import time
from collections import Counter

_LOG = logging.getLogger("pulse.tools.memprobe372")
if not _LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _LOG.addHandler(_h)
    _LOG.setLevel(logging.INFO)

#: 候选五类（N1 取证件 §四口径）——分器必须**逐类**给读数，别混成一个总数
CANDIDATES = (
    "image_mb",            # 不可释放镜像
    "thread_stacks",       # 线程栈局部（191 线程）
    "node_pool_resident",  # 节点池常驻
    "patch_ast_cache",     # 补丁池会话 AST 缓存
    "handle_growth",       # 句柄缓增
)


def _sample_inproc_base(top_n: int = 15) -> dict:
    """取 371 刀1 的 `sample_inproc`；不可用则本地实现并在 err 标注（不静默降级）。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from probe_memory_attribution_371 import sample_inproc as _si  # type: ignore
        return {"ok": True, "fn": _si, "err": None, "source": "probe_memory_attribution_371"}
    except Exception as _e:  # noqa: BLE001
        _LOG.warning("[372刀11] 371 探针不可 import，退化本件实现: %s: %s",
                     type(_e).__name__, _e)
        return {"ok": False, "fn": None, "err": "%s: %s" % (type(_e).__name__, _e),
                "source": "fallback_local"}


def _gc_generations() -> list:
    """GC 分代三维读数（代 0/1/2）：counts + stats。"""
    out = []
    try:
        _c = gc.get_count()
        _s = gc.get_stats()
        for _i in range(3):
            _row = {"gen": _i,
                    "count": int(_c[_i]) if _i < len(_c) else None,
                    "collections": None, "collected": None, "uncollectable": None}
            if _i < len(_s) and isinstance(_s[_i], dict):
                _row["collections"] = _s[_i].get("collections")
                _row["collected"] = _s[_i].get("collected")
                _row["uncollectable"] = _s[_i].get("uncollectable")
            out.append(_row)
    except Exception as _e:  # noqa: BLE001
        _LOG.warning("[372刀11] gc 分代读取失败: %s: %s", type(_e).__name__, _e)
        return [{"gen": i, "err": "%s: %s" % (type(_e).__name__, _e)} for i in range(3)]
    return out


def _thread_dim() -> dict:
    """线程维度：总数 + 按 name 前缀聚合（daemon/alive 分开计）。"""
    _rows = []
    try:
        for _t in threading.enumerate():
            _rows.append({"name": _t.name, "daemon": bool(_t.daemon),
                          "alive": bool(_t.is_alive())})
    except Exception as _e:  # noqa: BLE001
        _LOG.warning("[372刀11] 线程枚举失败: %s: %s", type(_e).__name__, _e)
        return {"n": None, "by_prefix": {}, "err": "%s: %s" % (type(_e).__name__, _e)}
    _pref = Counter((r["name"].split("-")[0] or "?") for r in _rows)
    return {"n": len(_rows), "daemon_n": sum(1 for r in _rows if r["daemon"]),
            "by_prefix": dict(_pref.most_common(20)), "rows": _rows[:50], "err": None}


def _container_dim(top_n: int = 15) -> dict:
    """容器维度：dict/list/set/tuple 的数量与浅尺寸（gc 可见对象内统计）。"""
    _cnt = Counter()
    _size = Counter()
    _n = 0
    try:
        for _o in gc.get_objects():
            _n += 1
            _t = type(_o).__name__
            if _t in ("dict", "list", "set", "tuple", "frozenset", "bytearray", "bytes"):
                _cnt[_t] += 1
                try:
                    _size[_t] += sys.getsizeof(_o)
                except TypeError as _e:
                    # 少数对象不支持 getsizeof —— 计 0 不中断（与 371 同口径）
                    _LOG.debug("[372刀11] getsizeof 不支持: %s", type(_e).__name__)
    except Exception as _e:  # noqa: BLE001
        _LOG.warning("[372刀11] 容器维度失败: %s: %s", type(_e).__name__, _e)
        return {"err": "%s: %s" % (type(_e).__name__, _e)}
    return {
        "objects_total": _n,
        "by_type_n": dict(_cnt.most_common(top_n)),
        "by_type_mb": {k: round(v / 1048576.0, 3) for k, v in _size.most_common(top_n)},
        "err": None,
    }


def _type_top(top_n: int = 15) -> list:
    """类型 Top（按实例数）：与容器维度互补 —— 容器看量，类型 Top 看面。"""
    _cnt = Counter()
    try:
        for _o in gc.get_objects():
            _cnt[type(_o).__name__] += 1
    except Exception as _e:  # noqa: BLE001
        _LOG.warning("[372刀11] 类型 Top 失败: %s: %s", type(_e).__name__, _e)
        return []
    return [{"type": k, "n": v} for k, v in _cnt.most_common(top_n)]


def sample_inproc_v2(top_n: int = 15, no_gc: bool = False) -> dict:
    """五维进程内分器（**只在目标进程内调用才有意义**）。

    Args:
        top_n: 类型/容器 Top 截断条数。
        no_gc: **对照分支** —— True 时**不主动 gc.collect()**。
            为什么要有它：`gc.collect()` 会临时改变堆形状，长窗采样若每次都收一遍，
            读数被"我们自己的采样动作"污染（铁律188 自噬同型）。归属归因必须与
            no-gc 对照差分，才能回答"这些对象是活着的还是等着被收的"。

    Returns:
        dict，含 `mode/ts/pid/dims/no_gc/err`。**异常一律结构化落 err，不上抛**
        （长窗采样中途崩掉会毁掉整段数据，比缺一维更糟）。
    """
    _t0 = time.time()
    _err = {}
    if not no_gc:
        try:
            gc.collect()   # 规范化堆形状，让计数可复跑
        except Exception as _e:  # noqa: BLE001
            _LOG.warning("[372刀11] 预收 gc 失败（继续采样）: %s: %s", type(_e).__name__, _e)
            _err["gc_collect"] = "%s: %s" % (type(_e).__name__, _e)

    def _safe(fn, name, *a, **kw):
        """每维独立兜底：**一维崩不得带走整拍**（长窗数据连续性优先）。"""
        try:
            return fn(*a, **kw)
        except Exception as _e:  # noqa: BLE001
            _LOG.warning("[372刀11] 维度 %s 失败（已降级为空）: %s: %s",
                         name, type(_e).__name__, _e)
            _err[name] = "%s: %s" % (type(_e).__name__, _e)
            return None

    _base = _sample_inproc_base(top_n)
    _gc = _safe(_gc_generations, "gc_generations")
    _th = _safe(_thread_dim, "threads")
    _ct = _safe(_container_dim, "containers", top_n)
    _tp = _safe(_type_top, "type_top", top_n)
    _org = _safe(_organs_dim, "organs")

    if _base["err"]:
        _err["base_import"] = _base["err"]

    return {
        "mode": "self_v2",
        "pid": os.getpid(),
        "ts": time.time(),
        "elapsed_ms": round((time.time() - _t0) * 1000.0, 1),
        "no_gc": bool(no_gc),
        "note": "★仅反映**本进程**；跨进程拿不到 gc 对象图。活体归属须在目标进程内调用本函数。",
        "base_source": _base["source"],
        "dims": {
            "gc_generations": _gc,          # ① GC 分代
            "threads": _th,                 # ② 线程
            "containers": _ct,              # ③ 容器
            "type_top": _tp,                # ④ 类型 Top
            "organs": _org,                # ⑤ 器官
        },
        "candidates": list(CANDIDATES),
        "err": _err or None,
    }


def _organs_dim() -> dict:
    """器官维度：**不 import 生产模块**（本件零生产依赖），改用 gc 里按类名归类。

    判据：类名含 Pulse 且是对象实例 ⇒ 归"疑似器官"；再按模块前缀粗分。
    这是**近似**读数，证据件里如实标注（不宣称这是精确的器官清单）。
    """
    _cnt = Counter()
    try:
        for _o in gc.get_objects():
            _cls = type(_o)
            _nm = _cls.__name__
            if "Pulse" in _nm and getattr(_cls, "__module__", ""):
                _mod = str(_cls.__module__).split(".")[0]
                _cnt["%s.%s" % (_mod, _nm)] += 1
    except Exception as _e:  # noqa: BLE001
        _LOG.warning("[372刀11] 器官维度（近似）失败: %s: %s", type(_e).__name__, _e)
        return {"err": "%s: %s" % (type(_e).__name__, _e)}
    return {"approximate": True,
            "note": "按 gc 中类名含 Pulse 的实例近似统计；精确器官清单见 cognitive/organ_inventory.py",
            "top": dict(_cnt.most_common(20)), "err": None}


INJECT_SNIPPET = '''# ===== 372 刀11：活体进程内注入片段（粘贴进目标进程的调试器 Console / REPL）=====
# 前提：目标进程已停在这一帧可执行上下文（IDE 断点 / py-spy attach 后 exec）。
# 本片段不注册定时器、不进心跳链、不 import 任何生产模块。
import sys, json, time
_P = r"{probe_path}"
if _P not in sys.path:
    sys.path.insert(0, _P)
import memprobe_372_inject as _mp

# ① 基线：先收 gc 再采（可比），② 对照：no_gc 再采一次（看"活着的"有多少）
_rows = []
_t_end = time.time() + {duration_s}
while time.time() < _t_end:
    _rows.append({{"kind": "gc", "d": _mp.sample_inproc_v2(no_gc=False)}})
    _rows.append({{"kind": "nogc", "d": _mp.sample_inproc_v2(no_gc=True)}})
    time.sleep({interval_s})

with open(r"{out_json}", "w", encoding="utf-8") as _f:
    json.dump({{"pid": _mp.os.getpid(), "rows": _rows}}, _f, ensure_ascii=False, indent=1)
print("[372刀11] 采样完成: %d 拍 ⇒ %s" % (len(_rows), r"{out_json}"))
# ===== 结束 =====
'''


def emit_inject_snippet(duration_s: int = 1800, interval_s: int = 5,
                        out_json: str = "tmp/memprobe_372_live.json") -> str:
    """生成可直接粘贴的注入片段（默认 30min / 5s 一拍 = 360 对样本）。"""
    return INJECT_SNIPPET.format(
        probe_path=os.path.dirname(os.path.abspath(__file__)),
        duration_s=int(duration_s), interval_s=int(interval_s), out_json=out_json)


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="372 刀11 进程内采样通路（静态就绪面）")
    _ap.add_argument("--self", action="store_true", help="在本进程跑一次五维采样")
    _ap.add_argument("--no-gc", action="store_true", help="no-gc 对照分支")
    _ap.add_argument("--top-n", type=int, default=15)
    _ap.add_argument("--emit-inject-snippet", action="store_true")
    _ap.add_argument("--duration-s", type=int, default=1800)
    _ap.add_argument("--interval-s", type=int, default=5)
    _ap.add_argument("--out-json", default=None)
    _a = _ap.parse_args(argv)

    if _a.emit_inject_snippet:
        print(emit_inject_snippet(_a.duration_s, _a.interval_s,
                                 _a.out_json or "tmp/memprobe_372_live.json"))
        return 0
    if _a.self:
        _d = sample_inproc_v2(_a.top_n, no_gc=_a.no_gc)
        _txt = json.dumps(_d, ensure_ascii=False, indent=1)
        if _a.out_json:
            io.open(_a.out_json, "w", encoding="utf-8", newline="\n").write(_txt + "\n")
            print("[372刀11] 写出 %s" % _a.out_json)
        else:
            print(_txt)
        return 0
    _ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
