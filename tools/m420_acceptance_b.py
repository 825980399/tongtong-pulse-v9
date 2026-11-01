#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m420_acceptance_b.py —— B 类「代码已施工 · 等活体」11 条判据一键验收（第420批）。

## 为什么要这个工具

`docs/路灯与星轨对话/交付报告/跨批遗留清单_20261101.md` 的 B 类 7 项全部是
「代码已写好、只需跑起来看一眼」。但『看一眼』散在 7 个地方、口径各异，
结果就是**每批都说"待活体验收"，然后没验**（416 起连续三批）。

本工具把 7 项拆成 11 条**可执行判据**，一条命令出结论。判据全部来自
**已落盘的产物 / 日志 / 快照**，不需要在框架进程内执行任何代码。

## 三态（★`unknown` 必须是一等公民）

| 态 | 含义 |
|---|---|
| `PASS` | 有正向观测数据，且达到判据线 |
| `FAIL` | 有观测数据，但没达到判据线 |
| `WAIT` | **没有观测数据** —— 框架没跑、产物没生成、字段还没写。**不是失败** |

把 `WAIT` 当 `FAIL` 会让"还没验"和"验了没过"混为一谈；把 `WAIT` 当 `PASS`
就是自欺。所以两者分列，退出码也分开（见 `--help`）。

## 用法

```
python tools/m420_acceptance_b.py                # 人类可读表
python tools/m420_acceptance_b.py --json         # 机读
python tools/m420_acceptance_b.py --since-min 30 # 放宽/收紧日志回看窗口
```

退出码：`0` 全 PASS ｜ `1` 存在 FAIL ｜ `2` 无 FAIL 但有 WAIT ｜ `3` 读不到仓库根
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL, WAIT = "PASS", "FAIL", "WAIT"

#: 判据线（集中在此，便于审阅"凭什么算过"）
TH_PATTERNS_FRESH_SEC = 300.0      # patterns.json 5 分钟内必须推进
TH_ALIVE_SEC = 300.0               # health_snapshot 5 分钟内有写入 ⇒ 框架活
TH_THREADS = 60                    # 418 刀3②：线程上限 61→29，验收线 <60
TH_GROWTH_MB_PER_MIN = 200.0       # 2GB / 10min = 200MB/min
TH_CHANNEL_TOP_SHARE = 0.60        # 单渠道最大份额（418 刀2 均衡前是 66.7%）
TH_CHANNEL_MIN_USED = 3            # 至少用到几个渠道
TH_PERF_DELTA_RATIO = 0.90         # effect_verify 里 perf_delta_ms 非 None 的比例


def _p(rel: str) -> str:
    return os.path.join(ROOT, rel.replace("/", os.sep))


#: 留痕去重键（同一处只打一次，避免热循环里逐行刷屏）
_WARNED: set = set()


def _warn(where: str, e: BaseException) -> None:
    """★420：本工具是 **CI 件**，不引框架 logger（零依赖，同
    `check_thread_spawn_baseline.py` 的 `_log`）。

    但「零依赖」不等于「静默」：每个 except 都必须留痕（铁律 4：
    `pass` / 赋值 / `continue` / `return` 同罪，`tools/` 同受管）。
    折中是**按 where 去重**——热循环（逐行解析 trace）里的 except
    不能逐条刷屏，但也绝不能一声不响。
    """
    if where in _WARNED:
        return
    _WARNED.add(where)
    sys.stderr.write("[m420_acceptance_b] %s %s: %s\n"
                     % (where, type(e).__name__, e))


def _read_json(path: str):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        _warn("_read_json:" + path, e)
        return None


def _today() -> str:
    return time.strftime("%Y%m%d", time.localtime())


def _mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError as e:
        _warn("_mtime:" + path, e)
        return None


def _size(path: str):
    try:
        return os.path.getsize(path)
    except OSError as e:
        _warn("_size:" + path, e)
        return None


def _tail_lines(path: str, max_bytes: int = 4 << 20):
    """读文件尾部若干字节并按行切 —— 大日志不整读。"""
    try:
        _sz = os.path.getsize(path)
        with open(path, "rb") as f:
            if _sz > max_bytes:
                f.seek(_sz - max_bytes)
                f.readline()  # 丢掉半行
            return f.read().decode("utf-8", "ignore").splitlines()
    except OSError as e:
        _warn("_tail_lines:" + path, e)
        return []


