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
import logging
import os
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# ★340K2：模块日志器（best-effort 兜底**不静默**，走 stdlib lastResort 进 stderr）
_LOG = logging.getLogger("K2Bench")

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
    """判据（★342刀3 E-2 裁决）：**双口径** —— 安全面定 verdict，吞吐只报不判。

    ① **安全面通过线**（决定 `verdict`）：三列 `reviewed/report/applied` 各轮完全相等，
       **且** 两侧处理的是同一批 id（同域）。这条线要证的是「并行没改变结论」——
       快而不准比慢更危险，故它必须能一票否决。
    ② **吞吐报告线**（只报）：墙钟降幅 `throughput_pct` 照实输出，**不设硬线**。

    为什么撤掉 30% 硬线（340B 刀8 实录）：649 条 × 4 片 × 串并各 3 轮，实测
    20.21s → 19.47s，**降 3.68%**；合成负载 0.66%。两个数都在 GIL 噪声量级 ——
    本负载是纯 CPU Python（读盘 + `ast.parse` + 图外 probe），线程并发在 GIL 下
    结构上拿不到两位数降幅。继续把 30% 当判据，只会把「并行模型选错」持续
    报成「代码变差」（340B 的 `verdict=FAIL_throughput` 就是这么来的）。
    提吞吐的正解是换并行模型（进程池），已出架构票 `tmp/p342_root/k2_arch_ticket.md`，
    结论落地前 K2 不再用吞吐卡人。
    """
    _ws = statistics.median([r["wall_sec"] for r in serial])
    _wp = statistics.median([r["wall_sec"] for r in parallel])
    _drop = (1.0 - _wp / _ws) * 100.0 if _ws > 0 else None
    _cols = ("reviewed", "report", "applied")
    _eq = {c: all(r[c] == serial[0][c] for r in serial + parallel) for c in _cols}
    _out_eq = all(_eq.values())
    # ★铁律160：分母必须与被测对象同域 —— 两侧**实际处理的 id 序列**必须完全一致
    _fp_set = {r.get("ids_fp") for r in serial + parallel}
    _same_domain = (len(_fp_set) == 1 and None not in _fp_set)
    _safety = bool(_out_eq and _same_domain)
    # 噪声带：|降幅| < 5% 视为 GIL 噪声量级（不是「略微提速」，也不是「变慢」）
    _band = ("unknown" if _drop is None else
             ("gil_noise" if abs(_drop) < 5.0 else
              ("above_noise" if _drop > 0 else "regression")))
    _legacy = ("PASS_throughput" if (_drop is not None and _drop >= 30.0 and _safety)
               else ("FAIL_output_mismatch" if not _safety else "FAIL_throughput"))
    return {
        "serial_wall_median": round(_ws, 4),
        "parallel_wall_median": round(_wp, 4),
        # ---- ① 安全面（verdict 唯一依据） ----
        "safety_pass": _safety,
        "input_same_domain": bool(_same_domain),
        "input_fp": (list(_fp_set)[0][:16] if _same_domain else None),
        "output_equal": bool(_out_eq),
        "output_equal_detail": _eq,
        "output_equal_meaning": ("三列 reviewed/report/applied 各轮完全相等 ⇒ "
                                 "**提吞吐不提产出**（快不等于多修好了东西）"),
        "verdict": ("PASS_safety" if _safety else "FAIL_output_mismatch"),
        "verdict_basis": "safety_only(产出全等∧同域)",
        # ---- ② 吞吐报告线（只报） ----
        "drop_pct": (round(_drop, 2) if _drop is not None else None),
        "throughput_pct": (round(_drop, 2) if _drop is not None else None),
        "throughput_band": _band,
        "throughput_report_only": True,
        # 历史字段保留：读方若还在读 drop_line_pct，必须同时看到「未强制」这个标记
        "drop_line_pct": 30.0,
        "throughput_line_pct": 30.0,
        "throughput_line_enforced": False,
        "verdict_legacy_throughput": _legacy,
        "verdict_legacy_note": ("340B 及以前的旧口径值（30% 硬线），仅供历史数据对表；"
                                "342刀3 起 verdict 不再由它决定"),
        "gil_note": ("合成/真实负载都是**纯 CPU Python**，GIL 下线程并发不会提速"
                     "（drop≈3.68% 属噪声量级，不是脚本坏）。降幅 >5% 才谈得上「非噪声」；"
                     "要两位数降幅得换进程池（见 k2_arch_ticket.md）。"),
        "honest_note": ("安全面通过 ≠ 提速成功；applied 恒 0 时表示未开 runner（安全闸未放宽）"),
    }



