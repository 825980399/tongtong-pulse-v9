#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""replay_conditional_writes.py —— 条件触发型写面的**条件解除复测**（★375 刀7）

## 为什么单点实测会得出「已干净」的假结论

`tests/conftest.py` 记了六件「真写 `data/`」的测试（`_M369_DYNAMIC_TARGETS`），
其中 4 件的写面是**条件触发**的：

    `unresolved_samples.dump_samples()` 每次落盘后都调 `_roll()` 滚动清理，
    但 `_roll()` 只在**目录内样本 > _KEEP(7) 份**时才删东西：

        for _old in _fs[:-_KEEP]:      # len(_fs) <= 7 时切片为空 ⇒ 一个都不删
            os.remove(...)

单点实测（目录里只有 1-2 份）看到的是「没删任何文件」⇒ 报「已干净」。
**这是假干净**：不是写面消失了，而是触发条件没满足。要证明写面还在，
必须**成对**复测：条件未满足 ⇒ 不写；条件解除 ⇒ 必写。

## 本件做什么

按 6 件矩阵逐件声明「写口 / 生产目标 / 触发条件 / 沙箱注入点」，并对**可纯函数级
复现**的族做真实复测（全部传 `project_root=tmp`，零污染）：

    unresolved 族（4 件）：预置 6 份 ⇒ dump 后 7 份**不删**（假干净复现）
                           预置 8 份 ⇒ dump 后 9→7 份**删 2**（条件解除）
    artifact  族（2 件）：`PULSE_ISOLATION_ROOT` 声明 ⇒ `guard_artifact_dir` 改道到沙箱
    hub                   ：`VerificationLearningHub(file_path=tmp)` ⇒ 写沙箱

## 用法

    python tools/ci/replay_conditional_writes.py --plan     # 只看矩阵
    python tools/ci/replay_conditional_writes.py --replay    # 实跑（沙箱，零污染）
    python tools/ci/replay_conditional_writes.py --replay --json out.json

★ 本件**不写生产 `data/`**：全部路径由 `project_root`/`file_path`/`PULSE_ISOLATION_ROOT`
显式注入到临时目录。跑完可再核 `data/` 文件数不变。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# 让 `from nucleus...` 在 tools/ci/ 直接运行时也能解析（不依赖 PYTHONPATH）
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 六件写面矩阵。`conditional=True` = 写面在条件满足时才发生（D13 的 4 件）。
#: `sandbox` = 生产侧已留好的注入口（铁律158：不靠改生产代码做隔离）。
WRITE_FACES: tuple[dict, ...] = (
    {
        "file": "test_dialog_evol_m84.py",
        "family": "hub",
        "port": "nucleus.mnemosyne.verification_learning_hub.VerificationLearningHub(file_path=)",
        "target": "data/verification_learning.json",
        "condition": "无条件：每次构造 hub 并按记录数落盘",
        "conditional": False,
        "sandbox": "构造器 file_path 参数",
    },
    {
        "file": "test_dialog_evol_m84.py",
        "family": "artifact",
        "port": "nucleus.domain_guard.guard_artifact_dir()",
        "target": "data/reports/memory/top_types_*.json",
        "condition": "无条件：自观测产物每次产出都写；隔离声明存在时改道",
        "conditional": False,
        "sandbox": "PULSE_ISOLATION_ROOT 环境变量",
    },
    {
        "file": "test_evolution_backup_filter_m60.py",
        "family": "artifact",
        "port": "PatchAutoApprover.DEFAULT_FILE（类属性）",
        "target": "data/metrics/evolution_effectiveness.json",
        # ★实测勘误：票面记它是「条件触发」，实际 `record_round()` 每次调用都走
        #   `_save()`（无 if 分支），属**无条件写**，只是写面可经 `_base_dir` 注入。
        "condition": "无条件：record_round() 每次调用都 _save()",
        "conditional": False,
        "sandbox": "PatchAutoApprover(_base_dir=) 构造参数",
    },
    {
        "file": "test_t101a_low_risk_release.py",
        "family": "unresolved",
        "port": "nucleus.self_awareness.unresolved_samples.dump_samples()",
        "target": "data/call_graph/unresolved_samples_*.jsonl（12MB/份）",
        "condition": "被测系统产出未解析样本时才 dump（条件触发）",
        "conditional": True,
        "sandbox": "dump_samples(project_root=)／_dir_of 注入",
    },
    {
        "file": "test_t105a_release_loop.py",
        "family": "unresolved",
        "port": "nucleus.self_awareness.unresolved_samples.dump_samples()",
        "target": "data/call_graph/unresolved_samples_*.jsonl（12MB/份）",
        "condition": "同上（同一写口，同一条件）",
        "conditional": True,
        "sandbox": "dump_samples(project_root=)／_dir_of 注入",
    },
    {
        "file": "test_tool_wiring_m49.py",
        "family": "unresolved",
        "port": "nucleus.self_awareness.unresolved_samples._roll()",
        "target": "data/call_graph/unresolved_samples_*.jsonl 的滚动删除",
        "condition": "目录内样本 > _KEEP(7) 份才删（★最典型的假干净源）",
        "conditional": True,
        "sandbox": "dump_samples(project_root=)／_dir_of 注入",
    },
    {
        "file": "test_patch_self_apply_m53.py",
        "family": "artifact",
        "port": "data/reports/memory/top_types_*.json 的滚动删除",
        "target": "data/reports/memory/top_types_*.json",
        "condition": "同 _roll 式滚动（>7 份才删）；★破坏性用例",
        "conditional": True,
        "sandbox": "PULSE_ISOLATION_ROOT 环境变量",
    },
)