def _log_ts(line: str):
    """从行首 `YYYY-MM-DD HH:MM:SS` 取 epoch；取不到返回 None。"""
    try:
        return time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError) as e:
        _warn("_log_ts", e)
        return None


class R:
    """一条判据的结果。"""

    __slots__ = ("cid", "name", "state", "value", "detail")

    def __init__(self, cid, name, state, value=None, detail="") -> None:
        self.cid, self.name, self.state = cid, name, state
        self.value, self.detail = value, detail

    def as_dict(self):
        return {"id": self.cid, "name": self.name, "state": self.state,
                "value": self.value, "detail": self.detail}


# --------------------------------------------------------------- 活性
def check_alive(now: float):
    """框架是否活着：health_snapshot 的 timestamp 是否新鲜。"""
    p = _p("data/monitor/health_snapshot.json")
    d = _read_json(p)
    if not isinstance(d, dict):
        return False, "health_snapshot.json 缺失或损坏"
    _ts = d.get("timestamp")
    if not isinstance(_ts, (int, float)):
        return False, "health_snapshot 无 timestamp"
    _age = now - float(_ts)
    if _age > TH_ALIVE_SEC:
        return False, ("health_snapshot 已 %.0fs 未更新（阈值 %.0fs），pid=%r"
                       % (_age, TH_ALIVE_SEC, d.get("pid")))
    return True, "health_snapshot %.0fs 前更新，pid=%r gen=%r" % (
        _age, d.get("pid"), d.get("proc_gen"))


# --------------------------------------------------------------- 判据
def c_patterns(now: float, alive: bool):
    """L09① `patterns.json` 是否在推进（418 刀1 的活体判据）。

    ★框架不活时判 **WAIT**：`mtime` 是"最后有人写过"，停机后它当然不动，
    拿它判 FAIL 等于把"没开机"记成"修复没生效"。
    """
    p = _p("data/stream_miner/patterns.json")
    _m, _s = _mtime(p), _size(p)
    if _m is None:
        return R("L09-1", "patterns.json 5 分钟内推进", WAIT, None, "文件不存在")
    _age = now - _m
    _v = {"age_sec": round(_age, 1), "size_b": _s, "framework_alive": alive}
    if not alive:
        return R("L09-1", "patterns.json 5 分钟内推进", WAIT, _v,
                 "框架未运行 ⇒ 无活体读数；末次写入 %.0fs 前，%s B" % (_age, _s))
    if _age <= TH_PATTERNS_FRESH_SEC:
        return R("L09-1", "patterns.json 5 分钟内推进", PASS, _v,
                 "%.1fs 前有写入，%s B" % (_age, _s))
    return R("L09-1", "patterns.json 5 分钟内推进", FAIL, _v,
             "已 %.0fs 未写入（阈值 %.0fs），%s B" % (_age, TH_PATTERNS_FRESH_SEC, _s))


def c_threads(alive: bool):
    """L09② 线程数 < 60（418 刀3②：上限 61→29）。

    ★框架不活时判 **WAIT**：快照里那个 `threads.n` 是停机前的历史读数，
    它能说明"改前是什么样"，**不能**说明"改后有没有降下来"。
    """
    d = _read_json(_p("data/monitor/health_snapshot.json")) or {}
    _n = ((d.get("threads") or {}).get("n"))
    _v = {"threads": _n, "snapshot_age_sec":
          (round(time.time() - float(d["timestamp"]), 1)
           if isinstance(d.get("timestamp"), (int, float)) else None),
          "framework_alive": alive}
    if not isinstance(_n, int):
        return R("L09-2", "线程数 < 60", WAIT, _v, "health_snapshot.threads.n 缺失")
    if not alive:
        return R("L09-2", "线程数 < 60", WAIT, _v,
                 "框架未运行 ⇒ 读到的是**停机前**的 %d（可作改前基线，不能当改后结论）" % _n)
    if _n < TH_THREADS:
        return R("L09-2", "线程数 < 60", PASS, _v, "实测 %d" % _n)
    return R("L09-2", "线程数 < 60", FAIL, _v, "实测 %d ≥ %d" % (_n, TH_THREADS))


