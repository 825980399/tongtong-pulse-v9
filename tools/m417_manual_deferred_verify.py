#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""417 刀4：不等 04:00，手动把 4 项 deferred 提前验一遍。

任务书四项（**两项口径已修正，见下**）：
  ① `narrative_store.persist_daily()` 能不能写出 `data/narrative/narrative_YYYYMMDD.jsonl`
  ② `experience_governance.mark_templates()` 能不能把 `is_template` 写上
     ★仓库里**没有** `mark_templates` —— 真入口是
       `annotate(pool=None, dry_run=True, limit=0, min_dup=3)`
       （`nucleus/observability/experience_governance.py:191`）
  ③ `o3_delete_dryrun.report(sample=8)` 的输出格式
  ④ 手动写一条叙事记录 → `load_recent()` 能不能读回

额外补的两项（deferred 账本里真正的堵点，任务书没点名但同因）：
  ①b `PulseNarrativeSelf.persist()` —— 416「`persist()` 生产里一次没执行」的
     直接当事人；只验 `persist_daily` 验不出这一层（它自己还包了一层 digest 拼接）
  ④b 414 刀2 的 `delta` 第四格 `narrative_events`（cell 结构）是否随行落盘

★安全边界（铁律 22：运行进程持有的数据文件不能由外部工具写）：
  `data/experience/experience_pool.json` 由 pid 19100 持有，15:54 刚被它写过一次
  ⇒ **绝不写**。② 的真写验证一律在隔离目录造一个 `ExperiencePool` 做
  「改原对象 → `_save()` → **新实例**读回」，生产池只做只读 scan + dry_run。
  `data/narrative/` 目录**本就不存在**（416 已证），框架只在 04:00 写它
  ⇒ ① 可以真写一条探测行，验完默认**回滚删除**，把「首次真落盘」留给 04:00。

用法：
    python tools/m417_manual_deferred_verify.py
    python tools/m417_manual_deferred_verify.py --keep --json tmp/x.json
    python tools/m417_manual_deferred_verify.py --skip-prod   # 完全不碰生产路径
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, str(ROOT))

# ★必须放在 sys.path 注入**之后**：`python tools/xxx.py` 的 sys.path[0] 是 tools/，
#   提前导入 nucleus 会 ModuleNotFoundError（单测里因为已插 ROOT 而照过 —— 假绿）。
from nucleus._silent_except import silent_exc  # noqa: E402

#: 结果累加器：每条 `{"id","name","verdict","detail"}`
RESULTS: list[dict] = []


def _rec(_id: str, _name: str, _cond: bool, _detail: dict) -> bool:
    RESULTS.append({"id": _id, "name": _name,
                    "verdict": "PASS" if _cond else "FAIL", "detail": _detail})
    return bool(_cond)


def _skip(_id: str, _name: str, _why: str) -> None:
    RESULTS.append({"id": _id, "name": _name, "verdict": "SKIP",
                    "detail": {"reason": _why}})


class _Sandbox:
    """把 `narrative_store` 的读写根临时指到隔离目录（读写两处同改）。"""

    def __init__(self, path: str):
        self.path = path
        self._old = None

    def __enter__(self):
        from nucleus.observability import narrative_store as _ns
        self._old = _ns.data_root()
        _ns.set_data_dir(self.path)
        return _ns

    def __exit__(self, *_exc):
        from nucleus.observability import narrative_store as _ns
        _ns.set_data_dir(self._old if self._old != _ns.project_root() else None)
        return False


# ============================================================ ① 隔离落盘
def check_1_isolated(base: str) -> None:
    _d = os.path.join(base, "k4_1")
    try:
        with _Sandbox(_d) as _ns:
            _now = time.time()
            _want = os.path.join(_d, "data", "narrative",
                                 "narrative_%s.jsonl" % _ns.day_stamp(_now))
            _r = _ns.persist_daily(now=_now, headline="[k4-1]", intent="probe",
                                   nodes={"cur": 1, "prev": None, "delta": None,
                                          "source": "probe", "reason": ""})
        _exists = os.path.exists(_want)
        _rows, _bad = _ns._read_lines(_want) if _exists else ([], 0)
        _rec("1", "persist_daily 隔离落盘", bool(_r.get("ok")) and _exists and len(_rows) == 1,
             {"ok": _r.get("ok"), "path": _r.get("path"), "expected_path": _want,
              "path_match": os.path.normcase(str(_r.get("path"))) == os.path.normcase(_want),
              "rows": len(_rows), "bad_lines": _bad, "reason": _r.get("reason", ""),
              "bytes": (os.path.getsize(_want) if _exists else 0)})
    except Exception as _e:
        _rec("1", "persist_daily 隔离落盘", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-800:]})


