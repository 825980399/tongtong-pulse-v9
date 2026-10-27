# -*- coding: utf-8 -*-
"""374 刀3 单测：K1 productive 产出卸载回主闭环（审计池）。

票 `T-K1主闭环卸载-1`。

验四件事：
① 转换层：字段映射 / 不可定位丢弃 / 日志级 severity → 池级 severity；
② 卸载层：真的落进池快照、幂等、空 report 不写空快照；
③ 语义边界：**进 optimize_list 不进 patch_pool**（评审记录不许冒充可修缺陷）；
④ 接线：子进程 review 拍 productive 时真的会调（否则就是死代码）。
"""
import os

import pytest

import nucleus.audit_problem_pool as pool
import nucleus.evolution.k1_offload as k1o

_SRC = os.path.join("nucleus", "reasoning", "SafeEvolutionExecutor.py")


@pytest.fixture(autouse=True)
def _reset():
    k1o.reset_offload_throttle()
    yield
    k1o.reset_offload_throttle()


def _item(**over):
    d = {"pulse_type": "ExceptionPulse", "file": "organs/body/PulseHeart.py",
         "method": "_trigger_heartbeat", "severity": "ERROR", "snippet_len": 42}
    d.update(over)
    return d


# ---------------------------------------------------------------- ① 转换层
def test_01_severity_mapped_to_pool_vocabulary():
    """日志级 severity 必须显式映射（池里 ERROR 是**非法值**，会进清洗队列）。"""
    _is = k1o.report_to_issues([_item(severity="ERROR"), _item(severity="WARN", method="m2"),
                                _item(severity="INFO", method="m3")])
    assert [x["severity"] for x in _is] == ["high", "medium", "low"]
    for _x in _is:
        assert pool.normalize_severity(_x["severity"])[1] is True, "映射后仍是非法 severity"


def test_02_unlocatable_dropped_not_merged():
    """既无 file 又无 method ⇒ 丢弃。留着会把 N 个问题合并成 1 条假条目。"""
    _is = k1o.report_to_issues([_item(file="", method=""), _item(method="ok_m")])
    assert len(_is) == 1 and _is[0]["method"] == "ok_m"


def test_03_attribution_kept():
    _is = k1o.report_to_issues([_item(ts="2026-10-27 10:00:00")])
    assert _is[0]["type"] == k1o.K1_ISSUE_TYPE
    assert _is[0]["attribution"]["source"] == "k1_task_face_review"
    assert _is[0]["attribution"]["pulse_type"] == "ExceptionPulse"


# ---------------------------------------------------------------- ② 卸载层
def test_04_lands_in_pool_snapshot(tmp_path):
    """真落盘：池快照里能读到这条（含 dedup_key / status=open）。"""
    r = k1o.offload_report([_item()], root=str(tmp_path))
    assert r["state"] == "ok", r
    assert r["added"] == 1 and r["offloaded"] == 1

    _load = pool.load_pool(str(tmp_path))
    assert _load["state"] == "ok", _load
    _e = [x for x in _load["entries"] if x.get("method") == "_trigger_heartbeat"]
    assert len(_e) == 1, f"池里没有该条目：{[x.get('method') for x in _load['entries']]}"
    assert _e[0]["status"] == pool.STATUS_OPEN
    assert _e[0]["dedup_key"] == pool.make_dedup_key(
        "organs/body/PulseHeart.py", "_trigger_heartbeat", k1o.K1_ISSUE_TYPE)


def test_05_idempotent_second_call_updates_only(tmp_path):
    k1o.offload_report([_item()], root=str(tmp_path), force=True)
    r2 = k1o.offload_report([_item()], root=str(tmp_path), now=None, force=True)
    assert r2["state"] == "ok"
    assert r2["added"] == 0, "同一条 anomaly 重复卸载不该新增条目"
    assert r2["updated"] >= 1
    assert len([x for x in pool.load_pool(str(tmp_path))["entries"]
                if x.get("method") == "_trigger_heartbeat"]) == 1


def test_06_empty_report_writes_nothing(tmp_path):
    r = k1o.offload_report([], root=str(tmp_path))
    assert r["state"] == "skipped" and r["reason"] == "empty"
    assert not os.path.isdir(pool.audit_dir(str(tmp_path))), "空 report 不得写空快照"


def test_07_throttled_within_window(tmp_path):
    k1o.offload_report([_item()], root=str(tmp_path))          # 第一次（记时间戳）
    r2 = k1o.offload_report([_item(method="other")], root=str(tmp_path))
    assert r2["state"] == "skipped" and r2["reason"] == "throttled"
    _m = [x.get("method") for x in pool.load_pool(str(tmp_path))["entries"]]
    assert "other" not in _m, "节流窗口内不得落盘"


def test_08_dry_run_does_not_write(tmp_path):
    r = k1o.offload_report([_item()], root=str(tmp_path), dry_run=True)
    assert r["reason"] == "dry_run" and r["issues"] == 1
    assert not os.path.isdir(pool.audit_dir(str(tmp_path)))


def test_09_goes_through_single_pool_writer(tmp_path, monkeypatch):
    """不许绕开唯一落盘口：必须调 `persist_pool` 且只调一次。"""
    _calls = []
    _orig = pool.persist_pool

    def _spy(report, **kw):
        _calls.append(kw)
        return _orig(report, **kw)

    monkeypatch.setattr(pool, "persist_pool", _spy)
    k1o.offload_report([_item()], root=str(tmp_path))
    assert len(_calls) == 1, f"应恰好走一次 persist_pool，实得 {len(_calls)}"


# ---------------------------------------------------------------- ③ 语义边界
def test_10_routes_to_optimize_list_not_patch_pool():
    """★K1 产出是「评审记录」，不是「可修缺陷」⇒ 只能进看板侧。"""
    _route = pool.classify_route(k1o.K1_ISSUE_TYPE)
    assert _route == pool.ROUTE_OPTIMIZE_LIST, f"实得 {_route}"
    assert pool.classify_klass(k1o.K1_ISSUE_TYPE) == pool.KLASS_UNKNOWN


def test_11_k1_criteria_untouched():
    """卸载不改 K1 判据本体（20 拍 / ≥15 产出 / 三列同报）。"""
    import nucleus.reasoning.SafeEvolutionExecutor as E

    assert E._K1_MIN_RUNS == 20 and E._K1_MIN_PRODUCTIVE == 15


# ---------------------------------------------------------------- ④ 接线
def test_12_wired_into_review_branch():
    """死代码检查：子进程 review 拍拿到 report 后必须有一处卸载调用。"""
    with open(_SRC, encoding="utf-8") as _f:
        src = _f.read()
    _i = src.index('mode == "review"')
    _seg = src[_i:_i + 6000]
    assert "offload_report(" in _seg, "review 拍未接线卸载 ⇒ 本刀等于没做"
    assert "if _productive:" in _seg, "只应对 productive 拍卸载（零产出没有东西可卸）"
    # 卸载必须在计数之后（否则算数与卸载两套口径会打架）
    assert _seg.index('_OUTPUT_STATS["runs"] += 1') < _seg.index("offload_report(")


def test_13_offload_failure_does_not_break_beat():
    """卸载抛异常不得冒泡（观测不得阻断主流程）。"""
    r = k1o.offload_report([_item()], root=None, audit_dir_path="\0bad\x00path")
    assert r["state"] in ("error", "skipped"), r