def c_lock_wait():
    """L09③ 锁等待 < 50ms。

    ★这个量**没有落盘出口**（`nucleus/runtime_metrics.py` 只在内存里留
    `lock_wait_samples`）。所以只能 WAIT + 说清去哪儿读，不猜。
    """
    d = _read_json(_p("data/monitor/health_snapshot.json")) or {}
    for _k in ("lock_wait", "lock_wait_samples", "lock_wait_ms"):
        if _k in d:
            _v = d.get(_k)
            if isinstance(_v, dict):
                _avg = _v.get("avg_ms")
                if isinstance(_v, (int, float)):
                    return (R("L09-3", "锁等待 < 50ms", PASS if _avg < 50 else FAIL,
                              _avg, "health_snapshot.%s.avg_ms=%.2f" % (_k, _avg)))
            return R("L09-3", "锁等待 < 50ms", WAIT, _v,
                     "字段 %s 在快照里但不是数值" % _k)
    return R("L09-3", "锁等待 < 50ms", WAIT, None,
             "快照无该字段；需进程内读 runtime_metrics.get_snapshot()['lock_wait_samples']")


def c_fusion(now: float, since_min: float):
    """L09④ 融合路径不再产出占位符（418 刀3④）。"""
    lines = _tail_lines(_p("logs/pulse.log"))
    if not lines:
        return R("L09-4", "融合不产占位符", WAIT, None, "pulse.log 读不到")
    _floor = now - since_min * 60.0
    _hit = 0
    for ln in lines:
        _ts = _log_ts(ln)
        if _ts is None or _ts < _floor:
            continue
        if "[器官别名]" in ln:
            _hit += 1
    _v = {"window_min": since_min, "placeholder_lines": _hit}
    if _hit == 0:
        return R("L09-4", "融合不产占位符", PASS, _v,
                 "近 %.0f 分钟 0 条含 [器官别名]" % since_min)
    return R("L09-4", "融合不产占位符", FAIL, _v,
             "近 %.0f 分钟 %d 条含 [器官别名]" % (since_min, _hit))


def c_channels():
    """L09⑤ 渠道分布是否均分（418 刀2）。"""
    p = _p("data/llm_traces/calls_%s.jsonl" % _today())
    if not os.path.isfile(p):
        return R("L09-5", "渠道分布均衡", WAIT, None, "今日 calls_*.jsonl 不存在")
    from collections import Counter
    cnt = Counter()
    n = 0
    try:
        with io.open(p, encoding="utf-8", errors="ignore") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError as e:
                    # 坏行（半行写入/非 JSON）—— 去重留痕，不逐行刷屏
                    _warn("c_channels:json", e)
                    continue
                n += 1
                cnt[str(d.get("channel") or "?")] += 1
    except OSError as e:
        _warn("c_channels:" + p, e)
        return R("L09-5", "渠道分布均衡", WAIT, None, "读取失败 %s" % e)
    if n == 0:
        return R("L09-5", "渠道分布均衡", WAIT, None, "今日 0 条调用")
    _top, _topn = cnt.most_common(1)[0]
    _share = _topn / float(n)
    _v = {"calls": n, "channels_used": len(cnt), "top": _top,
          "top_share": round(_share, 4), "dist": dict(cnt.most_common(10))}
    if len(cnt) >= TH_CHANNEL_MIN_USED and _share <= TH_CHANNEL_TOP_SHARE:
        return R("L09-5", "渠道分布均衡", PASS, _v,
                 "%d 渠道 / 最大 %s %.1f%% / %d 次" % (len(cnt), _top, _share * 100, n))
    return R("L09-5", "渠道分布均衡", FAIL, _v,
             "%d 渠道 / 最大 %s %.1f%%（线 %.0f%%）/ %d 次"
             % (len(cnt), _top, _share * 100, TH_CHANNEL_TOP_SHARE * 100, n))