# ============================================================ ①b 生产路径
def check_1b_production(base: str, keep: bool) -> None:
    from nucleus.observability import narrative_store as _ns
    _now = time.time()
    _p = _ns.day_path(_now)
    _dir = _ns.narrative_dir()
    _had_dir = os.path.isdir(_dir)
    _had_file = os.path.exists(_p)
    try:
        _r = _ns.persist_daily(now=_now, headline="[m417-manual-verify] 手动落盘探测",
                               intent="probe:417刀4",
                               events=[{"type": "probe", "ts": round(_now, 3),
                                        "note": "m417_manual_verify"}],
                               nodes={"cur": None, "prev": None, "delta": None,
                                      "source": "manual_verify", "reason": "probe"},
                               _m417_manual_verify=True)
        _exists = os.path.exists(_p)
        _rows, _bad = _ns._read_lines(_p)
        _back = _ns.load_recent(days=1, now=_now)
        _cond = bool(_r.get("ok")) and _exists and len(_rows) >= 1 and _back["state"] == "ok"
        _rec("1b", "persist_daily 生产路径真写", _cond,
             {"ok": _r.get("ok"), "path": _r.get("path"), "rel": os.path.relpath(_p, ROOT),
              "dir_preexisted": _had_dir, "file_preexisted": _had_file,
              "rows": len(_rows), "bad_lines": _bad, "replaced": _r.get("replaced"),
              "bytes": (os.path.getsize(_p) if _exists else 0),
              "load_recent": {"state": _back["state"], "rows": len(_back["rows"]),
                              "days_with_file": _back["days_with_file"]},
              "reason": _r.get("reason", ""),
              "rolled_back": (not keep and not _had_file)})
    except Exception as _e:
        _rec("1b", "persist_daily 生产路径真写", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-800:]})
        return
    if keep or _had_file or _r.get("ok") is not True:
        return
    # 回滚：只删「本次探测刚创建的那个文件」与「本次刚建的空目录」
    try:
        os.remove(_p)
        if not _had_dir:
            try:
                os.rmdir(_dir)
            except OSError as _e_rm:
                silent_exc(_e_rm, where="tools.m417_manual_deferred_verify:rollback_rmdir",
                           level="debug")
    except OSError as _e2:
        _rec("1b-rb", "生产探测行回滚", False, {"error": str(_e2)})
        return
    _rec("1b-rb", "生产探测行回滚", not os.path.exists(_p),
         {"removed": os.path.relpath(_p, ROOT), "dir_removed": not os.path.isdir(_dir)})


# ============================================================ ①c 真调用方
def check_1c_real_caller(base: str) -> None:
    """`PulseNarrativeSelf.persist()` —— 416「生产里一次没执行」的正主。"""
    _d = os.path.join(base, "k4_1c")
    try:
        with _Sandbox(_d) as _ns:
            from organs.identity.PulseNarrativeSelf import PulseNarrativeSelf
            from nucleus.observability import narrative_digest as _nd
            _organ = PulseNarrativeSelf()
            _now = time.time()
            # ★事件形状必须与 `_on_record` 写进去的**逐字段一致**：
            #   `{id, content, type, user_name, emotional_tone, timestamp}`。
            #   首版用 `{"type","ts","organ"}` ⇒ `events_of_day` 读 `timestamp` 取不到
            #   ⇒ 落盘 events=2 但 `narrative_events=0`、headline「当日无叙事事件」。
            #   那是**探针错**，不是 bug（`narrative_digest.events_of_day:114`）。
            # 时间戳取 `digest_anchor`，这样无论几点跑都落在「摘要日」内。
            _ts = float(_nd.digest_anchor(_now))
            _organ._narrative_events = [
                {"id": 1, "content": "心脏多跳了一拍", "type": "organ_pulse",
                 "user_name": "系统", "emotional_tone": "neutral", "timestamp": _ts},
                {"id": 2, "content": "改了 config.py 的开关", "type": "code_patch",
                 "user_name": "系统", "emotional_tone": "neutral", "timestamp": _ts + 1.0},
                {"id": 3, "content": "用户说了句谢谢", "type": "conversation",
                 "user_name": "用户", "emotional_tone": "positive", "timestamp": _ts + 2.0},
            ]
            _r = _organ.persist(now=_now)
            _p = _ns.day_path(_now)
            _rows, _bad = _ns._read_lines(_p)
            _row = _rows[-1] if _rows else {}
        _dl = (_r.get("digest") or {}).get("delta") or {}
        _rec("1c", "PulseNarrativeSelf.persist 真调用方",
             bool(_r.get("ok")) and bool(_row) and _row.get("narrative_events") == 3,
             {"ok": _r.get("ok"), "path": os.path.relpath(str(_r.get("path")), ROOT),
              "rows": len(_rows), "bad_lines": _bad,
              "headline": (_row.get("headline") or "")[:60],
              "intent": (_row.get("intent") or "")[:40],
              "narrative_events": _row.get("narrative_events"),
              "events_landed": len(_row.get("events") or []),
              "continuous_days": _row.get("continuous_days"),
              "delta_keys": sorted(_dl.keys()),
              "delta_has_4th": "narrative_events" in _dl,
              "delta_narrative_events": (_dl.get("narrative_events") or {}).get("delta"),
              "digest_keys": sorted((_r.get("digest") or {}).keys()),
              "reason": _r.get("reason", "")})
    except Exception as _e:
        _rec("1c", "PulseNarrativeSelf.persist 真调用方", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-1200:]})