# ---------------------------------------------------------------------------
# ★340刀8：live 模式 —— 真实 per-item 任务集 + 真实取证负载
# ---------------------------------------------------------------------------
def build_live_items(n: int = 657, project_root=None) -> dict:
    """从最新一份 per-item 留样读真实任务集（确定性取前 n 条）。

    为什么不随机抽：K2 要比的是"同一批活"串行/并发的墙钟差，
    换一批就等于换考题。故按 (file, method, line) 排序后取前 n —— 同盘同批同结果。
    """
    import glob
    _root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _fs = sorted(glob.glob(os.path.join(_root, "data", "reports", "code_review",
                                        "run_*.json")))
    if not _fs:
        return {"ok": False, "items": [], "source": "",
                "reason": "无 per-item 留样（data/reports/code_review/run_*.json）"}
    _src = _fs[-1]
    with io.open(_src, encoding="utf-8") as _f:
        _d = json.load(_f)
    _rows = []
    for _x in (_d.get("items") or []):
        if not isinstance(_x, dict):
            continue
        _f_ = str(_x.get("file") or "")
        _m_ = str(_x.get("method") or "")
        if not _f_ or not _m_:
            continue
        _rows.append({"id": str(_x.get("dedup_key") or ("%s::%s" % (_f_, _m_))),
                      "file": _f_, "method": _m_,
                      "line": int(_x.get("line") or 0),
                      "type": str(_x.get("type") or ""),
                      "severity": str(_x.get("severity") or "")})
    _rows.sort(key=lambda r: (r["file"], r["method"], r["line"], r["id"]))
    _take = _rows[:max(1, int(n))]
    return {"ok": bool(_take), "items": _take, "source": _src,
            "source_mtime": os.path.getmtime(_src),
            "pool_total": len(_rows), "taken": len(_take),
            "item_count_field": _d.get("item_count"),
            "criteria_version": _d.get("criteria_version"),
            "code_version": _d.get("code_version")}


def _live_read_src(rel: str, project_root=None):
    """读生产源码（**每次真读盘**，不缓存 —— 生产 per-item 也不缓存）。"""
    _root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _p = os.path.join(_root, str(rel).replace("/", os.sep))
    try:
        with io.open(_p, encoding="utf-8", errors="replace") as _f:
            return _f.read()
    except OSError as _e:
        _LOG.warning("[340K2] 读源码失败 file=%s err=%s: %s", rel, type(_e).__name__, _e)
        return ""


def warmup_live(project_root=None) -> dict:
    """预热图外 probe 索引（一次性全仓 AST 扫），**计时单列**不计入任何一轮。"""
    _t0 = time.perf_counter()
    try:
        _root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from nucleus import self_inspector as _si
        _idx = _si._m325_build_name_index(_root)
        return {"ok": bool(_idx.get("built")),
                "sec": round(time.perf_counter() - _t0, 4)}
    except Exception as _e:  # noqa: BLE001 - 预热失败要在产物里显式可见
        _LOG.warning("[340K2] probe 索引预热失败 err=%s: %s", type(_e).__name__, _e)
        return {"ok": False, "sec": round(time.perf_counter() - _t0, 4),
                "error": "%s: %s" % (type(_e).__name__, _e)}


def _live_work(item: dict, project_root=None) -> dict:
    """单条**真实** per-item 复核：读盘 + AST + 图外 probe 四类（生产同源）。"""
    _src = _live_read_src(item.get("file"), project_root)
    _names = []
    _parse_ok = True
    try:
        _tree = ast.parse(_src)
        _names = sorted({n.id for n in ast.walk(_tree) if isinstance(n, ast.Name)})
    except SyntaxError as _e:
        _LOG.warning("[340K2] AST 解析失败 file=%s: %s", item.get("file"), _e)
        _parse_ok = False
    try:
        from nucleus import self_inspector as _si
        _hits = list(_si._m325_probe_reachable(
            {"file": item.get("file"), "method": item.get("method")},
            project_root) or [])
    except Exception as _e:  # noqa: BLE001 - 取证失败显式记，不静默当"无证据"
        _LOG.warning("[340K2] probe 取证失败 file=%s method=%s err=%s: %s",
                     item.get("file"), item.get("method"),
                     type(_e).__name__, _e)
        _hits = ["probe_error:%s" % type(_e).__name__]
    _digest = hashlib.sha256(
        ("%s|%s|%s|%s" % (item.get("id"), _parse_ok, ",".join(_hits),
                          len(_names))).encode("utf-8")).hexdigest()[:12]
    return {"id": item.get("id"), "reviewed": 1, "report": True, "applied": 0,
            "digest": _digest, "candidate": (not _hits)}


