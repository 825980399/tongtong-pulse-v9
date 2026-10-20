# -*- coding: utf-8 -*-
"""318批刀3：压测首跑可复用件——基线快照 + 七段留痕对表 + 人推计数。

用法：
    python tools/stress/collect_stress_baseline.py                 # 出一份基线快照
    python tools/stress/collect_stress_baseline.py --loop 3        # 第 3 圈（带圈次前缀）
    python tools/stress/collect_stress_baseline.py --json out.json # 另存 JSON

**开三环、闭四环**（星轨+小林裁定）：本脚本只读统计，不触发执行环；
执行环（第四环）在压测期间显式标注"不真执行"（`requires_confirmation=True` 不消费）。

八项基线：台账 / pending / 待复核+覆盖率 / 图 / hub / ledger / 产出率 / M92。
七段留痕：发现`[307刀1]` → 入池`review_intake` → 分诊`[311刀1]` → 派单`[304刀5]`
          → 执行`SOP硬闸截断` → 验证`[309刀1]` → 沉淀`[316刀2]`。
人推计数（五类）：人工重启 / 改config / 手动拍 / 数值仅人工变化 / requires_confirmation手动放行。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

#: 七段留痕锚点（闭环顺序）
SEGMENTS = [
    ("1发现", "[307刀1]"),
    ("2入池", "review_intake"),
    ("3分诊", "[311刀1]"),
    ("4派单", "[304刀5]"),
    ("5执行", "SOP硬闸截断"),
    ("6验证", "[309刀1]"),
    ("7沉淀", "[316刀2]"),
]

#: 人推计数锚点（五类）
HUMAN_PUSH = [
    ("人工重启", r"框架启动完成|framework.*(start|boot)|曈曈启动"),
    ("改config", r"config.*(已修改|reload|热加载)"),
    ("手动拍", r"手动拍|manual_beat|人工触发"),
    ("数值仅人工变化", r"(人工|手动).*(调整|修改|设置)"),
    ("requires_confirmation手动放行", r"requires_confirmation.*(放行|确认|approved)"),
]


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception as _e:
        print(f"[WARN] 读 JSON 失败 {path}: {_e}")
        return default


def _count_jsonl(path: str) -> int:
    _n = 0
    try:
        with open(path, encoding="utf-8") as _f:
            for _line in _f:
                if _line.strip():
                    _n += 1
    except Exception as _e:
        print(f"[WARN] 读 JSONL 失败 {path}: {_e}")
    return _n


def _latest(pattern: str) -> str:
    _fs = sorted(glob.glob(os.path.join(_ROOT, pattern)))
    return _fs[-1] if _fs else ""


def _ledger() -> dict:
    _p = os.path.join(_ROOT, "data", "world_model", "expectation_ledger.jsonl")
    _rows = []
    try:
        with open(_p, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _rows.append(json.loads(_line))
                except ValueError:
                    continue
    except Exception as _e:
        print(f"[WARN] ledger 不可读: {_e}")
    _v: dict[str, int] = {}
    _g: dict[str, int] = {}
    for _r in _rows:
        _v[_r.get("verdict", "?")] = _v.get(_r.get("verdict", "?"), 0) + 1
        if _r.get("gap_reason"):
            _g[_r["gap_reason"]] = _g.get(_r["gap_reason"], 0) + 1
    _mt = 0.0
    try:
        _mt = float(os.path.getmtime(_p))
    except OSError as _e:
        print(f"[WARN] ledger mtime 不可读: {_e}")
    # ★331批刀3：判定线同步为「violated 100% 带归属」（90% 全量线作废）
    _own: dict = {"verdict": "UNAVAILABLE"}
    try:
        from nucleus.world_model.expectation_ledger import violated_gap_ownership_check
        _own = violated_gap_ownership_check(_rows)
    except Exception as _oe:
        print(f"[WARN] violated 归属校验不可读: {_oe}")
    return {"rows": len(_rows), "verdicts": _v, "gap_reasons": _g, "mtime": _mt,
            "violated_ownership": _own}


def _framework_pid_info() -> dict:
    """★321批刀4：活体 PID + **进程创建时刻**（L 圈三证之二）。

    烛微321§4-㊿：`source=live` 标签**可以被手工调用打出来**（09:05:35 实测
    generated=114 source=live 而活体 PID 未变），所以标签不能单独采信。进程
    创建时刻由内核记账、无法伪造，与标签+事件账本同判才计 L 圈。
    只读：psutil 不可用时返回空并标注原因（不假装“无活体”）。
    """
    _out = {"pid": None, "create_time": None, "create_str": "", "match": [],
            "available": False, "note": ""}
    try:
        import psutil
    except Exception as _e:
        silent_exc(_e, where="tools.stress.collect_stress_baseline.pid.import")
        _out["note"] = "psutil 不可用: %s" % type(_e).__name__
        return _out
    _out["available"] = True
    try:
        for _p in psutil.process_iter(["pid", "cmdline", "create_time"]):
            _cl = _p.info.get("cmdline") or []
            if any(os.path.basename(str(_a)).lower() == "main.py" for _a in _cl):
                _out["match"].append({"pid": _p.info.get("pid"),
                                      "create_time": _p.info.get("create_time")})
    except Exception as _e:
        silent_exc(_e, where="tools.stress.collect_stress_baseline.pid.iter")
        _out["note"] = "process_iter 失败: %s" % type(_e).__name__
    if _out["match"]:
        _best = sorted(_out["match"], key=lambda x: x.get("create_time") or 0)[-1]
        _out["pid"] = _best.get("pid")
        _out["create_time"] = _best.get("create_time")
        _ts = float(_best.get("create_time") or 0)
        if _ts:
            _out["create_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_ts))
    elif _out["available"]:
        _out["note"] = "未发现 main.py 活体进程（停机态）"
    return _out


def _rel(path: str) -> str:
    try:
        return os.path.relpath(path, _ROOT).replace("\\", "/")
    except Exception as _e:
        silent_exc(_e, where="tools.stress.collect_stress_baseline.rel")
        return path


def l_loop_triple_proof(_pid: dict) -> dict:
    """★321批刀4：L 圈**三证同判**的可计算部分（只读：不做结论，只给三证原料）。

    ① `source_process=live` 标签条数 ② 活体 PID 创建时刻 ③ `pulse_pairs_*.jsonl` 事件账本。
    三者一致才计该圈为有效自转圈；任一不符 ⇒ 该圈作废（见模板 §五ter）。
    `consistent` 故意留 `None`：三证里有两证需要人对 T0 时间轴判读，
    脚本不替人下结论（避“算出来的假真”）。
    """
    _live = 0
    _p = os.path.join(_ROOT, "data", "world_model", "expectation_ledger.jsonl")
    try:
        with open(_p, encoding="utf-8", errors="ignore") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _r = json.loads(_ln)
                except ValueError as _e:
                    silent_exc(_e, where="tools.stress.collect_stress_baseline.triple.row")
                    continue
                _s = str(_r.get("source_process") or "")
                if _s == "live" or (not _s and str(_r.get("source") or "") == "live"):
                    _live += 1
    except Exception as _e:
        silent_exc(_e, where="tools.stress.collect_stress_baseline.triple.read")
    _pairs: list = []
    try:
        _pairs = sorted(glob.glob(os.path.join(_ROOT, "**", "pulse_pairs_*.jsonl"),
                                  recursive=True))[-8:]
    except Exception as _e:
        silent_exc(_e, where="tools.stress.collect_stress_baseline.triple.pairs")
    return {
        "p1_tag_live_rows": _live,
        "p2_pid": _pid.get("pid"),
        "p2_pid_create_str": _pid.get("create_str"),
        "p3_pair_files": [_rel(x) for x in _pairs],
        "p3_pair_count": len(_pairs),
        "consistent": None,
        "rule": "三证一致才计 L 圈；任一不符 ⇒ 该圈作废",
    }


# ===== ★325批刀5：开档三字段取数（evidence_tier 分布 / trigger_counts / hard-soft 候选数）=====
# 取数优先级：**活体落盘 > 进程内统计 > unavailable**。
# 为什么不用进程内统计兜底活体数据：压测报告是外部进程跑的，进程内统计恒为空，
# 拿空当"0 候选"就是把"没接通"报成"没有"，正是 314 批定的红线。
_M325_BY_VERSION_REL = os.path.join("data", "reports", "code_review", "by_version")
_M325_INTAKE_REL = os.path.join("data", "reports", "evolution",
                                "intake_summary_latest.json")


def _m325_latest_per_item(root: str) -> dict:
    """取最新一份 per-item 留样（含 evidence_tier_dist）。找不到返回 {}。"""
    _d = os.path.join(root, _M325_BY_VERSION_REL)
    if not os.path.isdir(_d):
        return {}
    _best = None
    _best_m = -1.0
    for _dp, _dn, _fn in os.walk(_d):
        for _f in _fn:
            if not (_f.startswith("run_") and _f.endswith(".json")):
                continue
            _p = os.path.join(_dp, _f)
            try:
                _m = os.path.getmtime(_p)
            except OSError:
                continue
            if _m > _best_m:
                _best, _best_m = _p, _m
    if not _best:
        return {}
    try:
        _j = json.load(open(_best, encoding="utf-8"))
    except Exception as _e:
        silent_exc(_e, where="collect_stress_baseline::_m325_latest_per_item")
        return {}
    _dist = _j.get("evidence_tier_dist")
    return {"path": _rel(_best), "count": _j.get("count"),
            "source_count": _j.get("source_count"),
            "truncated": _j.get("truncated"),
            "evidence_tier_dist": (_dist if isinstance(_dist, dict) else None)}


def _m325_intake_summary(root: str) -> dict:
    """读 EvolutionLoop 落盘的本拍入料摘要（325刀0 新增的唯一可观测口）。"""
    _p = os.path.join(root, _M325_INTAKE_REL)
    if not os.path.isfile(_p):
        return {}
    try:
        _j = json.load(open(_p, encoding="utf-8"))
    except Exception as _e:
        silent_exc(_e, where="collect_stress_baseline::_m325_intake_summary")
        return {}
    if not isinstance(_j, dict):
        return {}
    _j["_path"] = _rel(_p)
    return _j


def m325_open_gate_fields(root: str | None = None) -> dict:
    """★325刀5：开档三字段汇总（渲染与外部复算共用同一实现）。"""
    _root = root or _ROOT
    _pi = _m325_latest_per_item(_root)
    _isum = _m325_intake_summary(_root)
    _dist = _pi.get("evidence_tier_dist") or None
    # ★330批刀1：三档 → 四档。`hard_strict` 才是 **N 口径**；`hard_loose` 是
    #   图外还有痕迹（只降级待复核）；`hard_legacy` 是 330 前的粗档（只作基线对照）。
    #   旧键 `hard_candidates` 保留，值改为 strict（330 之后的 N 就该是它）；
    #   若留样还是旧格式（只有 hard），则退回 legacy 值并标 src，绝不静默混报。
    _hs = (_dist or {}).get("hard_strict") if _dist else None
    _hl = (_dist or {}).get("hard_loose") if _dist else None
    _hg = (_dist or {}).get("hard_legacy") if _dist else None
    _hard = _hs if _hs is not None else _hg
    _tier_src = ("hard_strict" if _hs is not None
                 else ("hard_legacy" if _hg is not None else "unavailable"))
    _soft = (_dist or {}).get("soft") if _dist else None
    _tc = (_isum.get("trigger_counts") if isinstance(_isum, dict) else None) or None
    # ★331批刀5：报告模板增**进程代次**字段（PID ＋ 进程创建时刻）。
    #   两个面各记一份：活体面（观测对象）与计数面（本进程统计），
    #   混在一份里会出现"用 A 进程的代次标注 B 进程的计数"。
    _pg_live = _framework_pid_info()
    _pg_stats: dict = {"available": False, "note": ""}
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import get_output_rate_stats
        _pg_stats = (get_output_rate_stats().get("proc_gen") or {})
        _pg_stats = dict(_pg_stats, available=True)
    except Exception as _pge:
        # ★331批刀5·cw2整改：不可读必须**说出来**（否则读盘失败被静默吞掉，
        #   报表上"代次不可用"与"压根没取"长得一样）
        print(f"[WARN] 进程代次不可读: {_pge}")
        _pg_stats = {"available": False, "note": "%s" % type(_pge).__name__}
    return {
        "proc_gen_live": {"pid": _pg_live.get("pid"),
                          "create_time": _pg_live.get("create_time"),
                          "create_str": _pg_live.get("create_str"),
                          "available": bool(_pg_live.get("available"))},
        "proc_gen_stats": _pg_stats,
        "evidence_tier_dist": _dist,
        "evidence_tier_src": (_pi.get("path") or "") or "unavailable",
        "per_item_count": _pi.get("count"),
        "per_item_truncated": _pi.get("truncated"),
        "hard_candidates": _hard,
        # ★330批刀1：四档单列（N 口径 = hard_strict；legacy 不计入 N）
        "hard_strict_candidates": _hs,
        "hard_loose_candidates": _hl,
        "hard_legacy_candidates": _hg,
        "hard_candidates_tier_src": _tier_src,
        "hard_unverified_candidates": ((_dist or {}).get("hard_unverified")
                                       if _dist else None),
        "n_source": ("evidence_tier=hard_strict（图内三层无入边 且 图外 probe 四类皆空）"
                     if _hs is not None else "unavailable（无 strict 读数）"),
        "soft_candidates": _soft,
        "trigger_counts": _tc,
        "trigger_counts_src": (_isum.get("_path") or "") or "unavailable",
        "growth_stats": (_isum.get("growth_stats") if isinstance(_isum, dict) else None),
    }


def _m325_open_gate_safe(root: str) -> dict:
    """刀5 的安全包装：取数失败也给出可渲染的 `unavailable`，绝不让报告生成失败。"""
    try:
        return m325_open_gate_fields(root)
    except Exception as _e:
        silent_exc(_e, where="collect_stress_baseline::_m325_open_gate_safe", level="warning")
        return {"evidence_tier_dist": None, "evidence_tier_src": "unavailable",
                "hard_candidates": None, "soft_candidates": None,
                "trigger_counts": None, "trigger_counts_src": "unavailable"}


def render_markdown(res: dict) -> str:
    """★321批刀5：把快照渲染成 Markdown（多圈趋势自动报告最小路径）。"""
    _b = res.get("baseline") or {}
    _pv = res.get("provenance") or {}
    _tp = res.get("triple_proof") or {}
    _L = []
    _L.append("# 压测快照（自动生成）· 圈次=%s" % (res.get("loop") or "-"))
    _L.append("")
    _L.append("> 生成时间 %s；口径：开三环闭四环（执行环不真执行）。"
              "本文只输出读数，不代替人做通过判定。" % _b.get("as_of_str"))
    _L.append("")
    _L.append("## 一、八项基线")
    _L.append("")
    _L.append("| 项 | 值 |")
    _L.append("|---|---|")
    for _k, _v in (("1 台账", _b.get("ledger_patches")), ("2 pending", _b.get("pending")),
                   ("3 待复核/覆盖率", _b.get("dead_code_review")),
                   ("4 图", _b.get("graph")), ("5 hub", _b.get("hub")),
                   ("6 ledger", _b.get("expectation_ledger")),
                   ("7 产出率", _b.get("output_rate")), ("8 M92", _b.get("m92"))):
        _L.append("| %s | `%s` |" % (_k, str(_v).replace("|", "\\|")))
    _L.append("")
    _L.append("## 二、来源维度（320刀2 · 防 L 圈伪造）")
    _L.append("")
    _L.append("| 项 | 值 |")
    _L.append("|---|---|")
    for _k in ("ledger_by_source", "ledger_no_source_legacy", "fingerprint_live",
               "fingerprint_verification", "graph_edge_types", "graph_edge_types_src",
               "graph_edge_types_agg", "graph_edge_types_folded",
               "r1_edge_counts", "r1_in_live", "log_last_ts"):
        _L.append("| %s | `%s` |" % (_k, str(_pv.get(_k)).replace("|", "\\|")))
    _L.append("")
    _og = res.get("open_gate") or {}
    _L.append("## 二ter、开档三字段（★325刀5）")
    _L.append("")
    _L.append("> 空数据一律 `unavailable`，**不填 0**——0=真的没有，unavailable=没接通。")
    _L.append("")
    _L.append("| 字段 | 值 | 来源 |")
    _L.append("|---|---|---|")
    _dist = _og.get("evidence_tier_dist")
    _L.append("| evidence_tier 分布 | `%s` | `%s` |" % (
        ("unavailable" if not _dist else
         "hard_strict=%s/hard_loose=%s/hard_unverified=%s/soft=%s/infer-only=%s"
         % (_dist.get("hard_strict"), _dist.get("hard_loose"),
            _dist.get("hard_unverified"), _dist.get("soft"),
            _dist.get("infer-only"))),
        str(_og.get("evidence_tier_src") or "unavailable").replace("|", "\\|")))
    if _dist and _dist.get("hard_legacy"):
        _L.append("| hard_legacy（330 前粗档） | `%s` | 同上（**只作基线对照，"
                  "不计入 N**） |" % _dist.get("hard_legacy"))
    # ★330批刀1（+331 要求）：**hard_strict 与解析率分开两行印**——
    #   两者是不同尺子，并排会让人误以为"解析率高 ⇒ N 就低"。
    _L.append("| **hard_strict 候选数（N 口径）** | `%s` | %s |" % (
        "unavailable" if _og.get("hard_strict_candidates") is None
        else _og.get("hard_strict_candidates"),
        str(_og.get("hard_candidates_tier_src") or "unavailable")))
    _L.append("| hard_loose 候选数（只降级待复核） | `%s` | 同上 |" % (
        "unavailable" if _og.get("hard_loose_candidates") is None
        else _og.get("hard_loose_candidates")))
    _L.append("| 解析率（**另一把尺子，与 N 无关**） | `%s` | 调用图 stats（baseline.m92） |" % (
        "unavailable" if (_b.get("m92") or {}).get("resolve_rate") is None
        else (_b.get("m92") or {}).get("resolve_rate")))
    _L.append("| hard 候选数（兼容键，值同 strict） | `%s` | 同上 |" % (
        "unavailable" if _og.get("hard_candidates") is None else _og.get("hard_candidates")))
    _L.append("| soft 候选数 | `%s` | 同上 |" % (
        "unavailable" if _og.get("soft_candidates") is None else _og.get("soft_candidates")))
    _tc = _og.get("trigger_counts")
    _L.append("| trigger_counts | `%s` | `%s` |" % (
        ("unavailable" if not _tc else
         "defect=%s/growth_need=%s" % (_tc.get("defect"), _tc.get("growth_need"))),
        str(_og.get("trigger_counts_src") or "unavailable").replace("|", "\\|")))
    _L.append("")
    _L.append("## 三、L 圈三证（★321刀4）")
    _L.append("")
    _L.append("| 证 | 值 |")
    _L.append("|---|---|")
    for _k in ("p1_tag_live_rows", "p2_pid", "p2_pid_create_str", "p3_pair_count", "p3_pair_files"):
        _L.append("| %s | `%s` |" % (_k, str(_tp.get(_k)).replace("|", "\\|")))
    _L.append("| consistent | `%s`（需人对 T0 判读） |" % _tp.get("consistent"))
    _L.append("")
    _L.append("> 规则：%s" % _tp.get("rule"))
    _L.append("")
    _L.append("## 四、七段留痕 / 人推计数 / 任务面")
    _L.append("")
    for _k, _lbl in (("segments", "七段留痕"), ("human_push", "人推计数"),
                     ("task_face", "任务面")):
        _L.append("- **%s**: `%s`" % (_lbl, str(res.get(_k)).replace("|", "\\|")))
    _L.append("")
    _L.append("*本文由 tools/stress/collect_stress_baseline.py --md 自动生成。*")
    return "\n".join(_L) + "\n"


def source_provenance() -> dict:
    """★320批刀2：来源进程维度——区分「活体产出」与「验证运行产出」。

    烛微320§3-维度3：两者混在同一份日志/数据里，L 圈最容易被验证运行伪造。
    本函数只读，输出：ledger 按来源计数、版本指纹命中、图 edge_types、日志窗口。
    """
    _out: dict = {}
    # 1) ledger 按来源计数
    _p = os.path.join(_ROOT, "data", "world_model", "expectation_ledger.jsonl")
    _by_src: dict[str, int] = {}
    _no_src = 0
    _hours: dict[str, int] = {}
    try:
        with open(_p, encoding="utf-8", errors="ignore") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _r = json.loads(_line)
                except ValueError as _e:
                    silent_exc(_e, where="tools.stress.collect_stress_baseline.provenance")
                    continue
                _s = str(_r.get("source_process") or "")
                if not _s:
                    _s2 = str(_r.get("source") or "")
                    _s = _s2 if _s2 in ("live", "verification") else ""
                if _s:
                    _by_src[_s] = _by_src.get(_s, 0) + 1
                else:
                    _no_src += 1
                _ts = float((_r.get("measured") or {}).get("observed_at") or 0)
                if _ts:
                    _h = time.strftime("%Y-%m-%d %H", time.localtime(_ts))
                    _hours[_h] = _hours.get(_h, 0) + 1
    except Exception as _e:
        print(f"[WARN] ledger 来源统计失败: {_e}")
    _out["ledger_by_source"] = _by_src
    _out["ledger_no_source_legacy"] = _no_src
    _out["ledger_by_hour"] = dict(sorted(_hours.items())[-12:])

    # 2) 版本指纹命中（活体 vs 验证运行）
    _fp_live = ["[307刀1]", "[308刀2]", "[317刀0]", "[317刀3]", "[316刀2]",
                "[320刀0]", "[320刀6]"]
    _fp_verify = ["[318刀0]", "[318刀4]", "[316刀3]", "cluster_view", "mastery_assertion_check"]
    _hits: dict[str, int] = {}
    _last_ts = ""
    try:
        import glob as _glob
        for _f in _glob.glob(os.path.join(_ROOT, "logs", "pulse*.log*")):
            try:
                with open(_f, "rb") as _fh:
                    _s = _fh.read().decode("utf-8", "ignore")
            except Exception:
                continue
            for _m in _fp_live + _fp_verify:
                _c = _s.count(_m)
                if _c:
                    _hits[_m] = _hits.get(_m, 0) + _c
            for _ln in _s.splitlines()[-200:]:
                if len(_ln) > 19 and _ln[4] == "-" and _ln[13] == ":":
                    if _ln[:19] > _last_ts:
                        _last_ts = _ln[:19]
    except Exception as _e:
        print(f"[WARN] 指纹扫描失败: {_e}")
    _out["fingerprints"] = _hits
    _out["fingerprint_live"] = {k: v for k, v in _hits.items() if k in _fp_live}
    _out["fingerprint_verification"] = {k: v for k, v in _hits.items() if k in _fp_verify}
    _out["log_last_ts"] = _last_ts

    # 3) 图 edge_types（R1 是否进活体）
    # ★328批刀1：这里原来有两层错，叠在一起让读数**永远错**却没人发现：
    #   ① 读的是 `stats.edge_types`，而落盘写的是**顶层** `edge_types` ⇒ 恒为空字典；
    #   ② 就算读对顶层，328 之前的聚合把 unique_name/self_attr/typed_infer 折进了
    #      static ⇒ 按聚合判 R1 也照样判不出（"折叠"和"没进"读数一样）。
    #   所以判据改到**逐边统计**上（权威），聚合只作汇总展示：
    #   `graph_edge_types`        = 逐边口径（权威读数）
    #   `graph_edge_types_agg`    = 落盘聚合（展示用，可能被折叠）
    #   `graph_edge_types_folded` = 聚合 static 超出逐边 static 的条数（>0 即折叠）
    _g = _latest("data/call_graph/graph_*.json")
    _gd = _read_json(_g, {}) or {}
    _agg_raw = _gd.get("edge_types")
    _agg = {str(_k): int(_v) for _k, _v in _agg_raw.items()
            if isinstance(_agg_raw, dict)} if isinstance(_agg_raw, dict) else {}
    _cnt = {}
    for _e in (_gd.get("edges") or []):
        if not isinstance(_e, dict):
            continue
        _k = str(_e.get("kind") or "static")
        _cnt[_k] = int(_cnt.get(_k, 0)) + 1
    _out["graph_file"] = os.path.basename(_g) if _g else ""
    if _cnt:
        _out["graph_edge_types"] = _cnt
        _out["graph_edge_types_src"] = "graph.edges[].kind(逐边统计)"
    elif _agg:
        _out["graph_edge_types"] = _agg
        _out["graph_edge_types_src"] = "graph.edge_types(仅聚合，无逐边可算)"
    else:
        _out["graph_edge_types"] = {}
        _out["graph_edge_types_src"] = "unavailable"
    _out["graph_edge_types_agg"] = _agg
    _out["graph_edge_types_folded"] = max(
        0, int(_agg.get("static", 0)) - int(_cnt.get("static", 0))) if _cnt else None
    _et = _out["graph_edge_types"]
    _out["r1_edge_counts"] = {_k: int(_et.get(_k, 0))
                              for _k in ("unique_name", "self_attr") if _k in _et}
    # R1 是否已进活体：按**逐边口径**判（聚合折叠不再影响这个读数）
    _out["r1_in_live"] = bool(_out["r1_edge_counts"])
    return _out


def baseline() -> dict:
    """八项基线快照。"""
    _out: dict[str, Any] = {"as_of": time.time(),
                            "as_of_str": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 1) 台账（approved / runtime_verified）
    _led = _read_json(os.path.join(_ROOT, "data", "patches", "patch_history.json"), {})
    if isinstance(_led, dict):
        _led = _led.get("patches") or _led.get("history") or []
    if not isinstance(_led, list):
        _led = []
    _st: dict[str, int] = {}
    for _p in _led:
        if isinstance(_p, dict):
            _k = str(_p.get("status", "?"))
            _st[_k] = _st.get(_k, 0) + 1
    _out["ledger_patches"] = {"total": len(_led), "by_status": _st}
    # 2) pending
    _pend = _read_json(os.path.join(_ROOT, "data", "patches", "pending_patches.json"), [])
    if isinstance(_pend, dict):
        _pend = _pend.get("patches") or _pend.get("pending") or []
    _out["pending"] = len(_pend) if isinstance(_pend, list) else 0
    # 3) 待复核 + 覆盖率（走 self_inspector 统计出口）
    try:
        from nucleus.self_inspector import get_dead_code_review_stats
        _s = get_dead_code_review_stats() or {}
        _out["dead_code_review"] = {
            "count": _s.get("count", 0), "mode": _s.get("mode", ""),
            "coverage": _s.get("coverage", 0.0),
            "resolve_rate": _s.get("resolve_rate"),
            "criteria_version": _s.get("criteria_version", ""),
            "graph_file": _s.get("graph_file", "")}
    except Exception as _e:
        print(f"[WARN] 待复核统计不可读: {_e}")
        _out["dead_code_review"] = {"error": type(_e).__name__}
    # 4) 图
    _g = _latest("data/call_graph/graph_*.json")
    _gd = _read_json(_g, {}) or {}
    _out["graph"] = {"file": os.path.basename(_g) if _g else "",
                     "nodes": len(_gd.get("nodes") or []),
                     "edges": len(_gd.get("edges") or []),
                     "stats": _gd.get("stats") or {}}
    # 5) hub
    _hub_p = os.path.join(_ROOT, "data", "verification_learning", "hub_entries.json")
    _hub = _read_json(_hub_p, None)
    if _hub is None:
        _hub_p2 = _latest("data/**/verification_learning*.json")
        _hub = _read_json(_hub_p2, None) if _hub_p2 else None
    if isinstance(_hub, dict):
        _hub = _hub.get("entries") or []
    if not isinstance(_hub, list):
        _hub = []
    _org: dict[str, int] = {}
    _origin: dict[str, int] = {}
    for _e in _hub:
        if not isinstance(_e, dict):
            continue
        _org[str(_e.get("organ", "?"))] = _org.get(str(_e.get("organ", "?")), 0) + 1
        _origin[str(_e.get("origin", "?"))] = _origin.get(str(_e.get("origin", "?")), 0) + 1
    _out["hub"] = {"entries": len(_hub), "by_organ": _org, "by_origin": _origin}
    # 6) ledger
    _out["expectation_ledger"] = _ledger()
    # 7) 产出率
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import get_output_rate_stats
        _out["output_rate"] = get_output_rate_stats()
    except Exception as _e:
        print(f"[WARN] 产出率不可读: {_e}")
        _out["output_rate"] = {"error": type(_e).__name__}
    # 8) M92
    _m92 = _out["dead_code_review"].get("mode", "")
    _out["m92"] = {"dead_code_mode": _m92,
                   "resolve_gate_judge": 0.50,
                   "high_confidence_gate": 0.70,
                   "resolve_rate": _out["dead_code_review"].get("resolve_rate")}
    return _out


def segment_counts(since_ts: float | None = None) -> dict[str, int]:
    """七段留痕计数（扫描 logs/pulse*.log*）。"""
    _out = {_name: 0 for _name, _ in SEGMENTS}
    _files = sorted(glob.glob(os.path.join(_ROOT, "logs", "pulse*.log*")))
    for _f in _files:
        try:
            with open(_f, "rb") as _fh:
                _s = _fh.read().decode("utf-8", "ignore")
        except Exception as _e:
            print(f"[WARN] 日志不可读 {_f}: {_e}")
            continue
        for _name, _anchor in SEGMENTS:
            _out[_name] += _s.count(_anchor)
    return _out


def human_push_counts() -> dict[str, int]:
    """人推计数（五类规则写死，可审计）。"""
    _out = {_name: 0 for _name, _ in HUMAN_PUSH}
    _files = sorted(glob.glob(os.path.join(_ROOT, "logs", "pulse*.log*")))
    for _f in _files:
        try:
            with open(_f, "rb") as _fh:
                _s = _fh.read().decode("utf-8", "ignore")
        except Exception as _e:
            print(f"[WARN] 日志不可读 {_f}: {_e}")
            continue
        for _name, _pat in HUMAN_PUSH:
            _out[_name] += len(re.findall(_pat, _s))
    return _out


def task_face() -> dict:
    """刀4：任务面前置判定——`reviewed>0` 才算"这段有料"。"""
    try:
        from nucleus.reasoning.SafeEvolutionExecutor import get_output_rate_stats
        _s = get_output_rate_stats()
    except Exception as _e:
        print(f"[WARN] 产出率不可读: {_e}")
        return {"task_face_empty": True, "reason": "产出率不可读"}
    _runs = int(_s.get("runs", 0) or 0)
    _prod = int(_s.get("productive", 0) or 0)
    return {"runs": _runs, "productive": _prod, "rate": _s.get("rate", 0.0),
            "task_face_empty": _prod == 0,
            "reason": ("零产出归因=判据看不见（子进程不可见父进程运行期指标）"
                       if _prod == 0 and _runs else "本窗尚无子进程跑批")}


def main() -> int:
    _ap = argparse.ArgumentParser(description="压测首跑：基线快照 + 七段留痕 + 人推计数")
    _ap.add_argument("--loop", type=int, default=None, help="压测圈次（写进输出）")
    _ap.add_argument("--json", default="", help="另存 JSON 路径")
    _ap.add_argument("--md", default="", help="另存 Markdown 渲染路径（321刀4 render_markdown 输出）")
    _a = _ap.parse_args()

    _pid = _framework_pid_info()
    _res = {"loop": _a.loop, "baseline": baseline(),
            "provenance": source_provenance(),
            "pid": _pid, "triple_proof": l_loop_triple_proof(_pid),
            "segments": segment_counts(), "human_push": human_push_counts(),
            "task_face": task_face(),
            # ★325批刀5：开档三字段（evidence_tier / trigger_counts / hard-soft 候选数）
            "open_gate": _m325_open_gate_safe(_ROOT),
            "note": "开三环闭四环：执行环(第四环)不真执行；本脚本只读统计"}

    print("=" * 62)
    print("压测首跑快照  圈次=%s  时间=%s" % (_a.loop or "-", _res["baseline"]["as_of_str"]))
    print("=" * 62)
    _b = _res["baseline"]
    print("1) 台账: %s" % _b["ledger_patches"])
    print("2) pending: %s" % _b["pending"])
    print("3) 待复核/覆盖率: %s" % _b["dead_code_review"])
    print("4) 图: %s nodes=%s edges=%s" % (_b["graph"]["file"], _b["graph"]["nodes"],
                                           _b["graph"]["edges"]))
    print("5) hub: %s" % _b["hub"])
    _pv = _res.get("provenance") or {}
    print("-" * 62)
    print("★来源维度（320刀2 · 防L圈伪造）")
    print("   ledger 按来源: %s ｜ 无source旧数据: %s"
          % (_pv.get("ledger_by_source"), _pv.get("ledger_no_source_legacy")))
    print("   ledger 按时段(末12): %s" % (_pv.get("ledger_by_hour"),))
    print("   活体指纹: %s" % (_pv.get("fingerprint_live"),))
    print("   验证运行指纹: %s" % (_pv.get("fingerprint_verification"),))
    print("   图: %s edge_types=%s R1已进活体=%s"
          % (_pv.get("graph_file"), _pv.get("graph_edge_types"), _pv.get("r1_in_live")))
    print("   日志末条时间: %s" % (_pv.get("log_last_ts"),))
    _tp = _res.get("triple_proof") or {}
    print("★L圈三证（★321刀4 · 任一不符则该圈作废）")
    print("   1) tag=live 条数: %s" % _tp.get("p1_tag_live_rows"))
    print("   2) 活体 PID: %s （创建于 %s）" % (_tp.get("p2_pid"),
                                                      _tp.get("p2_pid_create_str") or "-"))
    print("   3) pulse_pairs 账本: %s 份 %s" % (_tp.get("p3_pair_count"),
                                                    _tp.get("p3_pair_files")))
    print("   一致判定: 需对 T0 时间轴人工判读（脚本不代替人下结论）")
    print("6) ledger: %s" % _b["expectation_ledger"])
    print("7) 产出率: %s" % _b["output_rate"])
    print("8) M92: %s" % _b["m92"])
    print("-" * 62)
    print("七段留痕: %s" % _res["segments"])
    print("人推计数: %s" % _res["human_push"])
    print("任务面: %s" % _res["task_face"])
    print("-" * 62)
    print("注: %s" % _res["note"])

    if _a.md:
        _md_text = render_markdown(_res)
        with open(_a.md, "w", encoding="utf-8") as _f:
            _f.write(_md_text)
        print("已写出 Markdown: %s" % _a.md)
    if _a.json:
        with open(_a.json, "w", encoding="utf-8") as _f:
            json.dump(_res, _f, ensure_ascii=False, indent=2)
        print("已写出: %s" % _a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
