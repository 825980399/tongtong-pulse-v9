"""400 系列 · 409刀2 对账器命令行（只读盘账 / 手动跑一轮 / 合成演示）。

三种用法：

    python tools/m409_reconcile_run.py --stats
        只看账本状态与探针注册表，**不写任何东西**。

    python tools/m409_reconcile_run.py --run
        手动跑一轮对账（等价于每日 03:00 钩子，用于不必等次日）；会写盘。

    python tools/m409_reconcile_run.py --demo
        在临时目录里造 3 条合成假设（recurrence / effect / reuse 各一）+ 合成探针，
        跑一轮并打印三态。**不碰真实 data/**，用于证明「三态真的产得出来」。

★ 为什么需要 `--demo`：现有 30 条老账既无判据也无 `due_at`（刀1 只给它们打了
  `missing_criterion=True`），所以真账本上 `due_entries()` 恒为 0 —— 光跑 `--run`
  看不出任何三态。「机制能产出三态」这件事必须单独造场景证明。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import hypothesis_criteria as hc          # noqa: E402
from nucleus.evolution import hypothesis_reconciler as hr       # noqa: E402


def _p(msg: str = "") -> None:
    sys.stdout.write(str(msg) + "\n")


def cmd_stats() -> int:
    _p(f"[契约] hypothesis_criteria={hc.version()}  "
       f"hypothesis_reconciler={hr.version()}  开关={'ON' if hc.enabled() else 'OFF'}")
    _p(f"[账本] {hr.ledger_path()}")
    _d, _ok = hr._load_ledger(hr.ledger_path())
    if not _ok:
        _p("  !! 账本读不出来（本轮不可回写）")
        return 2
    _s = hc.summarize(_d.get("strategies") or {})
    _p(f"  total={_s['total']} 有判据={_s['has_criteria']} "
       f"标missing={_s['missing_criterion']} 漏网={_s['no_criteria_and_no_flag']} "
       f"已对账={_s['settled']}")
    import time
    _due = hr.due_entries(_d, now=time.time())
    _p(f"  [到期] 现在该判 {len(_due)} 条")
    for _k, _e in _due[:10]:
        _p(f"    - {_k[:70]}  due_at={_e.get(hc.F_DUE)}  "
           f"attempts={_e.get('resolve_attempts')}")
    _pr = hr.probes()
    _p(f"  [探针] 已注册 {len(_pr)} 个：{sorted(_pr) or '（无，刀3 才装）'}")
    for _rel, _name in ((hr.refuted_path(), "refuted 清单"),
                        (hr.candidate_path(), "本能候选"),
                        (hr.last_run_path(), "运行快照")):
        _p(f"  [{_name}] {'存在' if os.path.isfile(_rel) else '未生成'}  {_rel}")
    return 0


def cmd_run() -> int:
    _r = hr.reconcile()
    _p(f"[对账] skipped={_r.get('skipped') or '-'}  due={_r.get('due')}  "
       f"confirmed={_r.get('confirmed')}  refuted={_r.get('refuted')}  "
       f"unknown={_r.get('unknown')}  written={_r.get('written')}")
    _dump_rows(_r.get("rows") or [])
    return 0


def cmd_demo() -> int:
    """临时目录里造 3 条合成假设，证明三态链路真的通。

    ★ 合成探针证明的是**机制**，不是判据本身。真判据的实测数据由刀3 负责。
    """
    _tmp = tempfile.mkdtemp(prefix="m409k2_demo_")
    _saved = hr.probes()
    try:
        hr.set_data_dir(_tmp)
        hr._PROBES.clear()
        # 合成探针：三种结论各一个
        hr.register_probe(hc.KIND_RECURRENCE,
                          lambda e, c: {"result": hc.RESULT_CONFIRMED,
                                        "detail": "[合成] 窗口内同签名 0 条",
                                        "evidence": {"hits": 0}})
        hr.register_probe(hc.KIND_EFFECT,
                          lambda e, c: {"result": hc.RESULT_REFUTED,
                                        "detail": "[合成] verdict.passed=False",
                                        "evidence": {"passed": False}})
        # reuse 不注册 ⇒ 走 no_probe → unknown，正好演示「判不了不硬判」
        _now = 1_800_000_000.0
        _l = {"version": "335.1", "updated_at": 0.0, "strategies": {
            "syn|recurrence|type::x": {
                "rule": "type::x", "strategy": "[合成] 复发判据",
                hc.F_CRITERION: {"kind": hc.KIND_RECURRENCE, "signature": "a.py::t::m"},
                hc.F_WINDOW: "24h", hc.F_DUE: _now - 60},
            "syn|effect|type::y": {
                "rule": "type::y", "strategy": "[合成] 效果判据",
                hc.F_CRITERION: {"kind": hc.KIND_EFFECT, "patch_id": "p_syn"},
                hc.F_WINDOW: "24h", hc.F_DUE: _now - 60},
            "syn|reuse|type::z": {
                "rule": "type::z", "strategy": "[合成] 复用判据（探针缺失）",
                hc.F_CRITERION: {"kind": hc.KIND_REUSE, "signature": "a.py::t::m"},
                hc.F_WINDOW: "7d", hc.F_DUE: _now - 60},
            "syn|legacy|type::old": {
                "rule": "type::old", "strategy": "[合成] 老条目（无判据无到期）",
                hc.F_MISSING: True},
        }}
        _p_path = hr.ledger_path()
        os.makedirs(os.path.dirname(_p_path), exist_ok=True)
        with open(_p_path, "w", encoding="utf-8") as _f:
            json.dump(_l, _f, ensure_ascii=False, indent=2)

        _p("[演示] 落盘目录 = 临时目录（真实 data/ 零改动）")
        _r = hr.reconcile(now=_now, path=_p_path)
        _p(f"[演示] due={_r['due']}  confirmed={_r['confirmed']}  "
           f"refuted={_r['refuted']}  unknown={_r['unknown']}  "
           f"written={_r['written']}")
        _dump_rows(_r.get("rows") or [])
        _p(f"[演示] refuted 清单条数={_r['refuted_total']}  "
           f"新入候选={_r['candidates_queued']}")
        _p(f"[演示] 本能 当前={_r['instinct_current']}/上限={_r['instinct_cap']} "
           f"冷却={_r.get('instinct_cooldown_days')}天")
        for _rel, _name in ((hr.refuted_path(), "refuted"),
                            (hr.candidate_path(), "candidates"),
                            (hr.last_run_path(), "last_run")):
            _p(f"[演示] {_name}: {os.path.basename(_rel)} "
               f"{'已写' if os.path.isfile(_rel) else '未写'}")
        return 0
    finally:
        hr._PROBES.clear()
        hr._PROBES.update(_saved)
        hr.set_data_dir(None)
        shutil.rmtree(_tmp, ignore_errors=True)


def cmd_probe() -> int:
    """拿**真实数据**跑三个判据，打印三态实测输出。

    ★ 全程 `dry_run=True` + 账本写在 tmp/ ⇒ 真实 `data/` 零改动，所以不需要
      重定向数据源根（重定向会同时改掉数据源和落盘根，反而是首跑的坑）。
    """
    from nucleus.evolution import hypothesis_probes as hp
    import time

    _p("[自检] " + json.dumps(hp.selfcheck(), ensure_ascii=False))
    _hp_installed = hp.install()
    _p(f"[探针] 安装 {_hp_installed}")

    _snaps = hp._audit_snapshots()
    _now = _snaps[-1][0] if _snaps else time.time()
    _eff = hp._load_effect().get("verdicts") or {}
    _by = {"true": [], "false": [], "none": []}
    for _pid, _v in _eff.items():
        _by["true" if _v.get("passed") is True else
            "false" if _v.get("passed") is False else "none"].append(_pid)

    _strat = {}

    def _add(tag, crit, win, due):
        _strat[tag] = {"rule": f"demo::{tag}", "strategy": f"[实测样本] {tag}",
                       hc.F_CRITERION: crit, hc.F_WINDOW: win, hc.F_DUE: due}

    # 判据1 复发：一条真实签名（应 refuted）+ 一条不存在的签名（应 confirmed）
    #          + 一条窗口外无覆盖的（应 unknown）
    if _snaps:
        # ★两个挑选条件都是为了"能演示到命中分支"：
        #   1) 排除 wontfix —— 探针显式排除它（"主动不修"不能拿来说补丁失效）
        #   2) `last_seen_ts` 必须落在窗口内 —— 否则同签名在池中也判不出复发
        #   （第一版只挑了"有 method"，结果 last_seen 太旧，命中 0 次，
        #    表面 confirmed 实际谁也没验到）
        _e0 = next((e for e in _snaps[-1][1]
                    if isinstance(e, dict) and e.get("method")
                    and str(e.get("status") or "") not in ("wontfix",)
                    and (hp._num(e.get("last_seen_ts")) or 0) >= _now - 86400), None)
        if _e0:
            _sig = hp._audit_sig(_e0)
            _add("复发/真实签名见池中", {"kind": hc.KIND_RECURRENCE,
                                    "signature": _sig}, "24h", _now - 60)
    _add("复发/不存在的签名", {"kind": hc.KIND_RECURRENCE,
                            "signature": "no\\such\\file.py::x::y"}, "24h", _now - 60)
    _add("复发/窗口外无审计", {"kind": hc.KIND_RECURRENCE,
                            "signature": "no\\such\\file.py::x::y"}, "24h",
         _now - 40 * 86400)

    # 判据2 效果：真实 verdict 三态各取一条
    for _tag, _key in (("effect/通过", "true"), ("effect/未通过", "false"),
                       ("effect/未判定(None)", "none")):
        if _by[_key]:
            _add(_tag, {"kind": hc.KIND_EFFECT, "patch_id": _by[_key][0]},
                 "24h", _now - 60)

    # 判据3 复用：真实 prompt 里的方法名（应 refuted）+ 假方法名（应 confirmed）
    #          + 空方法名（应 unknown/coarse_signature）
    _rows, _files = hp._trace_rows(_now - 86400, _now)
    _m = ""
    for _r in _rows:
        _txt = str(_r.get("prompt") or "")
        _i = _txt.find("方法名:")
        if _i >= 0:
            _m = _txt[_i + 4:].split("\n")[0].strip()
            if _m:
                break
    if _m:
        _add("复用/真实方法名", {"kind": hc.KIND_REUSE,
                            "signature": f"x.py::t::{_m}"}, "7d", _now - 60)
    _add("复用/假方法名", {"kind": hc.KIND_REUSE,
                        "signature": "x.py::t::NoSuchMethodZZZ"}, "7d", _now - 60)
    _add("复用/无方法名", {"kind": hc.KIND_REUSE,
                        "signature": "x.py::t::"}, "7d", _now - 60)

    _tmp_ledger = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tmp", "m409_probe_ledger.json")
    os.makedirs(os.path.dirname(_tmp_ledger), exist_ok=True)
    with open(_tmp_ledger, "w", encoding="utf-8") as _f:
        json.dump({"version": "335.1", "updated_at": 0.0, "strategies": _strat},
                  _f, ensure_ascii=False, indent=2)
    try:
        _r = hr.reconcile(now=_now, path=_tmp_ledger, dry_run=True)
        _p(f"[对账] now={_now:.0f}  due={_r['due']}  confirmed={_r['confirmed']}  "
           f"refuted={_r['refuted']}  unknown={_r['unknown']}  "
           f"written={_r['written']}（dry_run）")
        _p(f"  {'结论':<10} {'判据':<11} {'卡点':<18} {'样本':<26} 说明")
        for _row in _r.get("rows") or []:
            _p(f"  {_row['result']:<10} {(_row['kind'] or '-'):<11} "
               f"{(_row['reason'] or '-'):<18} {(_row.get('key') or '')[:26]:<26} "
               f"{(_row.get('detail') or '')[:48]}")
    finally:
        try:
            os.remove(_tmp_ledger)
        except OSError as _exc:
            from nucleus._silent_except import silent_exc as _se
            _se(_exc, where="m409_reconcile_run.cmd_probe.cleanup", level="debug")
    return 0


def _dump_rows(rows: list) -> None:
    if not rows:
        _p("  （本轮无到期条目）")
        return
    _p(f"  {'结论':<10} {'判据':<11} {'卡点':<18} key")
    for _r in rows:
        _p(f"  {_r.get('result'):<10} {(_r.get('kind') or '-'):<11} "
           f"{(_r.get('reason') or '-'):<18} {str(_r.get('key'))[:52]}")


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="409刀2 假设对账器命令行")
    _g = _ap.add_mutually_exclusive_group()
    _g.add_argument("--stats", action="store_true", help="只读盘账（默认）")
    _g.add_argument("--run", action="store_true", help="真跑一轮（会写盘）")
    _g.add_argument("--demo", action="store_true", help="合成演示（不碰真实 data/）")
    _g.add_argument("--probe", action="store_true",
                    help="用真实数据跑三判据（dry_run，真实 data/ 零改动）")
    _a = _ap.parse_args(argv)
    if _a.run:
        return cmd_run()
    if _a.demo:
        return cmd_demo()
    if _a.probe:
        return cmd_probe()
    return cmd_stats()


if __name__ == "__main__":
    sys.exit(main())