def c_world_model():
    """L04 世界模型日报首条。

    ★420 复核加强：`data/world_model/hit_rate_state.json` **根本不存在**（目录里只有
    `expectation_ledger.jsonl` / `.bak_325` / `periodic_profile.json` / `replay_410.json`），
    而 `health_snapshot.world_model.source` 却写着 source 就是它 ⇒ 不只是"日报没落盘"，
    而是**命中率状态机从未落过盘**（`windows_sampled` 也就无从谈起）。
    """
    p = _p("data/world_model/hit_rate_%s.json" % _today())
    _state_p = _p("data/world_model/hit_rate_state.json")
    if os.path.isfile(p):
        return R("L04", "世界模型日报首条", PASS,
                 {"path": os.path.basename(p), "size_b": _size(p),
                  "state_file": os.path.isfile(_state_p)},
                 "今日文件已生成（state 文件存在=%s）" % os.path.isfile(_state_p))
    _other = sorted(glob.glob(_p("data/world_model/hit_rate_*.json")))
    _st = _read_json(_state_p) or {}
    return R("L04", "世界模型日报首条", WAIT,
             {"any_hit_rate_files": len(_other),
              "state_file_exists": os.path.isfile(_state_p),
              "windows_sampled": _st.get("windows_sampled"),
              "hit_rate": _st.get("hit_rate")},
             "今日 hit_rate_*.json 未生成；历史共 %d 份；"
             "hit_rate_state.json 存在=%s（快照里 source 指的正是它）"
             % (len(_other), os.path.isfile(_state_p)))


def c_narrative():
    """L05 叙事落盘首条。"""
    p = _p("data/narrative/narrative_%s.jsonl" % _today())
    if os.path.isfile(p):
        _n = 0
        try:
            with io.open(p, encoding="utf-8", errors="ignore") as f:
                _n = sum(1 for _ln in f if _ln.strip())
        except OSError as e:
            _warn("c_narrative:" + p, e)
        _state = PASS if _n >= 1 else WAIT
        return R("L05", "叙事落盘首条", _state, {"lines": _n, "size_b": _size(p)},
                 "今日 %d 行" % _n)
    _dir = _p("data/narrative")
    _all = sorted(glob.glob(os.path.join(_dir, "narrative_*.jsonl"))) if os.path.isdir(_dir) else []
    return R("L05", "叙事落盘首条", WAIT,
             {"dir_exists": os.path.isdir(_dir), "any_files": len(_all)},
             "今日文件未生成；目录存在=%s；历史共 %d 份"
             % (os.path.isdir(_dir), len(_all)))


def c_mem_by_organ():
    """L11 器官级内存 top（★420 复核：这条其实已有产物，不是"从未跑过"）。"""
    p = _p("data/monitor/memory_by_organ_%s.json" % _today())
    if os.path.isfile(p):
        _d = _read_json(p)
        _rows = None
        if isinstance(_d, dict):
            for _k in ("top", "top_organs", "organs", "rows"):
                if isinstance(_d.get(_k), list):
                    _rows = _d[_k]
                    break
        elif isinstance(_d, list):
            _rows = _d
        return R("L11", "器官级内存 top", PASS,
                 {"size_b": _size(p), "rows": len(_rows) if _rows else None},
                 "今日文件已生成 %s B" % _size(p))
    return R("L11", "器官级内存 top", WAIT, None, "今日 memory_by_organ_*.json 未生成")