# ============================================================ ② 模板标记
def check_2_templates_readonly() -> None:
    from nucleus.observability import experience_governance as _eg
    try:
        _scan = _eg.scan_templates()
        _dry = _eg.annotate(dry_run=True)
        _rec("2", "生产池 scan + dry_run（只读）", _scan.get("state") in ("ok", "empty"),
             {"switch_on": _eg.enabled(), "version": _eg.version(),
              "scan_state": _scan.get("state"), "total": _scan.get("total"),
              "distinct_texts": _scan.get("distinct_texts"),
              "already_marked": _scan.get("already_marked"),
              "template_ratio": _scan.get("template_ratio"),
              "top_dup": (_scan.get("template_keys") or [])[:3],
              "dry_run": {"scanned": _dry.get("scanned"), "changed": _dry.get("changed"),
                          "template_rows": _dry.get("template_rows"),
                          "written": _dry.get("written"), "reason": _dry.get("reason")},
              "scan_reason": _scan.get("reason", "")})
    except Exception as _e:
        _rec("2", "生产池 scan + dry_run（只读）", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-800:]})


def _probe_exp(_id: str, _motivation: str, _summary: str, _ts: float, _ver: int) -> dict:
    """与 `record_experience` 写进池里**逐字段同形**的一条体验。

    为什么不能直接调 `record_experience` 造「同文本 3 条」：
    写入端有两道闸门会正当地拦掉 ——
      · `_classify_pollution_risk(content)`：空/极短内容判 `high`
      · `_screen_write_pollution()` 对 `polluted` **隔离不入库**（`experience_pool.py:234`）
    首版探针 `content=""` ⇒ 3 条全被判污染隔离，池恒 0 条
    （`pool_returned_nothing`，看着像 `annotate` 坏了，其实是探针造不出数据）。
    ⇒ 模板组只能**直接构造池状态**来复现。
    """
    return {
        "id": _id,
        "motivation": _motivation,
        "motivation_intensity": 0.5,
        "process_pressure": 0.0,
        "pressure_type": "cognitive",
        "result_reward": {"type": "cognitive", "intensity": 0.4},
        "field_frequency_snapshot": {},
        "emotion_tags": [],
        "emotion_intensity": 0.0,
        "timestamp": _ts,
        "decay_rate": 0.05,
        "is_summarized": False,
        "summary": _summary,
        "activation_count": 0,
        "summary_version": _ver,
    }