def _live_agg(rows: list, t0: float) -> dict:
    return {"wall_sec": round(time.perf_counter() - t0, 4),
            "reviewed": sum(int(r["reviewed"]) for r in rows),
            "report": sum(1 for r in rows if r["report"]),
            "applied": sum(int(r["applied"]) for r in rows),
            "ids_fp": _ids_fp([r["id"] for r in rows]),
            "verdict_fp": _ids_fp([r["id"] + "=" + r["digest"] for r in rows]),
            "candidates": sum(1 for r in rows if r["candidate"]),
            "n": len(rows)}


def run_serial_live(items: list, project_root=None) -> dict:
    _t0 = time.perf_counter()
    return _live_agg([_live_work(_it, project_root) for _it in items], _t0)


def run_parallel_live(items: list, workers: int = 3, project_root=None) -> dict:
    _t0 = time.perf_counter()
    _slices = _slice(items)
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as _ex:
        _done = list(_ex.map(
            lambda _s: [_live_work(_x, project_root) for _x in _s], _slices))
    return _live_agg([r for _chunk in _done for r in _chunk], _t0)


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

    # ★340刀8：live 模式已落地（原桩 `return 2` 移除）。
    _live_meta: dict = {}
    _warm: dict = {}
    _pr = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # ★342刀3：`tools/` 直接以脚本运行时不带项目根 ⇒ `import nucleus.*` 必失败，
    #   而 `[340K2]`/`[342K2]` 的落库留痕都靠 nucleus 日志器。补一次 sys.path，
    #   让留痕真进 pulse.log，而不是只打在 stdout 上（否则 grep 日志是 grep 不到的）。
    if _pr not in sys.path:
        sys.path.insert(0, _pr)
    if _a.mode == "live":
        _src = build_live_items(_a.items, _pr)
        if not _src.get("ok"):
            print("[340K2] live 任务集构建失败：%s" % _src.get("reason"),
                  file=sys.stderr)
            return 2
        _items = _src["items"]
        _live_meta = {k: _src.get(k) for k in
                      ("source", "source_mtime", "pool_total", "taken",
                       "item_count_field", "criteria_version", "code_version")}
        # 索引预热**计时单列**：一次性全仓 AST 扫若算进第 1 轮，
        # 会被读成"串行更慢"，属口径污染。
        _warm = warmup_live(_pr)
        print("[340K2] live 任务集=%d 条（池 %s）源=%s 预热=%.2fs"
              % (len(_items), _live_meta.get("pool_total"),
                 os.path.basename(str(_live_meta.get("source") or "")),
                 float(_warm.get("sec") or 0.0)))
        _blocks = assert_blocks(_items)
        _blocks["A4-3_live_source_ok"] = bool(_live_meta.get("taken"))
        _blocks["A4-4_probe_index_warm"] = bool(_warm.get("ok"))
    else:
        _items = build_items(_a.items)
        _blocks = assert_blocks(_items)

    _rounds: list = []
    for _i in range(1, max(1, _a.rounds) + 1):
        _r = (run_serial_live(_items, _pr) if _a.mode == "live"
              else run_serial(_items))
        _r.update({"phase": "serial", "round": _i})
        _rounds.append(_r)
    for _i in range(1, max(1, _a.rounds) + 1):
        _r = (run_parallel_live(_items, _a.workers, _pr) if _a.mode == "live"
              else run_parallel(_items, _a.workers))
        _r.update({"phase": "parallel", "round": _i})
        _rounds.append(_r)
    _serial = [r for r in _rounds if r["phase"] == "serial"]
    _parallel = [r for r in _rounds if r["phase"] == "parallel"]
    _summary = _conclude(_serial, _parallel)
    # ★340刀8：live 侧额外一条**非空转**断言 —— 逐条裁决指纹也必须一致。
    #   只看三列计数是不够的："reviewed 相等"不代表"判的是同一批结论"。
    if _a.mode == "live":
        _vfp = {r.get("verdict_fp") for r in _rounds}
        _verdict_equal = (len(_vfp) == 1 and None not in _vfp)
        _summary["verdict_fp_equal"] = bool(_verdict_equal)
        _summary["verdict_fp"] = (list(_vfp)[0][:16] if _verdict_equal else None)
        _summary["applied_equal"] = bool(
            (_summary.get("output_equal_detail") or {}).get("applied"))
    # ★342刀3：**双口径判据行**（两种模式都打，便于门禁/报表直接 grep）。
    #   一行里同时给出「安全面结论」与「吞吐落带」，避免读方把 drop 当通过线。
    _line342 = ("[342K2] mode=%s verdict=%s basis=%s safety_pass=%s "
                "output_equal=%s same_domain=%s | throughput_pct=%s band=%s "
                "report_only=True line_enforced=False | items=%d rounds=%d workers=%d"
                % (_a.mode, _summary.get("verdict"), _summary.get("verdict_basis"),
                   _summary.get("safety_pass"), _summary.get("output_equal"),
                   _summary.get("input_same_domain"), _summary.get("throughput_pct"),
                   _summary.get("throughput_band"), len(_items),
                   _a.rounds, _a.workers))
    print(_line342)
    _summary["trace_line"] = _line342
    try:
        from nucleus.logger import get_module_logger as _gml342
        _gml342("K2Bench").info(_line342)
    except Exception as _e342:  # noqa: BLE001 - 日志器不可用不影响产物落盘
        _LOG.warning("[342K2] nucleus 日志器不可用 err=%s: %s",
                     type(_e342).__name__, _e342)
    _payload = {
        "meta": {"batch": 339, "knife": 6, "k2_version": "339.k2.v1",
                 "ts": time.time(), "mode": _a.mode, "tag": _a.tag,
                 "seed": _SEED, "items": _a.items, "slices": _N_SLICES,
                 "rounds_per_phase": _a.rounds, "workers": _a.workers,
                 "workload": ("synthetic_ast_scan（合成负载，绝对值不代表生产）"
                              if _a.mode == "synthetic"
                              else "live_per_item（真实留样 + 图外 probe 四类）"),
                 "safety": dict(_SAFETY),
                 "writes_prod_data": False,
                 "live": _live_meta,
                 "warmup": _warm},
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
    if _a.mode == "live":
        # ★340K2 留痕：一行可 grep，含降幅与产出率判定（任务书验收④）
        _line = (
            "[340K2] live 对表: serial=%.2fs parallel=%.2fs drop=%.2f%% "
            "output_equal=%s applied_equal=%s verdict_fp_equal=%s verdict=%s "
            "drop_band=%s "
            "items=%d slices=%d workers=%d"
            % (float(_summary.get("serial_wall_median") or 0.0),
               float(_summary.get("parallel_wall_median") or 0.0),
               float(_summary.get("drop_pct") or 0.0),
               _summary.get("output_equal"),
               _summary.get("applied_equal"),
               _summary.get("verdict_fp_equal"), _summary.get("verdict"),
               _summary.get("throughput_band"),
               len(_items), _N_SLICES, _a.workers))
        print(_line)
        try:
            from nucleus.logger import get_module_logger as _gml
            _gml("K2Bench").info(_line)
        except Exception as _e:  # noqa: BLE001 - 日志器不可用不影响产物落盘
            _LOG.warning("[340K2] nucleus 日志器不可用 err=%s: %s",
                         type(_e).__name__, _e)
    print(json.dumps({"ok": bool(_summary.get("safety_pass")),
                      "verdict": _summary["verdict"],
                      "verdict_basis": _summary.get("verdict_basis"),
                      "drop_pct": _summary["drop_pct"],
                      "throughput_band": _summary.get("throughput_band"),
                      "throughput_report_only": _summary.get("throughput_report_only"),
                      "output_equal": _summary["output_equal"],
                      "assert_blocks": _blocks,
                      "out": _out}, ensure_ascii=False))
    return 0 if _blocks["A1-1_slice_conserved"] and _blocks["A1-2_no_dup"] else 1


if __name__ == "__main__":
    sys.exit(main())