def c_perf_delta():
    """L14 effect_verify 的**第三维**（`dims.perf_ok`）是否真有值。

    ★420 复核纠错（两个都是实测推翻了原有说法）：

    ① **落盘里没有 `perf_delta_ms` 这个字段**。`EvolutionEffectVerifier.verify()`
       收 `perf_delta_ms` 后转成 `dims.perf_ok` 再写盘（第 234 行 `("perf_ok", _d3)`），
       所以判据只能读 `dims.perf_ok`，读 `perf_delta_ms` 会恒 WAIT。
    ② 存量 50 条 verdict 的 `dims.perf_ok` **全是 null**（`reason` 写"未测量"），
       因为它们是 412 刀3 补供**之前**生成的；`PatchManager.py:5205` 现在确实
       传的是 `_m412_perf_delta_ms(...)`，所以缺的是「一次新 apply」，不是代码。
       ⇒ 全 null 时判 **WAIT**（等新补丁），不判 FAIL（代码没坏）。
    """
    d = _read_json(_p("data/evolution/effect_verify.json"))
    if not isinstance(d, dict):
        return R("L14", "第三维 perf_ok 有值", WAIT, None, "effect_verify.json 读不到")
    _v = d.get("verdicts")
    if isinstance(_v, dict):
        _items = list(_v.values())
    elif isinstance(_v, list):
        _items = _v
    else:
        return R("L14", "第三维 perf_ok 有值", WAIT, None, "verdicts 类型不认识")
    if not _items:
        return R("L14", "第三维 perf_ok 有值", WAIT, None, "effect_verify.verdicts 为空")

    _n_all = len(_items)
    _real, _stubs = [], 0
    for x in _items:
        if not isinstance(x, dict):
            continue
        # ★420 复核：`issue_type="x"` + `file="a.py"` 的是**演示桩**（p3d/pperf/pab
        #   这批，2026-11-01 00:25 同批写入）。418 刀3⑤ 的 `_M418_STUB_PATCH_IDS`
        #   只列了 tp1–tp4，漏了这批 ⇒ 这里按「真补丁 = patch_id 以 patch_ 开头」
        #   剔除，否则会把 3/50=6% 当成"真数据"。
        if str(x.get("issue_type") or "").strip() == "x" \
                or not str(x.get("patch_id") or "").startswith("patch_"):
            _stubs += 1
            continue
        _real.append(x)

    if not _real:
        return R("L14", "第三维 perf_ok 有值", WAIT,
                 {"verdicts": _n_all, "real": 0, "stub_rows": _stubs},
                 "%d 条 verdict 里没有真补丁（桩 %d 条）" % (_n_all, _stubs))

    _n = len(_real)
    _has = 0
    _newest = 0.0
    for x in _real:
        _dims = x.get("dims") if isinstance(x.get("dims"), dict) else {}
        # 落盘字段是 `dims.perf_ok`（`verify()` 在 EvolutionEffectVerifier:234
        # 把入参 `perf_delta_ms` 转成 `perf_ok` 再写），不是顶层 `perf_delta_ms`。
        if _dims.get("perf_ok") is not None or x.get("perf_delta_ms") is not None:
            _has += 1
        _ts = x.get("verified_at")
        if isinstance(_ts, (int, float)) and _ts > _newest:
            _newest = float(_ts)
    _ratio = _has / float(_n)
    _val = {"verdicts": _n_all, "real": _n, "stub_rows": _stubs,
            "with_perf_ok": _has, "ratio": round(_ratio, 4),
            "newest_verified_at": _newest or None,
            "newest_verified_at_str": (time.strftime("%Y-%m-%d %H:%M:%S",
                                                     time.localtime(_newest))
                                       if _newest else None)}
    if _has == 0:
        return R("L14", "第三维 perf_ok 有值", WAIT, _val,
                 "真补丁 %d 条里 dims.perf_ok **全是 null**（最新一条 %s；另有桩 %d 条）"
                 "⇒ 存量都是 412 补供前生成的，缺的是「一次新 apply」"
                 % (_n, _val["newest_verified_at_str"], _stubs))
    if _ratio >= TH_PERF_DELTA_RATIO:
        return R("L14", "第三维 perf_ok 有值", PASS, _val,
                 "%d/%d = %.1f%%（桩 %d 条已剔除）"
                 % (_has, _n, _ratio * 100, _stubs))
    return R("L14", "第三维 perf_ok 有值", FAIL, _val,
             "%d/%d = %.1f%% < %.0f%%（桩 %d 条已剔除）"
             % (_has, _n, _ratio * 100, TH_PERF_DELTA_RATIO * 100, _stubs))


def c_coop_tick():
    """L15 `_m416_coop_tick()` 真实触发（日志里 `让步=N次` 且 N>0）。

    ★420 复核纠错：集成路径里 `让步=0` **是预期，不是故障**——
    `_m416_coop_tick()` 挂在 `get_method_body()`，而集成扫描走
    `detect_code_issues()` → `_scan_all_organs()`，**不经**它。
    真路径要 `PulseCodeLearner` 逐条取方法体（417 已 ABBA 实测 400 次/轮触发）。
    所以只有「扫描跑过但让步恒 0」判 WAIT（口径不适用），不判 FAIL。
    """
    import re
    _pat = re.compile(r"\[416刀1\].*?让步=(\d+)")
    lines = _tail_lines(_p("logs/pulse.log"))
    if not lines:
        return R("L15", "coop_tick 真触发", WAIT, None, "pulse.log 读不到")
    _vals = []
    for ln in lines:
        _m = _pat.search(ln)
        if _m:
            _vals.append(int(_m.group(1)))
    if not _vals:
        return R("L15", "coop_tick 真触发", WAIT, None,
                 "日志里没有 [416刀1] 扫描完成行（扫描未跑或未到期）")
    _max = max(_vals)
    _val = {"runs": len(_vals), "max_ticks": _max}
    if _max > 0:
        return R("L15", "coop_tick 真触发", PASS, _val,
                 "%d 轮扫描，最大让步 %d 次" % (len(_vals), _max))
    return R("L15", "coop_tick 真触发", WAIT, _val,
             "%d 轮扫描让步全是 0 —— 集成路径不经 get_method_body，"
             "**这是预期**；真路径要看 PulseCodeLearner 取方法体时的 ABBA" % len(_vals))