def check_2b_templates_write(base: str) -> None:
    """隔离池真写：`annotate(dry_run=False)` → `_save()` → **新实例**读回。"""
    from nucleus.observability import experience_governance as _eg
    _d = os.path.join(base, "k4_2b")
    _pf = os.path.join(_d, "experience_pool.json")
    _TXT = "重复的动机文本：四条的 summary 都为空，norm_key 全落在 motivation 上"
    try:
        from nucleus.mnemosyne.experience_pool import ExperiencePool
        _p = ExperiencePool(base_dir=_d)
        _ver = int(getattr(_p, "SUMMARY_VERSION", 1) or 1)
        _t = time.time()
        # 4 条同文本（summary 留空 ⇒ text_of 落到 motivation）+ 1 条干扰项
        _p._experiences = [_probe_exp("probe417_%02d" % _i, _TXT, "", _t - _i, _ver)
                           for _i in range(4)]
        _p._experiences.append(_probe_exp("probe417_keep", "完全不同的另一条动机", "",
                                          _t - 9, _ver))
        _p._save()
        _pre = len(ExperiencePool(base_dir=_d).query_experiences(limit=1000) or [])

        _r = _eg.annotate(pool=_p, dry_run=False, min_dup=3)
        # ★关键：换一个**新实例**从盘上读，才算「真落盘」（内存里改到不算 —— 413 铁律）
        _back = ExperiencePool(base_dir=_d).query_experiences(limit=1000) or []
        _marked = [x for x in _back if _eg.is_template(x)]
        _plain = [x for x in _back if not _eg.is_template(x)]
        _w_marked = sorted({round(_eg.retrieval_weight(x), 3) for x in _marked})
        _w_plain = sorted({round(_eg.retrieval_weight(x), 3) for x in _plain})
        # 幂等：再跑一次应当 changed=0
        _r2 = _eg.annotate(pool=ExperiencePool(base_dir=_d), dry_run=False, min_dup=3)
        _cond = (bool(_r.get("written")) and _r.get("landed") == _r.get("template_rows") == 4
                 and len(_marked) == 4 and len(_plain) == 1
                 and _w_marked == [round(_eg.TEMPLATE_WEIGHT, 3)] and _w_plain == [1.0]
                 and _r2.get("changed") == 0)
        _rec("2b", "隔离池 annotate 真写 + 新实例读回", _cond,
             {"annotate": _r, "annotate_2nd": {"changed": _r2.get("changed"),
                                               "written": _r2.get("written"),
                                               "reason": _r2.get("reason")},
              "pool_file": os.path.relpath(_pf, ROOT),
              "file_bytes": (os.path.getsize(_pf) if os.path.exists(_pf) else 0),
              "rows_before": _pre, "reread_total": len(_back),
              "reread_marked": len(_marked), "reread_plain": len(_plain),
              "weights_marked": _w_marked, "weights_plain": _w_plain,
              "template_weight": _eg.TEMPLATE_WEIGHT,
              "sample_key": ((_marked[0].get("template_key") or "")[:48] if _marked else "")})
    except Exception as _e:
        _rec("2b", "隔离池 annotate 真写 + 新实例读回", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-1200:]})


# ============================================================ ③ O3 报表
_O3_KEYS = ("version", "generated_at", "dry_run", "applied", "source", "entries_state",
            "scanned", "o3_total", "eligible", "undecidable", "rejected", "samples",
            "graph", "basis", "reason")


def check_3_o3_report() -> None:
    from nucleus.evolution import o3_delete_dryrun as _dd
    try:
        _t0 = time.time()
        _r = _dd.report(sample=8)
        _ms = round((time.time() - _t0) * 1000.0, 1)
        _miss = [k for k in _O3_KEYS if k not in _r]
        _s = _r.get("samples") or []
        _cond = (not _miss and _r.get("dry_run") is True and _r.get("applied") == 0
                 and len(_s) <= 8 and _r.get("entries_state") == "ok")
        _rec("3", "o3_delete_dryrun.report(sample=8) 输出格式", _cond,
             {"version": _r.get("version"), "switch_on": _dd.enabled(),
              "missing_keys": _miss, "dry_run": _r.get("dry_run"),
              "applied": _r.get("applied"), "entries_state": _r.get("entries_state"),
              "source": _r.get("source"), "scanned": _r.get("scanned"),
              "o3_total": _r.get("o3_total"), "eligible": _r.get("eligible"),
              "undecidable": _r.get("undecidable"),
              "rejected_top": dict(list((_r.get("rejected") or {}).items())[:6]),
              "samples_n": len(_s), "samples": _s[:3],
              "graph": _r.get("graph"), "reason": _r.get("reason", ""),
              "elapsed_ms": _ms, "basis_chars": len(str(_r.get("basis") or ""))})
    except Exception as _e:
        _rec("3", "o3_delete_dryrun.report(sample=8) 输出格式", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-800:]})


