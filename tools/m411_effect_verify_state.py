# -*- coding: utf-8 -*-
"""411 刀2 工具：效果验证状态派生 / 新鲜度 / 链路探针。

用法::

    python tools/m411_effect_verify_state.py --stats    # 只看（默认）
    python tools/m411_effect_verify_state.py --stale    # 只看新鲜度
    python tools/m411_effect_verify_state.py --apply    # 给 patch_history 补派生字段
    python tools/m411_effect_verify_state.py --probe    # 隔离根验证 verify→落盘 链路

`--apply` 默认 **dry-run**，必须显式 `--apply --write` 才落盘。
所有写入都走 `os.replace` 原子替换；`--probe` 只用 `tmp/` 隔离目录，**不碰生产 data/**。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from nucleus.evolution import effect_verify_health as evh  # noqa: E402


def _print_stats() -> int:
    _s = evh.summarize_history()
    _v = evh.load_verdicts()
    print("=" * 78)
    print("[效果验证台账] %s" % evh.history_path())
    print("  历史总条数      : %d" % _s["total"])
    print("  applied=True    : %d" % _s["applied"])
    print("  派生状态分布    : %s" % json.dumps(_s["states"], ensure_ascii=False))
    print("  verdicts 条数   : %d" % _s["verdicts_total"])
    print("  最近一次 apply  : %s" % (_s["last_applied_at"] or "**从未**"))
    print("  说明            : %s" % _s["note"])
    print("=" * 78)
    _t = evh.stale_info()
    print("[新鲜度] state=%s age_sec=%s stale=%s updated=%s" % (
        _t["state"], _t["age_sec"], _t["stale"], _t["updated"]))
    print("[快照块] %s" % json.dumps(evh.snapshot_block(), ensure_ascii=False))
    # 关键取证：passed 占比与 missing 的成因
    _passed = _s["states"].get("passed", 0)
    if _s["applied"]:
        print("[读数] applied 中已验占比 = %d/%d = %.1f%%" % (
            _passed, _s["applied"], _passed * 100.0 / _s["applied"]))
    return 0


def _print_stale() -> int:
    print(json.dumps(evh.stale_info(), ensure_ascii=False, indent=1))
    return 0


def _apply(write: bool) -> int:
    _r = evh.annotate(dry_run=not write)
    print(json.dumps(_r, ensure_ascii=False, indent=1))
    if not write:
        print("\n★dry-run：未落盘。要写入请加 --write")
    return 0 if _r.get("ok") else 1


def _probe() -> int:
    """隔离根验证 `EvolutionEffectVerifier.verify() → effect_verify.json` 链路。

    为什么用探针而不是"手动 apply 一次"：
      T0 实测 10-31 的三次 apply 全部死在 **stage 2a 入库前静态闸**
      （`audit/failed_patches/20261031_*.json` 里 reason=`try 块不平衡`），
      补丁根本没到 apply ⇒ 无法用"真 apply"证明下游链路。
      探针直接对 verifier 下单，证明的是**下游这一段是通的**，
      把"断在上游"这个结论钉死。
    """
    _iso = os.path.join(_ROOT, "tmp", "m411_probe_effect_verify")
    if os.path.isdir(_iso):
        shutil.rmtree(_iso, ignore_errors=True)
    os.makedirs(_iso, exist_ok=True)
    _base = os.path.join(_iso, "evolution")
    os.makedirs(_base, exist_ok=True)

    from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier

    _fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  [PASS] " if cond else "  [FAIL] ") + msg)
        if not cond:
            _fails.append(msg)

    print("[探针] 隔离根: %s" % _iso)
    _v = EvolutionEffectVerifier(base_dir=_base)
    _patch = {"id": "m411_probe_1", "file": "a.py", "method": "f",
              "issue_type": "log", "modified_code": "print(1)\n", "applied": True}
    _issue = {"type": "log", "file": "a.py", "method": "f"}
    _t0 = time.time()
    _r1 = _v.verify(_patch, _issue, problem_gone=True, function_ok=True,
                    perf_delta_ms=None)
    _p = os.path.join(_base, "effect_verify.json")
    check(os.path.exists(_p), "verify 后产物已落盘")
    _d1 = json.loads(io.open(_p, encoding="utf-8").read()) if os.path.exists(_p) else {}
    check(isinstance(_d1.get("verdicts"), dict) and "m411_probe_1" in _d1["verdicts"],
          "verdicts 里出现本次 patch_id")
    # ★411 实测语义（与本批最重要的一条发现相关）：
    #   `verify()` 判 passed=True 要求 **三维全知**（problem_gone/function_ok/perf_ok），
    #   而 `PatchManager` 调用处固定传 `perf_delta_ms=None` ⇒ 新裁决**永远只能是 None**。
    #   所以这里钉的是真语义："两维已知 + 一维缺 ⇒ 覆盖不足，不判 passing"。
    check(_r1.get("passed") is None,
          "两维已知+perf 未测 ⇒ passed=None（覆盖不足，不判通过）")
    check("覆盖不足" in str(_r1.get("reason") or "") or "缺" in str(_r1.get("reason") or ""),
          "reason 如实写明缺口（%s）" % str(_r1.get("reason") or "")[:40])
    _upd1 = float(_d1.get("updated") or 0)
    check(_upd1 >= _t0 - 1.0, "updated 已刷新到本次时刻")

    # 三维全知 ⇒ 才可能 passed=True（对照组，证明判据不是恒 None）
    _patch3 = {"id": "m411_probe_3", "file": "a.py", "method": "h",
               "issue_type": "log", "modified_code": "print(3)\n", "applied": True}
    _r3 = _v.verify(_patch3, _issue, problem_gone=True, function_ok=True,
                    perf_delta_ms=5.0)
    check(_r3.get("passed") is True, "三维全知且均通过 ⇒ passed=True（对照组）")

    # 第二条：判不了 ⇒ passed 不是 True（第三态必须是一等公民）
    _patch2 = {"id": "m411_probe_2", "file": "a.py", "method": "g",
               "issue_type": "log", "modified_code": "print(2)\n", "applied": True}
    _r2 = _v.verify(_patch2, _issue, problem_gone=None, function_ok=None,
                    perf_delta_ms=None)
    check(_r2.get("passed") is not True, "problem_gone/function_ok 都 None ⇒ 不算 passed")
    _d2 = json.loads(io.open(_p, encoding="utf-8").read())
    _upd2 = float(_d2.get("updated") or 0)
    check(_upd2 >= _upd1, "后续 verify 后 updated 不回退")
    check(len(_d2.get("verdicts") or {}) >= 3, "verdicts 累计增加")

    # 派生状态反查（用隔离台账）
    _vd = evh.load_verdicts(_p)
    _s1, _why1 = evh.derive_state(_patch, _vd)
    check(_s1 == evh.STATE_UNVERIFIABLE,
          "derive_state(probe_1)=unverifiable（实测 %s，reason=%s）" % (_s1, _why1))
    _s3v, _why3 = evh.derive_state(_patch3, _vd)
    check(_s3v == evh.STATE_PASSED,
          "derive_state(probe_3)=passed（实测 %s，reason=%s）" % (_s3v, _why3))
    _s2, _why2 = evh.derive_state(_patch2, _vd)
    check(_s2 == evh.STATE_UNVERIFIABLE,
          "derive_state(probe_2)=unverifiable（实测 %s）" % _s2)
    _s4, _ = evh.derive_state({"id": "nope", "applied": False}, _vd)
    check(_s4 == evh.STATE_MISSING, "未 apply 且无 verdict ⇒ missing（实测 %s）" % _s4)

    # 新鲜度判据（同一份隔离台账，注入时钟）
    _st = evh.stale_info(path=_p, now=time.time() + 100000)
    check(_st["stale"] is True, "注入 +100000s ⇒ stale=True")
    _st2 = evh.stale_info(path=_p, now=time.time())
    check(_st2["stale"] is False, "当前时刻 ⇒ stale=False")

    print("\n[结论] %s（PASS %d / FAIL %d）" % (
        "PASS" if not _fails else "FAIL", 13 - len(_fails), len(_fails)))
    return 0 if not _fails else 1


def main() -> int:
    _ap = argparse.ArgumentParser(description="411 刀2 效果验证状态工具")
    _ap.add_argument("--stats", action="store_true", help="看台账与派生状态分布")
    _ap.add_argument("--stale", action="store_true", help="只看新鲜度")
    _ap.add_argument("--apply", action="store_true", help="给 patch_history 补派生字段（默认 dry-run）")
    _ap.add_argument("--write", action="store_true", help="与 --apply 同用才真正落盘")
    _ap.add_argument("--probe", action="store_true", help="隔离根验证 verify→落盘 链路")
    _args = _ap.parse_args()
    if _args.probe:
        return _probe()
    if _args.apply:
        return _apply(_args.write)
    if _args.stale:
        return _print_stale()
    return _print_stats()


if __name__ == "__main__":
    raise SystemExit(main())