def c_memory_wall():
    """L10 内存墙：growth_mb_per_min < 200（=2GB/10min）。"""
    d = _read_json(_p("data/monitor/health_snapshot.json")) or {}
    _mem = d.get("memory") or {}
    _g = _mem.get("growth_mb_per_min")
    if not isinstance(_g, (int, float)):
        return R("L10", "内存增长 < 200MB/min", WAIT, None,
                 "health_snapshot.memory.growth_mb_per_min 缺失（需 --watch 另测）")
    _val = {"growth_mb_per_min": _g, "rss_mb": _mem.get("process_rss_mb"),
            "threshold": TH_GROWTH_MB_PER_MIN}
    _ok = _g < TH_GROWTH_MB_PER_MIN
    return R("L10", "内存增长 < 200MB/min", PASS if _ok else FAIL, _val,
             "%.2f MB/min（线 %.0f）；RSS %.0f MB"
             % (_g, TH_GROWTH_MB_PER_MIN, _mem.get("process_rss_mb") or -1))


# --------------------------------------------------------------- 主流程
def run(since_min: float = 60.0) -> list:
    now = time.time()
    alive, why = check_alive(now)
    print("[活性] %s —— %s" % ("框架在跑" if alive else "框架未运行/快照过期", why))
    print()

    rows = [c_patterns(now, alive), c_threads(alive), c_lock_wait(),
            c_fusion(now, since_min), c_channels(), c_world_model(),
            c_narrative(), c_mem_by_organ(), c_perf_delta(), c_coop_tick(),
            c_memory_wall()]
    return rows


def render(rows) -> int:
    _w = max(len(r.name) for r in rows) if rows else 10
    print("%-8s %-*s %-6s %s" % ("编号", _w, "判据", "态", "读数"))
    print("-" * (12 + _w + 50))
    for r in rows:
        print("%-8s %-*s %-6s %s" % (r.cid, _w, r.name, r.state, r.detail))
    _c = {"PASS": 0, "FAIL": 0, "WAIT": 0}
    for r in rows:
        _c[r.state] = _c.get(r.state, 0) + 1
    print("-" * (12 + _w + 50))
    print("合计: PASS=%d  FAIL=%d  WAIT=%d  （WAIT = 没有观测数据，不是失败）"
          % (_c["PASS"], _c["FAIL"], _c["WAIT"]))
    if _c["FAIL"]:
        return 1
    if _c["WAIT"]:
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="B 类「等活体」11 条判据一键验收（第420批）",
        epilog="退出码：0 全 PASS ｜ 1 有 FAIL ｜ 2 无 FAIL 但有 WAIT")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--since-min", type=float, default=60.0,
                    help="日志回看窗口（分钟），默认 60")
    args = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "nucleus")):
        print("[FAIL] 仓库根不对（找不到 nucleus/）: %s" % ROOT)
        return 3

    rows = run(args.since_min)
    if args.json:
        _c = {"PASS": 0, "FAIL": 0, "WAIT": 0}
        for r in rows:
            _c[r.state] = _c.get(r.state, 0) + 1
        print(json.dumps({"generated_at": time.time(),
                          "counts": _c,
                          "rows": [r.as_dict() for r in rows]},
                         ensure_ascii=False, indent=2))
        return 1 if _c["FAIL"] else (2 if _c["WAIT"] else 0)
    return render(rows)


if __name__ == "__main__":
    sys.exit(main())
