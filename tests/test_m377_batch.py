# -*- coding: utf-8 -*-
"""★377批刀1：O3 自动派单准入字段回填工具（`tools/backfill_o3_dispatch_ready.py`）。

被测面（全部 tmp 隔离，**不写生产 `data/`**）：
  · 默认 dry-run 绝不写盘；
  · `--apply` 后**两个**字段齐（`o3_auto_dispatch_ready=True` + `requires_confirmation=False`），
    且除目标条目外**逐条字节不变**（"只补这一条"可验）；
  · 二次 apply 幂等（`already`，不再写盘）；
  · 反向：只补一个字段仍过不了 `_m339_o3_dispatch_ready`（两个方向各一条）；
  · 生产路径需 `--allow-prod` 双重显式；非 O3 / 非 pending / 已 applied 一律 skipped 带原因；
  · 行尾守恒（CRLF 入 ⇒ CRLF 出；LF 入 ⇒ LF 出，铁律138/213）。

★本批 T0 勘误（票面与实测不符，测试按实测钉）：
  任务书只说「补 `o3_auto_dispatch_ready`」，实测闸是**双条件**；票面点名的参照条
  `o3_59cd0eef8e125186` **自己也没有**该字段 ⇒ 参照不了，取值只认判据函数。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import backfill_o3_dispatch_ready as BF

TARGET_ID = "o3_b31b31fee790d6e9"


def _pool(target=None):
    """最小池：1 条 O3 观察位（缺字段）+ 2 条无关条目。"""
    _t = target if target is not None else {
        "id": TARGET_ID,
        "patch_id": TARGET_ID,
        "file": "nucleus/audit_problem_pool.py",
        "method": "restore_pool",
        "issue_type": "dead_code_candidate",
        "source": "o3_open_gate",
        "o3_dead_code": True,
        "status": "pending",
        "requires_confirmation": True,
        "evidence_tier": "hard_strict",
        "dup_group": "excluded_o3",
        "trust_score": 0,
    }
    return [_t,
            {"id": "patch_llm_a", "file": "x.py", "method": "m1",
             "source": "llm", "status": "runtime_verified", "risk_level": "低"},
            {"id": "patch_llm_b", "file": "y.py", "method": "m2",
             "source": "llm", "status": "runtime_verified", "risk_level": "低"}]


def _write(path, items, crlf=True):
    _s = json.dumps(items, ensure_ascii=False, indent=2)
    if crlf:
        _s = _s.replace("\r\n", "\n").replace("\n", "\r\n")
    _raw = _s.encode("utf-8")
    with open(path, "wb") as _f:
        _f.write(_raw)
    return _raw


def _read(path):
    with open(path, "rb") as _f:
        return _f.read()


def _run(path, tmp_path, **kw):
    kw.setdefault("archive_root", str(tmp_path / "_archive"))
    kw.setdefault("patch_ids", (TARGET_ID,))
    return BF.build_report(str(path), **kw)


# ---------------------------------------------------------------- 正向：dry-run
def test_m377_d1_dry_run_never_writes(tmp_path):
    _p = tmp_path / "pending_patches.json"
    _before = _write(str(_p), _pool())
    _r = _run(_p, tmp_path)
    assert _read(str(_p)) == _before, "dry-run 必须逐字节不动"
    assert _r["dry_run"] is True and _r["applied"] is False
    _row = _r["rows"][0]
    assert _row["state"] == "filled"
    assert _row["gate_before"] == {"ok": False, "why": "not_confirmed"}
    # dry-run 也要给出写后读数（在副本上预演，不动真身）
    assert _row["gate_after"] == {"ok": True, "why": "ok"}


# ---------------------------------------------------------------- 正向：apply
def test_m377_d1_apply_fills_both_fields_and_touches_nothing_else(tmp_path):
    _p = tmp_path / "pending_patches.json"
    _write(str(_p), _pool())
    _r = _run(_p, tmp_path, apply=True, allow_prod=False,
              prod_check=lambda _x: False)
    assert _r["applied"] is True, _r.get("error")
    _items = json.loads(_read(str(_p)).decode("utf-8"))
    _t = _items[0]
    assert _t["o3_auto_dispatch_ready"] is True
    assert _t["requires_confirmation"] is False
    assert _t[BF.FILL_MARK] == "backfill_377_d1"
    # 只补这一条：其余条目内容不变
    assert _r["untouched_ok"] is True and _r["untouched_leaked"] == []
    assert _r["changed_ids"] == [TARGET_ID]
    # 写后重读盘的闸读数（不是内存态自称）
    assert _r["rows"][0]["gate_persisted"] == {"ok": True, "why": "ok"}
    # 不手动 set_status
    assert _t["status"] == "pending"


def test_m377_d1_apply_keeps_crlf_and_lf(tmp_path):
    for _crlf in (True, False):
        _p = tmp_path / ("crlf.json" if _crlf else "lf.json")
        _write(str(_p), _pool(), crlf=_crlf)
        _r = _run(_p, tmp_path, apply=True, prod_check=lambda _x: False)
        assert _r["crlf"] is _crlf
        _raw = _read(str(_p))
        assert (_raw.count(b"\r\n") > 0) is _crlf, "行尾必须守恒（铁律138）"
        # 目标条目字段真的进去了
        assert b"o3_auto_dispatch_ready" in _raw


# ---------------------------------------------------------------- 幂等
def test_m377_d1_second_apply_is_already(tmp_path):
    _p = tmp_path / "pending_patches.json"
    _write(str(_p), _pool())
    _run(_p, tmp_path, apply=True, prod_check=lambda _x: False)
    _mid = _read(str(_p))
    _r2 = _run(_p, tmp_path, apply=True, prod_check=lambda _x: False)
    assert _r2["rows"][0]["state"] == "already"
    assert _r2["applied"] is False and "note" in _r2
    assert _read(str(_p)) == _mid, "幂等：第二次不得再写盘"


# ---------------------------------------------------------------- 反向：双条件缺一不可
@pytest.mark.parametrize("only_ready,only_confirm,why", [
    (True, None, "still_requires_confirmation"),   # 只补 ready ⇒ 仍卡确认闸
    (None, False, "not_confirmed"),                # 只放开确认 ⇒ 仍卡 ready 闸
    (None, None, "not_confirmed"),                 # 两个都没
])
def test_m377_d1_one_field_is_not_enough(tmp_path, only_ready, only_confirm, why):
    """★反向验证：证明「只补票面那一个字段」不足以过门 —— 这就是本批补两个的理由。"""
    _gate, _srcs, _types = BF._gate()
    _t = {"id": TARGET_ID, "o3_dead_code": True, "source": "o3_open_gate",
          "issue_type": "dead_code_candidate", "status": "pending"}
    if only_ready is not None:
        _t["o3_auto_dispatch_ready"] = only_ready
    if only_confirm is not None:
        _t["requires_confirmation"] = only_confirm
    _ok, _w = _gate(_t)
    assert _ok is False and _w == why
    # 工具的"已完成"判定必须与闸一致：缺任一 ⇒ 不算 already，仍要补
    assert BF._already(_t) is False
    _both = dict(_t, o3_auto_dispatch_ready=True, requires_confirmation=False)
    assert _gate(_both)[0] is True and BF._already(_both) is True


# ---------------------------------------------------------------- 生产路径守卫
def test_m377_d1_prod_path_needs_allow_prod(tmp_path):
    _p = tmp_path / "pending_patches.json"
    _before = _write(str(_p), _pool())
    _r = _run(_p, tmp_path, apply=True, allow_prod=False,
              prod_check=lambda _x: True)
    assert _r["applied"] is False
    assert "--allow-prod" in str(_r.get("error") or "")
    assert _read(str(_p)) == _before, "未授权时逐字节不动"
    # 授权后才写
    _r2 = _run(_p, tmp_path, apply=True, allow_prod=True,
               prod_check=lambda _x: True)
    assert _r2["applied"] is True


# ---------------------------------------------------------------- skipped 三态
@pytest.mark.parametrize("item,reason", [
    ({"id": "p1", "source": "llm", "status": "pending"}, "not_o3"),
    ({"id": "p2", "o3_dead_code": True, "status": "runtime_verified"},
     "status_not_pending"),
    ({"id": "p3", "o3_dead_code": True, "status": "pending", "applied": True},
     "applied"),
    ({"id": "p4", "o3_dead_code": True, "status": "pending",
      "pending_cleared": True}, "pending_cleared"),
])
def test_m377_d1_skip_reasons(tmp_path, item, reason):
    _p = tmp_path / "pending_patches.json"
    _items = _pool(target=dict(item))
    _before = _write(str(_p), _items)
    _r = BF.build_report(str(_p), (item["id"],), apply=True,
                         allow_prod=True, prod_check=lambda _x: False,
                         archive_root=str(tmp_path / "_archive"))
    assert _r["rows"][0]["state"] == "skipped"
    assert _r["rows"][0]["reason"] == reason
    assert _read(str(_p)) == _before


def test_m377_d1_missing_id_is_skipped(tmp_path):
    _p = tmp_path / "pending_patches.json"
    _before = _write(str(_p), _pool())
    _r = _run(_p, tmp_path, apply=True, allow_prod=True,
              patch_ids=("o3_not_exists",))
    assert _r["rows"][0] == {"patch_id": "o3_not_exists", "state": "skipped",
                             "reason": "not_in_pool", "gate_before": None,
                             "gate_after": None}
    assert _read(str(_p)) == _before


# ---------------------------------------------------------------- 默认观察位对齐
def test_m377_d1_default_ids_follow_zero_break_watch_slot():
    """默认只动 351 刀3 起的破零观察位；观察位换人时本工具必须同步（否则补错条目）。"""
    from nucleus.evolution.zero_break_judge import WATCH_IDS
    # ★385刀1：`WATCH_IDS` 从**单值**扩成多值元组（原观察位被 384刀2 清零删掉后
    #   破零静默失明，故改成"种子多值 + 自愈补位"，第 3 个由自愈从 pending 挑）。
    #   ⇒ 本工具的默认名单只需**跟随**观察位集合，不必与之全等；硬钉"全等且只有 1 个"
    #   会在每次扩容时假红（383刀4 已经在流转表守卫上栽过同一类跟法）。
    assert set(BF.DEFAULT_PATCH_IDS) <= set(WATCH_IDS), (
        "377 回填工具的默认名单必须落在观察位集合内 —— 观察位换人时本工具要同步")
    assert TARGET_ID in WATCH_IDS, "扩容不该把 351 起的原观察位丢掉"


# ================================================================ 刀2：G4 线提额
def test_m377_d2_g4_line_raised_to_7000():
    """票面口径：`G4_HEAP_MB` 2000→7000（真键名带 MEMORY_RELEASE_ 前缀）。"""
    import config
    from nucleus.mnemosyne import release_policy as rp
    assert float(config.MEMORY_RELEASE_G4_HEAP_MB) == 7000.0
    assert rp.thresholds()["g4_heap_mb"] == 7000.0
    # 三档顺序未被破坏（提额只动 G4，不许把 G2/G3 顶乱）
    _th = rp.thresholds()
    assert _th["g2_heap_mb"] < _th["g3_heap_mb"] < _th["g4_heap_mb"]


def test_m377_d2_tier_regains_resolution():
    """反向·核心：旧线下活体读数 4.9GB **恒落 G4**（实测 215 拍全落），提额后须落 G3。

    这条是本刀唯一的实质收益证据——不是"数字变大"，是判据从一档恢复成四档。
    """
    from nucleus.mnemosyne import release_policy as rp
    _th = rp.thresholds()
    _live = 4944.0                      # 2026-10-28 12:03:49 活体实测 heap
    assert _live < _th["g4_heap_mb"] and _live >= _th["g3_heap_mb"]
    _tier, _why = rp.pick_tier(_live)
    assert _tier == rp.TIER_G3, _why
    assert _tier != rp.TIER_G4, "提额后仍落 G4 ⇒ 本刀无效"
    # 四档边界都要能分出来（含提额后的新 G4 边界）
    assert rp.pick_tier(7100.0)[0] == rp.TIER_G4
    assert rp.pick_tier(1800.0)[0] == rp.TIER_G3
    assert rp.pick_tier(1300.0)[0] == rp.TIER_G2
    assert rp.pick_tier(800.0)[0] == rp.TIER_G1
    # 测不到 heap ⇒ 不释放（语义不因提额而变化）
    assert rp.pick_tier(None)[0] == rp.TIER_G1


def test_m377_d2_acceptance_line_coupling_is_explicit():
    """★377刀2 立的口子，**378刀4 已补上** —— 本用例随契约反向同步（强度不降）。

    377刀2 把 G4 线提到 7000 时，两个验收辅助函数的**默认线**跟着走 7000，
    当时只能靠注释+单测要求调用方显式传 2000（"提醒"不是"装置"）。
    378刀4 新增 `MEMORY_PEAK_ACCEPTANCE_MB=2000` 解耦 ⇒ 现在默认即 D3 硬线，
    要放宽必须**显式**传 7000。
    """
    from nucleus.mnemosyne import release_policy as rp
    _d = rp.heap_peak_guard(5000.0)
    assert _d["line_mb"] == 2000.0 and _d["verdict"] == "fail"
    assert rp.heap_peak_guard(5000.0, line_mb=7000.0)["verdict"] == "pass"
    assert rp.heap_peak_guard(1800.0)["verdict"] == "pass"
    # 判定线双写：heap 轨默认同样取验收键（不再跟 G4 分档键）
    _p = rp.judge_pass_line(8000.0, 5000.0)
    assert _p["heap_line_mb"] == 2000.0
    assert _p["verdict"] != "pass"
    assert rp.judge_pass_line(8000.0, 5000.0,
                              heap_line_mb=7000.0)["verdict"] == "pass"


def test_m377_d2_fallback_stays_conservative():
    """`release_policy` 里的 fallback 2000 **不是漏改**：config 读不到时退回旧保守线。"""
    import inspect
    from nucleus.mnemosyne import release_policy as rp
    _src = inspect.getsource(rp.thresholds)
    assert '_cfg("MEMORY_RELEASE_G4_HEAP_MB", 2000.0)' in _src, \
        "fallback 被改动 ⇒ 须同步 config.py 注释里的口径说明"


# ================================================================ 刀3：失败补丁正文留档
_UNBALANCED = "def f():\n    try:\n        x = 1\n    y = 2\n"
_ORPHAN = "x = 1\nexcept Exception:\n    pass\n"
_BALANCED = ("def f():\n    try:\n        x = 1\n"
             "    except Exception:\n        x = 2\n    return x\n")


def _mk_manager(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager
    return PatchManager(str(tmp_path))


def _patch_with(code, _id="patch_multi_1793000000_abcd"):
    return {"id": _id, "file": "organs/body/Demo.py", "method": "demo",
            "issue_type": "dead_code_candidate", "source": "llm_multi_file",
            "risk_level": "中", "dedup_key": "deadbeefdeadbeef",
            "original_code": "def demo():\n    x = 9\n    return x\n",
            "modified_code": code}


def _dump_dir(tmp_path):
    from nucleus.reasoning import PatchManager as PM
    return PM._m377_failed_patch_dir(str(tmp_path))


def _dump_files(tmp_path):
    _d = _dump_dir(tmp_path)
    if not os.path.isdir(_d):
        return []
    return sorted(f for f in os.listdir(_d) if f.endswith(".json"))


@pytest.mark.parametrize("code,state", [(_UNBALANCED, "unbalanced"),
                                        (_ORPHAN, "orphan")])
def test_m377_d3_rejected_patch_body_is_archived(tmp_path, code, state):
    """关2a 拒收 ⇒ 正文落独立审计件，带 issue id ＋ 拒收原因 ＋ try 统计。"""
    pm = _mk_manager(tmp_path)
    _r = pm._check_llm_patch_completeness(_patch_with(code))
    assert _r["complete"] is False and "try 块不平衡" in _r["reason"]
    _files = _dump_files(tmp_path)
    assert len(_files) == 1, "拒收但没留档（正是 376 刀4 抱怨的『data/ 全库查不到』）"
    _rec = json.loads((tmp_path / "data" / "audit" / "failed_patches"
                       / _files[0]).read_text(encoding="utf-8"))
    assert _rec["schema"] == "failed_patch/377.v1"
    assert _rec["issue_id"] == "patch_multi_1793000000_abcd"
    assert _rec["dedup_key"] == "deadbeefdeadbeef"
    assert _rec["stage"] == "2a"
    assert _rec["reason"] == _r["reason"], "留档原因必须与返回给调用方的一字不差"
    assert _rec["modified_code"] == code, "正文必须原样落盘（后缀若被裁就会再次无法复盘）"
    assert _rec["try_balance"]["state"] == state
    assert _rec["file"] == "organs/body/Demo.py" and _rec["method"] == "demo"


def test_m377_d3_balanced_patch_writes_nothing(tmp_path):
    """零回归：通过关2a 的补丁不得产出审计件（否则目录会被正常件淹没）。"""
    pm = _mk_manager(tmp_path)
    _r = pm._check_llm_patch_completeness(_patch_with(_BALANCED))
    assert _r.get("complete") is not False, _r.get("reason")
    assert _dump_files(tmp_path) == []


def test_m377_d3_verdict_unchanged_when_dump_fails(tmp_path, monkeypatch):
    """★反向验证：留档失败**不得**改变拒收结论（三态：拒收 / 未留档 / 原因不变）。"""
    from nucleus.reasoning import PatchManager as PM
    pm = _mk_manager(tmp_path)
    _base = pm._check_llm_patch_completeness(_patch_with(_UNBALANCED))
    _n_before = len(_dump_files(tmp_path))
    assert _n_before == 1                      # 基线确实留了档
    monkeypatch.setattr(PM, "_m377_dump_failed_patch",
                        lambda *a, **kw: {"written": False, "why": "boom"})
    _r = pm._check_llm_patch_completeness(_patch_with(_UNBALANCED))
    assert _r == _base, "留档失败泄漏进了裁决分支"
    assert _r["complete"] is False
    assert len(_dump_files(tmp_path)) == _n_before, "留档失败却仍写盘"


def test_m377_d3_dump_never_raises(tmp_path):
    """★反向验证：helper 对任何入参都不得抛（它在拒收路径上，抛=拒收路径被劫持）。"""
    from nucleus.reasoning import PatchManager as PM
    for _bad in (None, "not-a-dict", 123, [1, 2]):
        _r = PM._m377_dump_failed_patch(_bad, "why-not", root=str(tmp_path))
        assert isinstance(_r, dict) and _r["written"] is False
        assert _r["why"] == "not_dict"
    # 空 dict 也只在注入根下落盘（默认根是生产仓库，测试绝不碰）
    _r = PM._m377_dump_failed_patch({}, "why-not", root=str(tmp_path))
    assert _r["written"] is True            # 空正文也能留档（正是「零留存」要修的场景）
    # 目录口径
    assert PM._m377_failed_patch_dir("/tmp/x").replace("\\", "/").endswith(
        "/data/audit/failed_patches")
    # 默认根必须指向本仓库（不依赖 cwd）
    _d = PM._m377_failed_patch_dir().replace("\\", "/")
    assert _d.endswith("/data/audit/failed_patches")
    assert os.path.abspath(_d).replace("\\", "/").startswith(
        os.path.abspath(ROOT).replace("\\", "/"))


def test_m377_d3_dir_cap_returns_dir_full(tmp_path, monkeypatch):
    """封顶：目录满后停写盘但**明确报 dir_full**（不是静默丢弃）。"""
    from nucleus.reasoning import PatchManager as PM
    monkeypatch.setattr(PM, "_M377_FAILED_PATCH_MAX", 1)
    _p = _patch_with(_UNBALANCED, _id="p_cap_1")
    _r1 = PM._m377_dump_failed_patch(_p, "r1", root=str(tmp_path))
    assert _r1["written"] is True
    _r2 = PM._m377_dump_failed_patch(_patch_with(_UNBALANCED, _id="p_cap_2"),
                                     "r2", root=str(tmp_path))
    assert _r2["written"] is False and _r2["why"] == "dir_full"
    assert _r2["count"] == 1


# ================================================================ 刀4：内存观测
def _mk_pmc():
    from organs.core.PulseMetricsCollector import PulseMetricsCollector
    return PulseMetricsCollector("t377d4")


def test_m377_d4_attribution_never_reports_zero_for_no_data():
    """★反向验证（376 刀5 病根二）：没有尺寸行时必须 unavailable，**不许出 0.0**。"""
    _pmc = _mk_pmc()
    for _rows, _why in (([], "no_rows"),
                        ([{"file": "a.py", "count": 7}], "zero_size_rows"),
                        ([{"file": "a.py", "line": 1, "size_kb": 0.0, "count": 3}],
                         "zero_size_rows")):
        _r = _pmc._m349_heap_attribution(_rows)
        assert _r["state"] == "unavailable", _rows
        assert _r["reason"] == _why
        assert _r["total_size_kb"] is None, "不许拿 0.0 冒充「没采到」"
        assert _r["total_size_kb"] != 0.0
        assert _r["buckets"] == {}, "无尺寸时不许产出四类占比（那会是假 0%）"


def test_m377_d4_attribution_ok_when_sizes_present():
    _pmc = _mk_pmc()
    _rows = [{"file": "CallGraphAnalyzer.py", "line": 10, "size_kb": 300.0, "count": 4},
             {"file": "mnemosyne/PulseNodePool.py", "line": 20, "size_kb": 100.0,
              "count": 2},
             {"file": "PIL/Image.py", "line": 30, "size_kb": 400.0, "count": 1},
             {"file": "random.py", "line": 40, "size_kb": 200.0, "count": 5}]
    _r = _pmc._m349_heap_attribution(_rows)
    assert _r["state"] == "ok" and _r["reason"] == ""
    assert _r["total_size_kb"] == 1000.0
    assert _r["buckets"]["ast"]["size_kb"] == 300.0
    assert _r["buckets"]["pulsenode"]["size_kb"] == 100.0
    assert _r["buckets"]["image"]["size_kb"] == 400.0
    assert abs(sum(b["pct"] for b in _r["buckets"].values()) - 1.0) < 1e-6
    assert _r["rows_with_size"] == 4


def test_m377_d4_delta_states(tmp_path):
    """两快照差分的四态：no_baseline → ok（有增长）→ baseline_unreadable。"""
    import tracemalloc as _tm
    from organs.core.PulseMetricsCollector import _m377_tm_delta
    if not _tm.is_tracing():
        _tm.start(1)
    _base = str(tmp_path / "_tm_baseline.snap")
    _rows, _st = _m377_tm_delta(_tm.take_snapshot(), _base)
    assert _st == "no_baseline" and _rows == []
    assert os.path.isfile(_base), "差分后必须留下基线，否则下一拍又只能 no_baseline"
    _junk = [bytearray(4096) for _ in range(300)]        # 制造真实增长
    assert len(_junk) == 300
    _rows2, _st2 = _m377_tm_delta(_tm.take_snapshot(), _base)
    assert _st2 == "ok", "有增长却报 %s" % _st2
    assert _rows2 and all(r["size_kb"] > 0 for r in _rows2)
    assert _rows2 == sorted(_rows2, key=lambda r: -r["size_kb"]), "增量须按字节降序"
    # 基线读不动 ⇒ 如实报，不得当成「无增量」
    with open(_base, "w", encoding="utf-8") as _f:
        _f.write("not-a-snapshot")
    _rows3, _st3 = _m377_tm_delta(_tm.take_snapshot(), _base)
    assert _st3 == "baseline_unreadable" and _rows3 == []


def test_m377_d4_probe_end_to_end_resident(tmp_path, monkeypatch):
    """端到端：resident 模式下**不许再空采** —— rows 非空、basis=cumulative、
    归因 state=ok、top20 来源标明 tracemalloc。"""
    import config as _c
    import time as _t
    _pmc = _mk_pmc()
    _h = int(_t.localtime().tm_hour)
    monkeypatch.setattr(_c, "M339_MEMORY_PROBE_HOURS", (_h,), raising=False)
    monkeypatch.setattr(_c, "M339_MEMORY_PROBE_TOP_N", 8, raising=False)
    monkeypatch.setattr(_c, "M339_MEMORY_PROBE_MAX_SEC", 120.0, raising=False)
    monkeypatch.setattr(_c, "M339_TRACEMALLOC_MODE", "resident", raising=False)
    _pmc._m339_probe_last_slot = None
    _pmc._m339_probe_last_gc_slot = None
    _r = _pmc._m339_memory_probe(heap_mb=100.0, out_dir=str(tmp_path))
    assert _r["ran"] is True, _r.get("reason")
    _p = json.loads(open(_r["path"], encoding="utf-8").read())
    assert _p["tracemalloc_rows"], "常驻模式仍空采 ⇒ 刀4 未生效"
    assert "cumulative" in _p["tracemalloc_basis"]
    assert _p["top20_source"] == "tracemalloc"
    assert _p["heap_attribution"]["state"] == "ok"
    assert _p["heap_attribution"]["total_size_kb"] > 0, "归因仍算成 0 ⇒ 病根二未修"
    assert _p["tracemalloc_delta_state"] == "no_baseline"
    assert _p["mode"] in ("tracemalloc", "tracemalloc+gc_objects")


def test_m377_d4_probe_off_mode_is_labelled(tmp_path, monkeypatch):
    """反向：off 模式不许"看着像空采" —— 必须显式标 off + 归因 unavailable。"""
    import config as _c
    import time as _t
    _pmc = _mk_pmc()
    monkeypatch.setattr(_c, "M339_MEMORY_PROBE_HOURS",
                        (int(_t.localtime().tm_hour),), raising=False)
    monkeypatch.setattr(_c, "M339_MEMORY_PROBE_MAX_SEC", 120.0, raising=False)
    monkeypatch.setattr(_c, "M339_TRACEMALLOC_MODE", "off", raising=False)
    _pmc._m339_probe_last_slot = None
    _pmc._m339_probe_last_gc_slot = None
    _r = _pmc._m339_memory_probe(heap_mb=100.0, out_dir=str(tmp_path))
    assert _r["ran"] is True, _r.get("reason")
    _p = json.loads(open(_r["path"], encoding="utf-8").read())
    assert _p["tracemalloc_rows_state"] == "off"
    assert _p["tracemalloc_delta_state"] == "off"
    assert _p["heap_attribution"]["state"] == "unavailable"
    assert _p["top20_source"] == "none"



