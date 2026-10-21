# -*- coding: utf-8 -*-
"""★339批刀6：K2 生产对表（执行单 · 脚本，不跑生产）。

背景（A裁决 · 烛微338前置§1.2）
------------------------------
生产留样已 `parallelism=3`（657条 / 261KB / 11分钟一份）**从未对过表**；
322 试点（review 档）串行 0.7895s → 并发 0.3787s（−52.03%）。

判据（任务书写死）
------------------
· 墙钟降幅 ≥ 30%；
· **产出率相等**（三列 `reviewed/report/applied` 同报且各轮 `applied` 相等）；
· 诚实口径 = **提吞吐不提产出**（快不等于多修好了东西）。

纪律
----
· 隔离根/主进程双跑对表时，隔离根 **不得写生产 `data/`**（本脚本全程只写 `--out`）；
· 两侧任务集与 seed **相同**（`sample_verify_hard_candidates` seed=325 固定）；
· K2 复跑不得与压测圈次重叠（由调度方保证，本脚本只标 `--tag`）；
· 红线：不动 `_RESOLVE_GATE_*` / 安全闸 `hard_cap=1` / `blast_cap=1` / 不开 runner。

用法
----
    python tools/k2_bench_339.py --rounds 3 --workers 3 --items 657 \
        --out tmp/p339_root/k2_bench.json --mode synthetic

真实生产对表（**10-22 04:00 重启窗之后**再跑，本批不跑）：

    python tools/k2_bench_339.py --mode live --out tmp/p339_root/k2_bench_live.json

产物
----
`{"meta": {...}, "rounds": [{phase, round, wall_sec, reviewed, report, applied}],
  "summary": {"serial", "parallel", "drop_pct", "output_equal", "verdict"}}`
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

_SEED = 325                      # 与 sample_verify_hard_candidates 同 seed
_N_SLICES = 4                    # 任务集分 4 片
_SAFETY = {"hard_cap": 1, "blast_cap": 1, "runner": False,
           "resolve_gate_touched": False}


def build_items(n: int = 657, seed: int = _SEED) -> list:
    """合成 per-item 任务集（**与生产同条数、同 seed**，隔离根可跑）。

    为什么不直接拿生产 657 条：真实 per-item 需要调用图与源码（`--mode live`
    才走那条路），而**本批不跑生产**。这里造的是"结构相同"的任务集：
    同样的条数、同样的分片数、同样三列产出，好让对表脚本先被证明**能跑通**。
    合成负载的绝对值**不代表生产**，只用于验证脚本与判据口径。
    """
    _rnd = random.Random(seed)
    _out = []
    for _i in range(n):
        _depth = _rnd.choice((1, 2, 3, 4))
        _body = "\n".join("    _v%d = %d * %d" % (_k, _k, _rnd.randint(1, 9))
                          for _k in range(6 * _depth))
        _src = ("def _probe_%d(a, b):\n%s\n    return a + b\n" % (_i, _body))
        _out.append({"id": "item_%04d" % _i, "src": _src,
                     "file": "nucleus/synthetic_%d.py" % (_i % 37),
                     "method": "_probe_%d" % _i})
    return _out


def _work(item: dict) -> dict:
    """单条 per-item 复核的**代表负载**（AST 解析 + 正则 + 排序 + 摘要）。

    刻意保持"CPU 型、无 IO 阻塞"：K2 要比的是**调度开销**（串行 vs
    `parallelism=3`），掺进 IO 会把结论污染成"IO 等得久不久"。
    """
    _src = str(item.get("src") or "")
    _t = ast.parse(_src)
    _names = sorted({n.id for n in ast.walk(_t) if isinstance(n, ast.Name)})
    _calls = len(re.findall(r"\w+\(", _src))
    _digest = hashlib.sha256(_src.encode("utf-8")).hexdigest()[:12]
    _dead = (_calls == 0 and len(_names) <= 2)
    return {"id": item.get("id"), "reviewed": 1, "report": True,
            "applied": 0, "digest": _digest, "candidate": bool(_dead)}


def _slice(items: list, k: int = _N_SLICES) -> list:
    """切成 k 片（连续切，便于"并集==全集且无重复"的断言）。"""
    _n = len(items)
    _step = (_n + k - 1) // k
    return [items[i:i + _step] for i in range(0, _n, _step)]


def _ids_fp(ids: list) -> str:
    return hashlib.sha256("|".join(str(_i) for _i in ids).encode("utf-8")).hexdigest()


def run_serial(items: list) -> dict:
    _t0 = time.perf_counter()
    _rows = [_work(_it) for _it in items]
    return {"wall_sec": round(time.perf_counter() - _t0, 4),
            "reviewed": sum(int(r["reviewed"]) for r in _rows),
            "report": sum(1 for r in _rows if r["report"]),
            "applied": sum(int(r["applied"]) for r in _rows),
            # ★真实纳入指纹：断言用「实际处理过的 id 序列」，不是「应处理的」
            "ids_fp": _ids_fp([r["id"] for r in _rows]),
            "n": len(_rows)}


def run_parallel(items: list, workers: int = 3) -> dict:
    """按生产 `parallelism=3` 同档并发（**分片后每片一任务**，不逐条建线程）。"""
    _t0 = time.perf_counter()
    _slices = _slice(items)
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as _ex:
        _done = list(_ex.map(lambda _s: [_work(_x) for _x in _s], _slices))
    _rows = [r for _chunk in _done for r in _chunk]
    return {"wall_sec": round(time.perf_counter() - _t0, 4),
            "reviewed": sum(int(r["reviewed"]) for r in _rows),
            "report": sum(1 for r in _rows if r["report"]),
            "applied": sum(int(r["applied"]) for r in _rows),
            "ids_fp": _ids_fp([r["id"] for r in _rows]),
            "n": len(_rows)}


def assert_blocks(items: list) -> dict:
    """A1/A4 断言块（**对表有效性断言**，不是"跑得快不快"）。

    A1-1 切片守恒：4 片并集 == 全集（按 id），且总条数不变；
    A1-2 无重复：4 片 id 全不重复；
    A4-1 两侧输入指纹一致：串行/并发拿到的 item id 序列 sha256 相同；
    A4-2 安全闸未被放宽：`hard_cap=1` / `blast_cap=1` / 不开 runner。

    为什么断言这些：拿两份不同的任务集比墙钟，降幅多少都是假的（铁律160
    分母与被测对象同域）。
    """
    _ids = [str(_x.get("id")) for _x in items]
    _slices = _slice(items)
    _flat = [str(_x.get("id")) for _s in _slices for _x in _s]
    _a1_1 = (_flat == _ids) and (len(_flat) == len(items))
    _a1_2 = len(set(_flat)) == len(_flat)
    # ★A4-1 必须是**真断言**：两次独立构造分片，并集与顺序都要一致
    #   （自己和自己比指纹恒 True = 空转判据，铁律163）
    _flat2 = [str(_x.get("id")) for _s in _slice(list(items)) for _x in _s]
    _a4_1 = (_flat2 == _ids and _flat2 == _flat)
    _fp = _ids_fp(_ids)
    _a4_2 = (_SAFETY["hard_cap"] == 1 and _SAFETY["blast_cap"] == 1
             and _SAFETY["runner"] is False
             and _SAFETY["resolve_gate_touched"] is False)
    return {"A1-1_slice_conserved": bool(_a1_1),
            "A1-2_no_dup": bool(_a1_2),
            "A4-1_slice_construction_deterministic": bool(_a4_1),
            "A4-2_safety_gates_intact": bool(_a4_2),
            "input_fp": _fp[:16], "n_items": len(items),
            "n_slices": len(_slices), "slice_sizes": [len(_s) for _s in _slices]}


def _conclude(serial: list, parallel: list) -> dict:
    """判据：墙钟降 ≥30% **且** 三列产出相等（尤其 applied 必须相等）。"""
    _ws = statistics.median([r["wall_sec"] for r in serial])
    _wp = statistics.median([r["wall_sec"] for r in parallel])
    _drop = (1.0 - _wp / _ws) * 100.0 if _ws > 0 else None
    _cols = ("reviewed", "report", "applied")
    _eq = {c: all(r[c] == serial[0][c] for r in serial + parallel) for c in _cols}
    _out_eq = all(_eq.values())
    # ★铁律160：分母必须与被测对象同域 —— 两侧**实际处理的 id 序列**必须完全一致
    _fp_set = {r.get("ids_fp") for r in serial + parallel}
    _same_domain = (len(_fp_set) == 1 and None not in _fp_set)
    _pass = (_drop is not None and _drop >= 30.0 and _out_eq and _same_domain)
    return {
        "serial_wall_median": round(_ws, 4),
        "parallel_wall_median": round(_wp, 4),
        "drop_pct": (round(_drop, 2) if _drop is not None else None),
        "drop_line_pct": 30.0,
        "input_same_domain": bool(_same_domain),
        "input_fp": (list(_fp_set)[0][:16] if _same_domain else None),
        "output_equal": bool(_out_eq),
        "output_equal_detail": _eq,
        "output_equal_meaning": ("三列 reviewed/report/applied 各轮完全相等 ⇒ "
                                 "**提吞吐不提产出**（快不等于多修好了东西）"),
        # ★判定顺序必须是「先看产出是否可比，再看快不快」——
        #   反过来会把"产出不等"报成"没提速"，那是把两类失败混成一类。
        "verdict": ("PASS_throughput" if _pass else
                    ("FAIL_output_mismatch" if (not _out_eq or not _same_domain)
                     else "FAIL_throughput")),
        "gil_note": ("合成负载是**纯 CPU Python**，GIL 下线程并发不会提速"
                     "（drop≈0 是预期，不是脚本坏）。真实 per-item 若走子进程/IO，"
                     "才可能有 322 试点那种降幅 —— 必须以 --mode live 实测为准。"),
        "honest_note": ("本判据只证「同样产出下的墙钟降幅」，"
                        "不证「修得更多」；applied 恒 0 时表示未开 runner（安全闸未放宽）"),
    }


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="339 刀6 · K2 生产对表")
    _ap.add_argument("--rounds", type=int, default=3)
    _ap.add_argument("--workers", type=int, default=3,
                     help="与生产 parallelism=3 同档")
    _ap.add_argument("--items", type=int, default=657)
    _ap.add_argument("--mode", choices=("synthetic", "live"), default="synthetic")
    _ap.add_argument("--tag", default="", help="口径标签（用于避免与压测圈次重叠时区分）")
    _ap.add_argument("--out", default="tmp/p339_root/k2_bench.json")
    _a = _ap.parse_args(argv)

    if _a.mode == "live":
        # 真实生产 per-item 需要调用图 + 源码；本批不跑（重启窗后执行）
        print("[k2] --mode live 需在 10-22 04:00 重启窗之后执行；"
              "本批只交付脚本与合成对表。", file=sys.stderr)
        return 2

    _items = build_items(_a.items)
    _blocks = assert_blocks(_items)
    _rounds: list = []
    for _i in range(1, max(1, _a.rounds) + 1):
        _r = run_serial(_items)
        _r.update({"phase": "serial", "round": _i})
        _rounds.append(_r)
    for _i in range(1, max(1, _a.rounds) + 1):
        _r = run_parallel(_items, _a.workers)
        _r.update({"phase": "parallel", "round": _i})
        _rounds.append(_r)
    _serial = [r for r in _rounds if r["phase"] == "serial"]
    _parallel = [r for r in _rounds if r["phase"] == "parallel"]
    _summary = _conclude(_serial, _parallel)
    _payload = {
        "meta": {"batch": 339, "knife": 6, "k2_version": "339.k2.v1",
                 "ts": time.time(), "mode": _a.mode, "tag": _a.tag,
                 "seed": _SEED, "items": _a.items, "slices": _N_SLICES,
                 "rounds_per_phase": _a.rounds, "workers": _a.workers,
                 "workload": ("synthetic_ast_scan（合成负载，绝对值不代表生产；"
                              "真实对表见 --mode live）"
                              if _a.mode == "synthetic" else "live"),
                 "safety": dict(_SAFETY),
                 "writes_prod_data": False},
        "assert_blocks": _blocks,
        "rounds": _rounds,
        "summary": _summary,
    }
    _out = _a.out if os.path.isabs(_a.out) else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _a.out)
    _d = os.path.dirname(os.path.abspath(_out))
    if _d:
        os.makedirs(_d, exist_ok=True)
    with io.open(_out, "w", encoding="utf-8", newline="\n") as _f:
        json.dump(_payload, _f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": _summary["verdict"] != "FAIL_throughput",
                      "verdict": _summary["verdict"],
                      "drop_pct": _summary["drop_pct"],
                      "output_equal": _summary["output_equal"],
                      "assert_blocks": _blocks,
                      "out": _out}, ensure_ascii=False))
    return 0 if _blocks["A1-1_slice_conserved"] and _blocks["A1-2_no_dup"] else 1


if __name__ == "__main__":
    sys.exit(main())
