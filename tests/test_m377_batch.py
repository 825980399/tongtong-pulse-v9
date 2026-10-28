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
    assert tuple(BF.DEFAULT_PATCH_IDS) == tuple(WATCH_IDS) == (TARGET_ID,)


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
    """★口径陷阱钉死：两个验收辅助函数的**默认线**跟着 G4 键走（现为 7000）。

    它们当前无生产调用方（全库仅本批测试与 m338 测试调用，且 m338 显式传 2000）
    ⇒ 本批不动任何生效中的验收口径；但将来接线必须显式传 2000 才等于 D3 硬线。
    """
    from nucleus.mnemosyne import release_policy as rp
    _d = rp.heap_peak_guard(5000.0)
    assert _d["line_mb"] == 7000.0 and _d["verdict"] == "pass"
    assert rp.heap_peak_guard(5000.0, line_mb=2000.0)["verdict"] == "fail"
    assert rp.heap_peak_guard(1800.0, line_mb=2000.0)["verdict"] == "pass"
    # 判定线双写：heap 轨默认同样跟 G4 键
    _p = rp.judge_pass_line(8000.0, 5000.0)
    assert _p["heap_line_mb"] == 7000.0
    assert rp.judge_pass_line(8000.0, 5000.0,
                              heap_line_mb=2000.0)["verdict"] != "pass"


def test_m377_d2_fallback_stays_conservative():
    """`release_policy` 里的 fallback 2000 **不是漏改**：config 读不到时退回旧保守线。"""
    import inspect
    from nucleus.mnemosyne import release_policy as rp
    _src = inspect.getsource(rp.thresholds)
    assert '_cfg("MEMORY_RELEASE_G4_HEAP_MB", 2000.0)' in _src, \
        "fallback 被改动 ⇒ 须同步 config.py 注释里的口径说明"