SCOPE = (
    "条件解除复测：写面函数全部以 project_root/tmp 或 PULSE_ISOLATION_ROOT 显式注入临时目录，"
    "不触生产 data/；「条件未满足不写」与「条件解除必写」成对断言，"
    "单点只测前者会得到假干净（D13）；unresolved 族用预置份数造条件，"
    "artifact/hub 族用隔离声明与构造器参数注入；"
    "全部写入落在显式注入的沙箱目录内，生产 data/ 零变动（可用预置文件名反查）"
)

_KEEP = 7
_PREFIX = "unresolved_samples_"


def conditional_faces() -> list[dict]:
    return [f for f in WRITE_FACES if f["conditional"]]


def _seed(d: str, n: int) -> list[str]:
    """在沙箱目录预置 n 份样本文件，返回文件名（按名字升序 = 时间升序）。"""
    os.makedirs(d, exist_ok=True)
    _names = []
    for _i in range(n):
        _n = f"{_PREFIX}20200101_0000{_i:02d}.jsonl"
        with open(os.path.join(d, _n), "w", encoding="utf-8", newline="\n") as _f:
            _f.write('{"probe": 1}\n')
        _names.append(_n)
    return sorted(_names)


def _count(d: str) -> int:
    try:
        return len([f for f in os.listdir(d) if f.startswith(_PREFIX) and f.endswith(".jsonl")])
    except OSError as _e:
        _log(f"沙箱目录不可读：{d}（{_e}）")
        return -1


def replay_unresolved(tmp_root: str) -> dict:
    """unresolved 族的成对复测（4 件共用同一写口）。

    条件未满足：预置 6 份 ⇒ dump 后 7 份，`_fs[:-7]` 为空 ⇒ **不删**（假干净复现）
    条件解除　：预置 8 份 ⇒ dump 后 9 份，删到 7 份 ⇒ **删 2**
    """
    from nucleus.self_awareness import unresolved_samples as U

    # 必须与 `_dir_of(project_root)` 同构：<project_root>/data/call_graph。
    # 差一层 `data/` 会让 dump 写到别处，读数变成「预置份数恒定」的假结果（实测踩过）。
    _d = os.path.join(tmp_root, "data", "call_graph")
    _r: dict = {"dir": _d}

    # ① 条件未满足
    _seed(_d, 6)
    _before = _count(_d)
    U.dump_samples([{"probe": "below_keep"}], project_root=tmp_root)
    _after = _count(_d)
    _r["below_keep"] = {"before": _before, "after": _after, "deleted": _before + 1 - _after,
                        "expect_deleted": 0}

    # ② 条件解除（清空后重来，避免上面的 7 份干扰）
    for _f in os.listdir(_d):
        os.remove(os.path.join(_d, _f))
    _seed(_d, 8)
    _before2 = _count(_d)
    U.dump_samples([{"probe": "above_keep"}], project_root=tmp_root)
    _after2 = _count(_d)
    _r["above_keep"] = {"before": _before2, "after": _after2,
                        "deleted": _before2 + 1 - _after2, "expect_deleted": _before2 + 1 - _KEEP}
    _r["consistent"] = (_r["below_keep"]["deleted"] == 0
                        and _r["above_keep"]["deleted"] == _r["above_keep"]["expect_deleted"] > 0)
    return _r


def replay_artifact(tmp_root: str) -> dict:
    """artifact 族：隔离声明存在时，产物目录必须改道到隔离根下。"""
    from nucleus import domain_guard as G

    _prod_dir = "data/reports/memory"
    _old = os.environ.get("PULSE_ISOLATION_ROOT")
    os.environ["PULSE_ISOLATION_ROOT"] = tmp_root
    try:
        _actual, _why = G.guard_artifact_dir(_prod_dir)
    finally:
        if _old is None:
            os.environ.pop("PULSE_ISOLATION_ROOT", None)
        else:
            os.environ["PULSE_ISOLATION_ROOT"] = _old
    _decl = G.norm_path(_actual)
    return {
        "declared_dir": _actual,
        "reason": _why,
        "rerouted": not _decl.startswith("data/"),
        "under_tmp": os.path.abspath(_actual).replace("\\", "/").lower().startswith(
            os.path.abspath(tmp_root).replace("\\", "/").lower()),
    }