# ============================================================ ④ 写→读回
def check_4_roundtrip(base: str) -> None:
    _d = os.path.join(base, "k4_4")
    try:
        with _Sandbox(_d) as _ns:
            _now = time.time()
            _evs = [{"type": "organ_pulse", "ts": round(_now - 5, 3), "organ": "心脏"},
                    {"type": "code_patch", "ts": round(_now - 3, 3), "file": "config.py"},
                    {"type": "narrative", "ts": round(_now - 1, 3), "note": "roundtrip"}]
            _w = _ns.persist_daily(
                now=_now, headline="[k4-4] 往返探针", intent="probe",
                narrative_events=len(_evs), continuous_days=0, events=_evs,
                nodes={"cur": 12, "prev": 10, "delta": 2, "source": "probe", "reason": ""},
                patches={"cur": 1, "prev": 1, "delta": 0, "source": "probe", "reason": ""},
                delta_narrative_events={"cur": 3, "prev": 2, "delta": 1,
                                        "source": "probe", "reason": ""})
            _r = _ns.load_recent(days=1, now=_now)
        _rows = _r.get("rows") or []
        _one = _rows[0] if _rows else {}
        _cond = (_w.get("ok") and _r.get("state") == "ok" and len(_rows) == 1
                 and _one.get("headline") == "[k4-4] 往返探针"
                 and len(_one.get("events") or []) == 3
                 and "narrative_events" in (_one.get("delta") or {}))
        _rec("4", "写一条 → load_recent 读回", _cond,
             {"write_ok": _w.get("ok"), "state": _r.get("state"),
              "rows": len(_rows), "days_with_file": _r.get("days_with_file"),
              "bad_lines": _r.get("bad_lines"), "truncated": _r.get("truncated"),
              "headline": _one.get("headline"), "intent": _one.get("intent"),
              "narrative_events": _one.get("narrative_events"),
              "events_readback": len(_one.get("events") or []),
              "events_truncated": _one.get("events_truncated"),
              "delta_keys": sorted((_one.get("delta") or {}).keys()),
              "nodes_delta": (_one.get("delta") or {}).get("nodes"),
              "version": _one.get("version"),
              "extra_keys": sorted((_one.get("extra") or {}).keys()),
              "reason": _w.get("reason", "")})
    except Exception as _e:
        _rec("4", "写一条 → load_recent 读回", False,
             {"error": "%s:%s" % (type(_e).__name__, _e), "tb": traceback.format_exc()[-800:]})


# ============================================================ main
def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="417 刀4：手动提前验证 deferred 项")
    _ap.add_argument("--basetemp", default=os.path.join("tmp", "m417_k4_verify"))
    _ap.add_argument("--json", default=os.path.join("tmp", "m417_k4_verify.json"))
    _ap.add_argument("--keep", action="store_true", help="不删除 ①b 写进生产 data/narrative/ 的探测行")
    _ap.add_argument("--skip-prod", action="store_true", help="完全不碰生产 data/ 路径")
    _a = _ap.parse_args(argv)

    _base = os.path.abspath(os.path.join(ROOT, _a.basetemp))
    if os.path.isdir(_base):
        shutil.rmtree(_base, ignore_errors=True)
    os.makedirs(_base, exist_ok=True)

    print("=" * 74)
    print("417 刀4 · 手动提前验证 deferred（basetemp=%s）" % os.path.relpath(_base, ROOT))
    print("=" * 74)

    check_1_isolated(_base)
    if _a.skip_prod:
        _skip("1b", "persist_daily 生产路径真写", "--skip-prod")
    else:
        check_1b_production(_base, _a.keep)
    check_1c_real_caller(_base)
    check_2_templates_readonly()
    check_2b_templates_write(_base)
    check_3_o3_report()
    check_4_roundtrip(_base)

    for _r in RESULTS:
        print("\n[%s] %s (%s)" % (_r["verdict"], _r["name"], _r["id"]))
        print(json.dumps(_r["detail"], ensure_ascii=False, indent=2, default=str))

    _p = sum(1 for x in RESULTS if x["verdict"] == "PASS")
    _f = sum(1 for x in RESULTS if x["verdict"] == "FAIL")
    _s = sum(1 for x in RESULTS if x["verdict"] == "SKIP")
    print("\n" + "=" * 74)
    print("汇总：PASS %d / FAIL %d / SKIP %d" % (_p, _f, _s))
    for _r in RESULTS:
        print("  %-4s %s  %s" % (_r["verdict"], _r["id"].ljust(6), _r["name"]))
    print("=" * 74)

    _out = {"generated_at": round(time.time(), 3), "basetemp": _a.basetemp,
            "pass": _p, "fail": _f, "skip": _s, "results": RESULTS}
    _jp = os.path.join(ROOT, _a.json)
    try:
        os.makedirs(os.path.dirname(_jp), exist_ok=True)
        with open(_jp, "w", encoding="utf-8", newline="\n") as _f2:
            json.dump(_out, _f2, ensure_ascii=False, indent=2, default=str)
        print("JSON → %s" % _a.json)
    except OSError as _e3:
        print("JSON 写失败：%s" % _e3)
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
