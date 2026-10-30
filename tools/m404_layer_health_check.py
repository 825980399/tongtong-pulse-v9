#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m404_layer_health_check —— 七层自我分析体系自检面板（404 批·刀3）。

逐层判定 L1~L7 是否成立：每层一行 `Lx status 名称 detail`，末行整体汇总，exit 0/1/2。
**纯只读**：不 import 生产单例、不发起脉冲、不写任何文件（与 `tools/gate_health_check.py`
的"阈值常量从源码静态解析、不在线 import"惯例同构）。

三态，绝不把"判不了"混进 PASS
------------------------------
* ``PASS`` —— 判据成立；
* ``FAIL`` —— 判据不成立，且能确定是故障；
* ``SKIP`` —— **前提不成立，判不了**（框架停机 / psutil 不可用 / 当天首个小时还没过完）。

整体结论只有三种：有 FAIL 就是 ``FAIL``；只有 SKIP 是 ``PARTIAL``；全 PASS 才是 ``PASS``。
SKIP 一律带 ``reason``，默认不阻断（``--strict`` 才把 SKIP 当失败）——
把"框架停机"做成阻断门，等于"停机维护期间不允许提交代码"，这是最典型的一种自伤。

七层判据（阈值来源逐个写在常量旁，不靠记忆）
--------------------------------------------
L1 调用图   ``data/call_graph/graph_latest.json`` 存在，``stats.total_files ≥ L1_MIN_FILES``、``parse_errors == 0``
L2 事件字典 ``data/contracts/event_dictionary.json`` 存在，``unique_events``/``constant_entries`` > 0 且互不矛盾
L3 pairs    ``data/monitor/pulse_pairs_<day>.jsonl`` 行数 ≤ ``pulse_pair_file_cap``（从 config.py **静态解析**）
L4 因果链   ``data/monitor/causal_chains_<day>.jsonl`` 当天 ≥1 条 ``kind=hourly_summary``（未过 01:00 则 SKIP）
L5 健康快照 快照年龄 ≤ ``snapshot_max_age``（默认从 external_heartbeat.py 静态解析 = 90s）；框架停机 ⇒ SKIP
L6 温冷快照 ``data/monitor/health_snapshot_hourly.json`` 存在且 ``schema=snapshot_tiers/*``、含 ``warm``/``cold``
L7 心跳摘要 快照 ``heartbeat.prev_beat_events`` 是非空字符串列表，且 ``prev_beat_event_count`` 与长度一致

★ 与任务书票面不同的两处（均为 T0 实测，详见 404 交付报告 §T0）
--------------------------------------------------------------
1. 任务书写「L1 覆盖 ≥409 文件」—— 409 是当时的实测值，这里保留为**下限**常量
   ``L1_MIN_FILES``（实测 412）。判据是"≥ 下限"，不是"== 409"：文件数只会增不会减，
   钉死精确值会被"新增一个正常文件"顶红（铁律 9）。
2. 任务书写「L5 快照最近 5s 更新」—— **健康系统也做不到**：心跳实拍 11s/拍
   （373 批刀3 用 ``logs/pulse.log`` 638 条心跳行复核，并据此把
   ``external_heartbeat.DEFAULT_MAX_AGE_SEC`` 定为 90s ≈ 连丢 7—8 拍）。
   按 5s 判会在每一拍间隙报红，面板随即变成噪声源。
   ⇒ 默认口径复用 90s；``--snapshot-5s`` 时按任务书字面值判（报告里如实披露该偏差）。
   两种口径下结果都带 ``strict_5s`` 布尔，读方想看字面口径随时看得到。
"""
import argparse
import ast
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:  # 脚本直跑时 sys.path[0] 是 tools/，不在工程根
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

SCHEMA = "layer_health/404.v1"
CRITERIA_VERSION = "404.layer_health.v1"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

#: L1 覆盖文件数下限（任务书 409；403 批实测 412）
L1_MIN_FILES = 409
#: L3 行数上限的兜底值 —— 正常路径从 config.py 静态解析，解析不到才用它
DEFAULT_PAIRS_CAP = 500000
#: L5 快照年龄上限的兜底值 —— 正常路径从 external_heartbeat.py 静态解析
DEFAULT_SNAPSHOT_MAX_AGE_S = 90.0
#: 任务书字面的「最近 5s」（仅供 --snapshot-5s 与 strict_5s 读数使用）
TASK_LITERAL_STALE_S = 5.0
#: L6 小时快照年龄上限（任务书 405 刀6 定为 3700s ≈ 1h2min）。
#: 比小时落盘周期 3600s 宽 100s —— 容一次整点写入的抖动，又不至于放过
#: 「隔了一个小时还没写」的真实陈旧。可用 --l6-max-age 覆盖。
DEFAULT_L6_MAX_AGE_S = 3700.0

_MONITOR = os.path.join("data", "monitor")
_UNSET = object()


# ---------------------------------------------------------------------------
# 基础工具（全部纯只读；失败一律留痕后降级，不抛）
# ---------------------------------------------------------------------------
def _load_json(path):
    """读 JSON；不存在/不可解析返回 ``None``（调用方据此判 FAIL/SKIP，不猜 0）。"""
    try:
        with open(path, "r", encoding="utf-8") as _f:
            return json.load(_f)
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._load_json[open]", level="debug")
        return None
    except (ValueError, TypeError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._load_json[parse]")
        return None


def _int(_v):
    """安全取整数；取不到按 0（调用方的判据会因此落 FAIL，而不是静默放过）。"""
    try:
        return int(_v)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._int", level="debug")
        return 0


def _age(path, now):
    """文件年龄（秒）；取不到返回 ``None``（**不填 0**，0 会被读成"刚刚写过"）。"""
    try:
        return round(float(now) - os.path.getmtime(path), 1)
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._age", level="debug")
        return None


def _count_lines(path):
    """流式数非空行（pairs 单日可达 500k 行，整读会把内存吃穿）。"""
    _n = 0
    try:
        with open(path, "rb") as _f:
            for _line in _f:
                if _line.strip():
                    _n += 1
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._count_lines")
        return None
    return _n


def _read_const(path, name, conv=float):
    """从源码里**静态解析**一个模块级常量（不 import，避免拉进生产单例与副作用）。

    理由：本工具要在 pre-commit 里跑，import `config` 会连带构造整张 RUNTIME_PARAMS；
    AST 只读一个赋值语句，零副作用、失败可降级。
    """
    try:
        with open(path, "r", encoding="utf-8") as _f:
            _src = _f.read()
        for _node in ast.parse(_src).body:
            if not isinstance(_node, ast.Assign):
                continue
            for _tgt in _node.targets:
                if isinstance(_tgt, ast.Name) and _tgt.id == name:
                    return conv(_node.value.value), _node.lineno
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._read_const[open]", level="debug")
    except (SyntaxError, ValueError, TypeError, AttributeError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._read_const[parse]", level="debug")
    return None, None


def read_pairs_cap(root=None):
    """``RUNTIME_PARAMS["pulse_pair_file_cap"]`` —— 静态解析 config.py 的字典字面量。

    Returns:
        ``(cap, source)``；``source`` 为 ``"config.py:<line>"`` 或 ``"default"``。

    ★``cap <= 0`` **不是「零行」**：``utils/pulse_tracer._pair_file_cap`` 把 bool / 非 int /
    ``<= 0`` 一律视为**无效配置**并回落 ``_PAIR_FILE_CAP``(500000)。本函数必须同语义 ——
    否则配置写坏时，面板会报"行数 > 0 已超限"，而生产其实按 50 万在裁，两边读数永远对不上。
    """
    _p = os.path.join(root or _ROOT, "config.py")
    _cap, _line = None, None
    try:
        with open(_p, "r", encoding="utf-8") as _f:
            _tree = ast.parse(_f.read())
        for _node in ast.walk(_tree):
            if not isinstance(_node, ast.Dict):
                continue
            for _k, _v in zip(_node.keys, _node.values):
                if not isinstance(_k, ast.Constant) or _k.value != "pulse_pair_file_cap":
                    continue
                if isinstance(_v, ast.Constant) and isinstance(_v.value, int) \
                        and not isinstance(_v.value, bool) and int(_v.value) > 0:
                    _cap, _line = int(_v.value), _v.lineno
                    break
            if _cap is not None:
                break
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check.read_pairs_cap[open]")
    except (SyntaxError, ValueError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check.read_pairs_cap[parse]")
    if _cap is None:
        return DEFAULT_PAIRS_CAP, "default"
    return _cap, "config.py:%d" % _line


def read_snapshot_max_age(root=None):
    """``external_heartbeat.DEFAULT_MAX_AGE_SEC`` —— 静态解析（不 import nucleus）。"""
    _p = os.path.join(root or _ROOT, "nucleus", "world_model", "external_heartbeat.py")
    _v, _line = _read_const(_p, "DEFAULT_MAX_AGE_SEC", float)
    if _v is None:
        return DEFAULT_SNAPSHOT_MAX_AGE_S, "default"
    return float(_v), "external_heartbeat.py:%d" % _line


def _probe_pid_is_framework(pid):
    """快照自报的 pid 是否仍是**框架**进程：``True``/``False``/``None``（判不了）。

    ★为什么不能只靠 `is_framework_running()`
    ----------------------------------------
    它按"命令行里有没有 basename==``main.py``"匹配，**任何带 main.py 路径的命令都会
    骗到它** —— `git commit -- main.py` 自己、`python -m py_compile main.py`、编辑器
    打开的文件参数，全都会让它在框架早已停机时返回 True。
    404 刀4 提交时实测到一次：面板报"框架在跑但快照不更新"（L5 FAIL），
    而全机 `psutil` 枚举下来 main.py 进程数为 0。

    快照里带着框架**自己**报的 ``pid``，核验"这个 pid 还活着且命令行指向 main.py"
    才是硬证据；拿不到 pid 就老实说判不了（``None``），不猜。
    """
    try:
        _pid = int(pid)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._probe_pid_is_framework[pid]",
                   level="debug")
        return None
    if _pid <= 0:
        return None
    try:
        import psutil
    except Exception as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._probe_pid_is_framework[import]",
                   level="debug")
        return None
    try:
        if not psutil.pid_exists(_pid):
            return False
        _proc = psutil.Process(_pid)
        try:
            _cl = _proc.cmdline()
        except (psutil.AccessDenied, psutil.NoSuchProcess) as _e:
            silent_exc(_e, where="tools.m404_layer_health_check._probe_pid_is_framework[cmdline]",
                       level="debug")
            return None
        from nucleus.evolution.night_orchestration import _cmdline_is_framework_main
        return bool(_cmdline_is_framework_main(_cl))
    except Exception as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._probe_pid_is_framework")
        return None


def _probe_framework_running(root):
    """框架是否在跑：``True``/``False``/``None``（None = psutil 不可用，判不了）。"""
    try:
        from nucleus.evolution.night_orchestration import is_framework_running
        return is_framework_running(root)
    except Exception as _e:
        silent_exc(_e, where="tools.m404_layer_health_check._probe_framework_running")
        return None


def _ok(layer, name, detail, evidence=None):
    return {"layer": layer, "name": name, "status": PASS, "detail": detail,
            "reason": None, "evidence": evidence or {}}


def _bad(layer, name, detail, evidence=None, reason=None):
    return {"layer": layer, "name": name, "status": FAIL, "detail": detail,
            "reason": reason, "evidence": evidence or {}}


def _skip(layer, name, reason, detail, evidence=None):
    return {"layer": layer, "name": name, "status": SKIP, "detail": detail,
            "reason": reason, "evidence": evidence or {}}


# ---------------------------------------------------------------------------
# L1 调用图
# ---------------------------------------------------------------------------
def check_l1(ctx):
    _p = os.path.join(ctx["root"], "data", "call_graph", "graph_latest.json")
    if not os.path.exists(_p):
        return _bad("L1", "调用图", "graph_latest.json 不存在（调用图从未生成？）", {"path": _p})
    _d = _load_json(_p)
    if not isinstance(_d, dict):
        return _bad("L1", "调用图", "graph_latest.json 不可解析", {"path": _p})
    _st = _d.get("stats") or {}
    _files = _int(_st.get("total_files"))
    _parsed = _int(_st.get("parsed_files"))
    _errs = _int(_st.get("parse_errors"))
    _ev = {"path": _p, "total_files": _files, "parsed_files": _parsed,
           "parse_errors": _errs, "total_functions": _int(_st.get("total_functions")),
           "total_calls": _int(_st.get("total_calls")),
           "cross_file_calls": _int(_st.get("cross_file_calls")),
           "age_s": _age(_p, ctx["now"]), "min_files": L1_MIN_FILES}
    if _errs:
        return _bad("L1", "调用图", "解析失败文件 %d 个（要求 0）" % _errs, _ev)
    if _files < L1_MIN_FILES:
        return _bad("L1", "调用图", "覆盖文件 %d < 下限 %d" % (_files, L1_MIN_FILES), _ev)
    if _parsed < _files:
        return _bad("L1", "调用图", "成功解析 %d < 覆盖 %d（有文件没解析出来）" % (_parsed, _files), _ev)
    return _ok("L1", "调用图",
               "覆盖 %d 文件 / %d 函数 / %d 调用（解析失败 0）"
               % (_files, _ev["total_functions"], _ev["total_calls"]), _ev)


# ---------------------------------------------------------------------------
# L2 事件字典
# ---------------------------------------------------------------------------
def check_l2(ctx):
    _p = os.path.join(ctx["root"], "data", "contracts", "event_dictionary.json")
    if not os.path.exists(_p):
        return _bad("L2", "事件字典", "event_dictionary.json 不存在", {"path": _p})
    _d = _load_json(_p)
    if not isinstance(_d, dict):
        return _bad("L2", "事件字典", "event_dictionary.json 不可解析", {"path": _p})
    _t = _d.get("totals") or {}
    _ue = _int(_t.get("unique_events"))
    _ce = _int(_t.get("constant_entries"))
    _ev = {"path": _p, "schema": _d.get("schema"), "generated_at": _d.get("generated_at"),
           "generated_by": _d.get("generated_by"), "unique_events": _ue,
           "constant_entries": _ce, "emitted_in_prod": _int(_t.get("emitted_in_prod")),
           "never_emitted": _int(_t.get("never_emitted")),
           "age_s": _age(_p, ctx["now"])}
    if _ue <= 0 or _ce <= 0:
        return _bad("L2", "事件字典", "条目数异常（unique_events=%d, constant_entries=%d）"
                    % (_ue, _ce), _ev)
    if _ue > _ce:
        return _bad("L2", "事件字典", "唯一事件数 %d > 常量条目数 %d（口径自相矛盾）"
                    % (_ue, _ce), _ev)
    return _ok("L2", "事件字典", "唯一事件 %d / 常量条目 %d / 生产已发 %d / 从未发 %d"
               % (_ue, _ce, _ev["emitted_in_prod"], _ev["never_emitted"]), _ev)


# ---------------------------------------------------------------------------
# L3 pairs 落盘容量
# ---------------------------------------------------------------------------
def _today_str(now, fmt="%Y%m%d"):
    return time.strftime(fmt, time.localtime(float(now)))


def _pairs_path(root, now):
    """与 `utils/causal_chain.pairs_path` 同口径：当日不存在则回落到昨日（跨零点窗口）。"""
    _day = _today_str(now)
    _p = os.path.join(root, _MONITOR, "pulse_pairs_%s.jsonl" % _day)
    if os.path.exists(_p):
        return _p, _day
    _y = time.strftime("%Y%m%d", time.localtime(float(now) - 86400))
    return os.path.join(root, _MONITOR, "pulse_pairs_%s.jsonl" % _y), _y


def check_l3(ctx):
    _cap = ctx.get("pairs_cap")
    _src = "cli"
    if _cap is None:
        _cap, _src = read_pairs_cap(ctx["root"])
    _p, _day = _pairs_path(ctx["root"], ctx["now"])
    if not os.path.exists(_p):
        return _skip("L3", "pairs 容量", "pairs_file_missing",
                     "当日/昨日 pairs 文件都不存在（本层只判容量是否超限，不判落盘断没断）",
                     {"path": _p, "cap": _cap, "cap_source": _src})
    _n = _count_lines(_p)
    if _n is None:
        return _bad("L3", "pairs 容量", "行数读不出来（文件被占用？）",
                    {"path": _p, "cap": _cap, "cap_source": _src})
    try:
        _size = os.path.getsize(_p)
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check.check_l3[size]", level="debug")
        _size = 0
    _ev = {"path": _p, "day": _day, "rows": _n, "cap": _cap, "cap_source": _src,
           "size_mb": round(_size / 1024.0 / 1024.0, 2),
           "usage_ratio": round(_n / float(_cap), 4) if _cap else None}
    if _cap and _n > _cap:
        return _bad("L3", "pairs 容量", "行数 %d > 上限 %d（裁切没生效）" % (_n, _cap), _ev)
    return _ok("L3", "pairs 容量", "%d 行 / 上限 %d（%.1f%%）/ %.1fMB"
               % (_n, _cap, 100.0 * (_n / float(_cap) if _cap else 0), _ev["size_mb"]), _ev)


# ---------------------------------------------------------------------------
# L4 因果链
# ---------------------------------------------------------------------------
def check_l4(ctx):
    _day = _today_str(ctx["now"])
    _p = os.path.join(ctx["root"], _MONITOR, "causal_chains_%s.jsonl" % _day)
    _hour = time.localtime(float(ctx["now"])).tm_hour
    _ev = {"path": _p, "day": _day, "local_hour": _hour}
    if not os.path.exists(_p):
        if _hour < 1:
            return _skip("L4", "因果链", "day_too_young",
                         "当天首个小时还没过完（首条小时汇总在 01:00 之后），判不了", _ev)
        return _bad("L4", "因果链", "当天没有 causal_chains 台账", _ev)
    _rows = 0
    _hours = []
    _incomplete = 0
    try:
        with open(_p, "r", encoding="utf-8", errors="replace") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _r = json.loads(_line)
                except (ValueError, TypeError) as _e:
                    silent_exc(_e, where="tools.m404_layer_health_check.check_l4[parse]",
                               level="debug")
                    continue
                if not isinstance(_r, dict) or _r.get("kind") != "hourly_summary":
                    continue
                _rows += 1
                if not _r.get("complete"):
                    _incomplete += 1
                _h = str(_r.get("hour") or "")
                if _h and _h not in _hours:
                    _hours.append(_h)
    except OSError as _e:
        silent_exc(_e, where="tools.m404_layer_health_check.check_l4[read]")
        return _bad("L4", "因果链", "台账读不出来（文件被占用？）", _ev)
    _ev.update({"summaries": _rows, "hours": sorted(_hours),
                "incomplete": _incomplete,
                "latest_hour": max(_hours) if _hours else None})
    if not _rows:
        if _hour < 1:
            return _skip("L4", "因果链", "day_too_young", "文件在但还没到 01:00", _ev)
        return _bad("L4", "因果链", "当天台账里没有 hourly_summary 记录", _ev)
    _stale = None
    if _hours:
        try:
            _latest = int(max(_hours).split("T")[1])
            _stale = max(0, _hour - _latest)
        except (IndexError, ValueError) as _e:
            silent_exc(_e, where="tools.m404_layer_health_check.check_l4[hour]", level="debug")
    _ev["stale_hours"] = _stale
    return _ok("L4", "因果链", "%d 条小时汇总，最新 %s（滞后 %s 小时，未完成 %d 条）"
               % (_rows, _ev["latest_hour"], _stale, _incomplete), _ev)


# ---------------------------------------------------------------------------
# L5 健康快照新鲜度
# ---------------------------------------------------------------------------
def check_l5(ctx):
    _p = os.path.join(ctx["root"], _MONITOR, "health_snapshot.json")
    _max = ctx.get("snapshot_max_age")
    _src = "cli"
    if _max is None:
        _max, _src = read_snapshot_max_age(ctx["root"])
    if ctx.get("snapshot_5s"):
        _max, _src = TASK_LITERAL_STALE_S, "task_book_5s"
    _d = _load_json(_p)
    _ev = {"path": _p, "max_age_s": _max, "max_age_source": _src,
           "exists": os.path.exists(_p)}
    if not isinstance(_d, dict):
        return _skip("L5", "健康快照", "snapshot_unavailable",
                     "health_snapshot.json 不存在或不可解析（没有读数 ≠ 读数为 0）", _ev)
    _age_file = _age(_p, ctx["now"])
    _ts = _d.get("timestamp")
    _age_ts = None
    try:
        _age_ts = round(float(ctx["now"]) - float(_ts), 1) if _ts is not None else None
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check.check_l5[timestamp]", level="debug")
    # 年龄取 mtime 与快照自报 timestamp 中更保守的那个（与 external_heartbeat 同口径）：
    # 前者防"写盘卡住但内容旧"，后者防"文件被 touch 过"。
    _cand = [_a for _a in (_age_file, _age_ts) if _a is not None]
    # ★变量名不能叫 _age：会遮蔽模块级的 _age() 函数（本行以上刚调过它）
    _snap_age = max(_cand) if _cand else None
    _ev.update({"age_s": _snap_age, "age_from_file_s": _age_file, "age_from_ts_s": _age_ts,
                "beat_count": _d.get("beat_count"),
                "pid": _d.get("pid"),
                "strict_5s": bool(_snap_age is not None
                                  and _snap_age <= TASK_LITERAL_STALE_S)})
    if _snap_age is None:
        return _bad("L5", "健康快照", "快照年龄取不到（mtime 与 timestamp 都读不到）", _ev)
    # ① 新鲜就是新鲜 —— 不 enquête 进程。省一次 psutil 全表扫描（约 1s），
    #    也彻底躲开下面那条进程判据的假阳性。
    if _snap_age <= _max:
        return _ok("L5", "健康快照", "年龄 %.1fs ≤ 上限 %.0fs（beat=%s，字面 5s 口径 %s）"
                   % (_snap_age, _max, _ev["beat_count"],
                      "满足" if _ev["strict_5s"] else "不满足"), _ev)
    # ② 陈旧：先用**快照自报 pid** 核验（硬证据），拿不到才退回进程枚举
    _alive = ctx.get("snapshot_pid_alive", _UNSET)
    if _alive is _UNSET:
        _alive = _probe_pid_is_framework(_d.get("pid"))
    _ev["snapshot_pid_alive"] = _alive
    if _alive is True:
        return _bad("L5", "健康快照",
                    "快照自报 pid=%s 仍是框架进程，但快照已 %.1fs 未更新（> 上限 %.0fs）"
                    % (_d.get("pid"), _snap_age, _max), _ev)
    if _alive is False:
        return _skip("L5", "健康快照", "framework_down",
                     "快照自报 pid=%s 已不在（框架停机，快照 age=%.1fs）—— 停机时本层不判"
                     % (_d.get("pid"), _snap_age), _ev)
    # ③ pid 判不了：退回进程枚举，但**只把 True 当线索不当铁证**
    _run = ctx.get("framework_running", _UNSET)
    if _run is _UNSET:
        _run = _probe_framework_running(ctx["root"])
    _ev["framework_running"] = _run
    _ev["running_unverified"] = bool(_run is True)
    if _run is None:
        return _skip("L5", "健康快照", "psutil_unavailable",
                     "psutil 不可用，判不了框架是否在跑（age=%.1fs）" % _snap_age, _ev)
    if _run is False:
        return _skip("L5", "健康快照", "framework_down",
                     "框架未运行（快照 age=%.1fs）—— 停机时本层不判，避免假警报"
                     % _snap_age, _ev)
    return _skip("L5", "健康快照", "running_unverified",
                 "进程枚举说 main.py 在跑，但快照没有可用 pid 交叉核验（age=%.1fs）；"
                 "该判据会被 `git commit -- main.py` 这类命令骗到，故不判 FAIL" % _snap_age,
                 _ev)


# ---------------------------------------------------------------------------
# L6 温冷快照
# ---------------------------------------------------------------------------
def check_l6(ctx):
    """温冷分层快照：结构合法 + 年龄不超上限。

    ★405 刀6 之前本函数**只看结构不看年龄** ⇒ 停机 2.2h 仍报 PASS
      （404 遗留，见交付报告「L6 只判文件存在」条）。现在年龄也进判据。

    判据顺序（**不可交换**）：
        ① 存在 → ② 可解析 → ③ schema → ④ warm/cold 齐 → ⑤ 非空
        这五步是真故障，一律 FAIL；
        ⑥ 年龄 ≤ 上限 → PASS；否则按 L5 同口径 SKIP（reason 可分辨）。
      把年龄放在最后，是为了「结构坏了 + 同时超龄」时仍报 FAIL 而不是降级 SKIP。
    """
    _p = os.path.join(ctx["root"], _MONITOR, "health_snapshot_hourly.json")
    if not os.path.exists(_p):
        return _bad("L6", "温冷快照", "health_snapshot_hourly.json 不存在（温冷分层没落盘）",
                    {"path": _p})
    _d = _load_json(_p)
    if not isinstance(_d, dict):
        return _bad("L6", "温冷快照", "health_snapshot_hourly.json 不可解析", {"path": _p})
    _schema = str(_d.get("schema") or "")
    # 年龄取 mtime 与快照自报 timestamp 中**更保守**的那个（与 L5 逐字同口径）：
    # 前者防"写盘卡住但内容旧"，后者防"文件被 touch 过"。
    _age_file = _age(_p, ctx["now"])
    _ts = _d.get("timestamp")
    _age_ts = None
    try:
        _age_ts = round(float(ctx["now"]) - float(_ts), 1) if _ts is not None else None
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="tools.m404_layer_health_check.check_l6[timestamp]", level="debug")
    _cand = [_a for _a in (_age_file, _age_ts) if _a is not None]
    # ★变量名不能叫 _age：会遮蔽模块级的 _age() 函数（本行以上刚调过它）
    _snap_age = max(_cand) if _cand else None
    _max = ctx.get("l6_max_age")
    _max_src = "cli" if _max is not None else "default"
    if _max is None:
        _max = DEFAULT_L6_MAX_AGE_S
    _ev = {"path": _p, "schema": _schema, "age_s": _snap_age,
           # ★必须无条件写入，不能只在超龄分支里补：evidence 是排查唯一依据，
           #   字段随分支缺失会让人以为"这条判据不存在"。fresh 路径同样要能看到它。
           "has_pid_field": "pid" in _d,
           "age_from_file_s": _age_file, "age_from_ts_s": _age_ts,
           "max_age_s": _max, "max_age_source": _max_src,
           "warm_age_s": _d.get("warm_age_s"), "cold_age_s": _d.get("cold_age_s"),
           "has_warm": isinstance(_d.get("warm"), dict),
           "has_cold": isinstance(_d.get("cold"), dict)}
    # ---- 结构判据（硬 FAIL，必须在年龄之前）----
    if not _schema.startswith("snapshot_tiers/"):
        return _bad("L6", "温冷快照", "schema 不是 snapshot_tiers/*（实际 %r）" % _schema, _ev)
    if not (_ev["has_warm"] and _ev["has_cold"]):
        return _bad("L6", "温冷快照", "缺 warm 或 cold 段", _ev)
    if not _d.get("warm") and not _d.get("cold"):
        return _bad("L6", "温冷快照", "warm 与 cold 都是空的（等于没分层）", _ev)
    # ---- 年龄判据（405 刀6 新增）----
    if _snap_age is None:
        # 连 mtime 都读不到属环境异常，不是"不新鲜" ⇒ 不报 FAIL
        return _skip("L6", "温冷快照", "snapshot_age_unavailable",
                     "温冷快照年龄取不到（mtime 与 timestamp 都读不到）", _ev)
    if _snap_age <= _max:
        return _ok("L6", "温冷快照", "schema=%s 温 %s / 冷 %s，年龄 %.1fs ≤ 上限 %.0fs（%s）"
                   % (_schema, _ev["warm_age_s"], _ev["cold_age_s"], _snap_age, _max, _max_src),
                   _ev)
    # 超龄：**本文件没有 pid 字段**（实测只有 schema/timestamp/warm/cold/*_age_s/*_at）
    # ⇒ L5 那条「自报 pid 做硬证据」的路走不通，只能枚举进程，且枚举只能当线索。
    _run = ctx.get("framework_running", _UNSET)
    if _run is _UNSET:
        _run = _probe_framework_running(ctx["root"])
    _ev["framework_running"] = _run
    if _run is None:
        return _skip("L6", "温冷快照", "psutil_unavailable",
                     "psutil 不可用，判不了框架是否在跑（小时快照 age=%.1fs > 上限 %.0fs）"
                     % (_snap_age, _max), _ev)
    if _run is False:
        return _skip("L6", "温冷快照", "framework_down",
                     "小时快照 age=%.1fs > 上限 %.0fs，且框架未运行 —— 停机时本层不判，"
                     "避免假警报（与 L5 同口径）" % (_snap_age, _max), _ev)
    return _skip("L6", "温冷快照", "running_unverified",
                 "小时快照 age=%.1fs > 上限 %.0fs，进程枚举说 main.py 在跑，但本文件"
                 "**没有 pid 字段**可交叉核验；该线索会被 `git commit -- main.py` 这类"
                 "命令骗到，故不判 FAIL（与 L5 对 running_unverified 的处置一致）"
                 % (_snap_age, _max), _ev)


# ---------------------------------------------------------------------------
# L7 心跳事件摘要
# ---------------------------------------------------------------------------
def check_l7(ctx):
    _p = os.path.join(ctx["root"], _MONITOR, "health_snapshot.json")
    _d = _load_json(_p)
    _ev = {"path": _p}
    if not isinstance(_d, dict):
        return _skip("L7", "心跳摘要", "snapshot_unavailable",
                     "health_snapshot.json 不存在或不可解析，判不了", _ev)
    _hb = _d.get("heartbeat") if isinstance(_d.get("heartbeat"), dict) else {}
    _pb = _hb.get("prev_beat_events", _d.get("prev_beat_events"))
    _where = "heartbeat.prev_beat_events" if "prev_beat_events" in _hb else "prev_beat_events"
    _ev.update({"field": _where, "snapshot_age_s": _age(_p, ctx["now"]),
                "prev_beat_event_count": _hb.get("prev_beat_event_count"),
                "prev_beat_span_s": _hb.get("prev_beat_span_s")})
    if _pb is None:
        return _bad("L7", "心跳摘要", "快照里没有 prev_beat_events 字段（L7 未接线）", _ev)
    if not isinstance(_pb, list):
        return _bad("L7", "心跳摘要", "prev_beat_events 不是列表（实际 %s）"
                    % type(_pb).__name__, _ev)
    _bad_names = [_x for _x in _pb if not isinstance(_x, str) or not _x.strip()]
    _ev["events"] = _pb[:12]
    _ev["events_len"] = len(_pb)
    if _bad_names:
        return _bad("L7", "心跳摘要", "prev_beat_events 里有 %d 个非事件名元素"
                    % len(_bad_names), _ev)
    _cnt = _hb.get("prev_beat_event_count")
    if _cnt is not None and _int(_cnt) != len(_pb):
        return _bad("L7", "心跳摘要", "prev_beat_event_count=%s 与列表长度 %d 不一致"
                    % (_cnt, len(_pb)), _ev)
    if not _pb:
        return _ok("L7", "心跳摘要", "字段在但本次为空（上一拍没有事件）", _ev)
    return _ok("L7", "心跳摘要", "%d 个事件（span %ss）：%s"
               % (len(_pb), _ev["prev_beat_span_s"], ", ".join(_pb[:3])), _ev)


CHECKS = [
    ("L1", "调用图", check_l1),
    ("L2", "事件字典", check_l2),
    ("L3", "pairs 容量", check_l3),
    ("L4", "因果链", check_l4),
    ("L5", "健康快照", check_l5),
    ("L6", "温冷快照", check_l6),
    ("L7", "心跳摘要", check_l7),
]


# ---------------------------------------------------------------------------
# 汇总与 CLI
# ---------------------------------------------------------------------------
def make_context(root=None, now=None, strict=False, pairs_cap=None,
                 snapshot_max_age=None, framework_running=_UNSET,
                 snapshot_5s=False, snapshot_pid_alive=_UNSET,
                 l6_max_age=None):
    """构造检查上下文（测试可注入 ``snapshot_pid_alive`` / ``framework_running``，不碰 psutil）。"""
    _ctx = {"root": os.path.abspath(root or _ROOT),
            "now": float(now if now is not None else time.time()),
            "strict": bool(strict),
            "pairs_cap": pairs_cap,
            "snapshot_max_age": snapshot_max_age,
            "l6_max_age": l6_max_age,
            "snapshot_5s": bool(snapshot_5s)}
    if framework_running is not _UNSET:
        _ctx["framework_running"] = framework_running
    if snapshot_pid_alive is not _UNSET:
        _ctx["snapshot_pid_alive"] = snapshot_pid_alive
    return _ctx


def run_all(ctx=None):
    """跑七层，返回汇总 dict。整体结论：有 FAIL ⇒ FAIL；有 SKIP ⇒ PARTIAL；否则 PASS。"""
    _ctx = ctx or make_context()
    _checks = [_fn(_ctx) for _l, _n, _fn in CHECKS]
    _n_pass = sum(1 for _c in _checks if _c["status"] == PASS)
    _n_fail = sum(1 for _c in _checks if _c["status"] == FAIL)
    _n_skip = sum(1 for _c in _checks if _c["status"] == SKIP)
    if _n_fail:
        _overall = FAIL
    elif _n_skip:
        _overall = "PARTIAL"
    else:
        _overall = PASS
    _code = 0
    if _n_fail:
        _code = 1
    elif _n_skip and _ctx["strict"]:
        _code = 2
    return {
        "schema": SCHEMA,
        "criteria_version": CRITERIA_VERSION,
        "root": _ctx["root"],
        "now": round(_ctx["now"], 3),
        "strict": _ctx["strict"],
        "checks": _checks,
        "overall": _overall,
        "counts": {"pass": _n_pass, "fail": _n_fail, "skip": _n_skip, "total": len(_checks)},
        "exit_code": _code,
    }


def format_report(result):
    """人读格式：每层一行 + 末行汇总。"""
    _lines = []
    for _c in result["checks"]:
        _lines.append("%-3s %-5s %-9s %s" % (_c["layer"], _c["status"], _c["name"],
                                             _c["detail"]))
        if _c["status"] == SKIP and _c.get("reason"):
            _lines.append("        └─ skip_reason=%s" % _c["reason"])
    _k = result["counts"]
    _lines.append("=" * 72)
    _lines.append("整体=%s  PASS=%d FAIL=%d SKIP=%d / 共 %d 层  (criteria=%s)"
                  % (result["overall"], _k["pass"], _k["fail"], _k["skip"],
                     _k["total"], result["criteria_version"]))
    return "\n".join(_lines)


def main(argv=None):
    _ap = argparse.ArgumentParser(
        description="七层自我分析体系自检面板（404 刀3）—— 逐层 PASS/FAIL/SKIP，只读")
    _ap.add_argument("--root", default=None, help="工程根（默认本文件上一级）")
    _ap.add_argument("--json", action="store_true", help="输出 JSON（机器读）")
    _ap.add_argument("--strict", action="store_true", help="SKIP 也算失败（exit 2）")
    _ap.add_argument("--snapshot-5s", dest="snapshot_5s", action="store_true",
                     help="L5 按任务书字面 5s 判（默认复用 external_heartbeat 的 90s 口径）")
    _ap.add_argument("--pairs-cap", dest="pairs_cap", type=int, default=None,
                     help="覆盖 L3 行数上限（默认静态解析 config.py）")
    _ap.add_argument("--snapshot-max-age", dest="snapshot_max_age", type=float, default=None,
                     help="覆盖 L5 快照年龄上限（秒）")
    _ap.add_argument("--l6-max-age", dest="l6_max_age", type=float, default=None,
                     help="覆盖 L6 温冷快照年龄上限（秒，默认 %g）" % DEFAULT_L6_MAX_AGE_S)
    _ap.add_argument("--layer", default=None, help="只跑某几层，逗号分隔（如 L3,L5）")
    _a = _ap.parse_args(argv)
    if _a.pairs_cap is not None and _a.pairs_cap <= 0:
        # 与 config 侧不同：CLI 是**人显式给的**，静默换成 500000 会让人以为设定生效了
        _ap.error("--pairs-cap 必须为正整数（cap<=0 在 config 侧表示无效并回落 50 万，"
                  "这里直接拒绝，避免误以为设定生效）")

    _ctx = make_context(root=_a.root, strict=_a.strict, pairs_cap=_a.pairs_cap,
                        snapshot_max_age=_a.snapshot_max_age, snapshot_5s=_a.snapshot_5s,
                        l6_max_age=_a.l6_max_age)
    _res = run_all(_ctx)
    if _a.layer:
        _want = {_x.strip().upper() for _x in _a.layer.split(",") if _x.strip()}
        _res["checks"] = [_c for _c in _res["checks"] if _c["layer"] in _want]
        _res["counts"] = {
            "pass": sum(1 for _c in _res["checks"] if _c["status"] == PASS),
            "fail": sum(1 for _c in _res["checks"] if _c["status"] == FAIL),
            "skip": sum(1 for _c in _res["checks"] if _c["status"] == SKIP),
            "total": len(_res["checks"])}
        if _res["counts"]["fail"]:
            _res["overall"], _res["exit_code"] = FAIL, 1
        elif _res["counts"]["skip"]:
            _res["overall"] = "PARTIAL"
            _res["exit_code"] = 2 if _a.strict else 0
        else:
            _res["overall"], _res["exit_code"] = PASS, 0

    if _a.json:
        print(json.dumps(_res, ensure_ascii=False, indent=2))
    else:
        print(format_report(_res))
    return _res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