def replay_hub(tmp_root: str) -> dict:
    """hub 族：构造器给什么路径就写哪，不给就是生产路径。"""
    from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub

    _p = os.path.join(tmp_root, "verification_learning.json")
    _h = VerificationLearningHub(file_path=_p)
    _got = str(getattr(_h, "_file_path", "") or "")
    _got_norm = _got.replace("\\", "/").lower()
    return {
        "ctor_path": _p,
        "hub_path": _got,
        "follows_ctor": _got_norm == _p.replace("\\", "/").lower(),
        "default_is_prod": "data/verification_learning.json" in str(
            VerificationLearningHub.__init__.__defaults__ or ""),
    }


def run_replay(tmp_root: str | None = None) -> dict:
    _tmp = tmp_root or tempfile.mkdtemp(prefix="m375_b7_")
    os.makedirs(_tmp, exist_ok=True)
    _out = {"scope": SCOPE, "tmp_root": _tmp,
            "conditional_count": len(conditional_faces()),
            "faces_total": len(WRITE_FACES)}
    for _name, _fn in (("unresolved", replay_unresolved),
                       ("artifact", replay_artifact), ("hub", replay_hub)):
        try:
            _out[_name] = _fn(_tmp)
            _out[_name]["ok"] = True
        except Exception as _e:  # 复测件要如实报失败，不吞
            _log(f"复测项 {_name} 抛异常：{_e}")
            _out[_name] = {"ok": False, "error": f"{type(_e).__name__}: {_e}"}
    _out["all_ok"] = all(_out[k]["ok"] for k in ("unresolved", "artifact", "hub")) and bool(
        _out.get("unresolved", {}).get("consistent"))
    return _out


def _log(msg: str) -> None:
    """CI 件的最小留痕口（cw2 认 LOG_FUNCS 里的 `_log`；不引入 nucleus 依赖）。"""
    print("[ci-375] " + str(msg), file=sys.stderr)

def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="条件触发型写面的条件解除复测（只写沙箱）")
    _ap.add_argument("--plan", action="store_true", help="只打印六件矩阵")
    _ap.add_argument("--replay", action="store_true", help="实跑复测（沙箱）")
    _ap.add_argument("--json", default=None)
    _a = _ap.parse_args(argv)

    if _a.plan or not _a.replay:
        print(f"[375刀7] 六件写面矩阵（条件触发型 {len(conditional_faces())} 件）")
        for _f in WRITE_FACES:
            _tag = "★条件触发" if _f["conditional"] else "无条件"
            print(f"\n  {_f['file']:<40}{_tag}")
            print(f"    写口  : {_f['port']}")
            print(f"    目标  : {_f['target']}")
            print(f"    条件  : {_f['condition']}")
            print(f"    沙箱  : {_f['sandbox']}")

    if _a.replay:
        _r = run_replay()
        print(f"\n[375刀7] 条件解除复测（沙箱 {_r['tmp_root']}）")
        _u = _r.get("unresolved", {})
        if _u.get("ok"):
            _b, _a2 = _u["below_keep"], _u["above_keep"]
            _okb = "OK" if _b["deleted"] == 0 else "FAIL"
            print(f"  条件未满足：{_b['before']} 份 → dump → {_b['after']} 份，"
                  f"删除 {_b['deleted']}（期望 0）{_okb}")
            _oka = "OK" if _a2["deleted"] == _a2["expect_deleted"] else "FAIL"
            print(f"  条件解除　：{_a2['before']} 份 → dump → {_a2['after']} 份，"
                  f"删除 {_a2['deleted']}（期望 {_a2['expect_deleted']}）{_oka}")
        else:
            print(f"  unresolved: FAIL {_u.get('error')}")
        _ar = _r.get("artifact", {})
        print(f"  artifact  ：改道={_ar.get('rerouted')} "
              f"落在沙箱={_ar.get('under_tmp')}（{_ar.get('declared_dir', '')}）")
        _hb = _r.get("hub", {})
        print(f"  hub       ：跟随构造器路径={_hb.get('follows_ctor')} "
              f"（默认值含生产路径={_hb.get('default_is_prod')}）")
        print("  结论：%s" % ("条件解除后写面确实发生" if _r["all_ok"] else "有复测项未通过"))
        if _a.json:
            with open(_a.json, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_r, _f, ensure_ascii=False, indent=1)
            print(f"  已写出：{_a.json}")
        return 0 if _r["all_ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
