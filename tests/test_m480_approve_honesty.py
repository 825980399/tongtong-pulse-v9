# -*- coding: utf-8 -*-
"""480 批：让「审批成功」这个返回值诚实（四个刀 + channel_health 单例）。

## 判据来源

活体 2026-11-09 实测链条：
  ① `channel_client::_report_health` import 不存在的 `get_channel_health`
     ⇒ 渠道健康度**从未被记录**，熔断整条机制失效（刀1）；
  ② `all_collab` 三处 import 不存在的 `get_logger`
     ⇒ 「跳过坏渠道」三条最该看见的日志静默丢失（刀2）；
  ③ 同一补丁当天被"批准成功" 3 次，pending 里 status 始终 verified
     ⇒ 3 次吃光日额度，当天其余补丁全 daily_cap（刀3/刀4/刀5）。

## 这些测试**不**证明什么

不证明渠道真的变健康了 —— 那要活体跑；这里只证明「上报口存在且写得进去」。
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


from nucleus.evolution import approval_bridge as AB
from nucleus.reasoning.PatchManager import PatchManager

_DEF = "def target(self, x):\n    return x\n"
_NEW = "def target(self, x):\n    if x is None:\n        return 0\n    return x\n"


def _mk_patch(pid="patch_m480_a", status="verified"):
    return {
        "id": pid,
        "file": "organs/body/PulseHeart.py",
        "method": "target",
        "issue_type": "silent_exception",
        "risk_level": "低",
        "trust_score": 75.0,
        "confidence": "high",
        "baseline_errors": 2,
        "problem_gone": True,
        "original_code": _DEF,
        "modified_code": _NEW,
        "status": status,
        "applied": False,
    }


def _pm(tmp_path):
    """隔离 PatchManager：root = tmp_path，绝不碰活体 data/patches/。"""
    _pm_ = PatchManager(str(tmp_path))
    _pd = os.path.join(str(tmp_path), "data", "patches")
    os.makedirs(_pd, exist_ok=True)
    assert os.path.abspath(_pm_._pending_file).startswith(os.path.abspath(str(tmp_path))), \
        "隔离失败：_pending_file 不在 tmp 内"
    return _pm_


def _write_pending(pm_, rows):
    pm_._save_patch_list(pm_._pending_file, rows)


def _trace(root, rows):
    _p = AB.approval_trace_path(str(root))
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    with open(_p, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return _p


# ============ 刀1：channel_health 单例 ============

def test_m480_channel_health_singleton_exists():
    """`channel_client._report_health` 要的 `get_channel_health` 必须真的存在。"""
    from nucleus.llm import channel_health as CH

    assert callable(getattr(CH, "get_channel_health", None)), \
        "channel_client 每次调用都会 import 这个名字，缺了就是 ImportError"
    _a = CH.get_channel_health()
    _b = CH.get_channel_health()
    assert _a is _b, "必须是进程级单例（两本账会让熔断读不到写入）"


def test_m480_channel_health_can_record():
    """能真的记进去 —— 否则熔断永远看 success_rate=1.0。"""
    from nucleus.llm import channel_health as CH

    CH.reset_channel_health()
    _h = CH.get_channel_health()
    _h.reset()
    for _ in range(3):
        _h.record("probe_ch", False, 0.1)
    assert _h.success_rate("probe_ch") == 0.0
    assert _h.is_available("probe_ch") is False, "连续 3 次失败应触发熔断"
    CH.reset_channel_health()


def test_m480_channel_client_report_health_no_import_error(monkeypatch):
    """端到端：`_report_health` 走真 import，且失败不影响主流程。"""
    from nucleus.llm import channel_health as CH
    from nucleus.reasoning import channel_client as CC

    CH.reset_channel_health()
    CC._report_health("probe_e2e", True, 0.05)
    _snap = CH.get_channel_health().snapshot()
    assert "probe_e2e" in _snap, "上报没有落到共享 tracker ⇒ 还是 ImportError 被吞"
    assert _snap["probe_e2e"]["calls"] == 1
    CH.reset_channel_health()


# ============ 刀2：all_collab 的 logger ============

def test_m480_all_collab_imports_cleanly():
    """`nucleus.logger` 没有 `get_logger`；模块必须自备 logger 而不是每次 import。"""
    from nucleus.autonomy import all_collab as AC

    assert hasattr(AC, "_L"), "模块级 logger 缺失"
    assert callable(getattr(AC._L, "info", None))


def test_m480_all_collab_low_rate_log_not_swallowed(tmp_path, monkeypatch):
    """低成功率渠道被跳过时，日志必须真的打出来（此前被 ImportError 吞掉）。"""
    from nucleus.autonomy import all_collab as AC

    _calls = []

    class _Spy:
        def info(self, msg, *a):
            _calls.append(msg % a if a else msg)

        def warning(self, msg, *a):
            _calls.append(msg % a if a else msg)

        def debug(self, msg, *a):
            pass

    monkeypatch.setattr(AC, "_L", _Spy())
    # 清掉“同类只记一次”的集合，否则第二次跑不出日志
    AC._LOWRATE_LOGGED.clear()
    monkeypatch.setattr(AC, "_channel_stats",
                        lambda _win: {"bad_ch": (20, 1)})
    _out = AC._low_rate_channels()
    assert "bad_ch" in _out
    assert any("跳过低成功率渠道" in c for c in _calls), \
        "日志被吞了 —— 这正是刀2 要修的静默丢失"


# ============ 刀3：日额度按 patch_id 去重 ============

def _row(pid, approved=True, refund=False):
    return {
        "day": time.strftime("%Y-%m-%d"),
        "approved": bool(approved),
        "refund": True if refund else None,
        "patch_id": pid,
    }


def test_m480_cap_dedup_same_patch_counts_once(tmp_path):
    """同一条补丁当天被"批准成功" 3 次 ⇒ 只计 1 条额度。"""
    _trace(tmp_path, [_row("patch_x"), _row("patch_x"), _row("patch_x")])
    assert AB.approved_today(str(tmp_path)) == 1


def test_m480_cap_dedup_distinct_patches(tmp_path):
    _trace(tmp_path, [_row("patch_x"), _row("patch_y"), _row("patch_z")])
    assert AB.approved_today(str(tmp_path)) == 3


def test_m480_cap_refund_still_works_with_dedup(tmp_path):
    """退款照旧扣回；同一条重复批准 + 一次退款 ⇒ 0（不是 -1）。"""
    _trace(tmp_path, [_row("patch_x"), _row("patch_x"),
                      _row("patch_x", approved=False, refund=True)])
    assert AB.approved_today(str(tmp_path)) == 0


def test_m480_cap_dedup_off_restores_row_count(tmp_path, monkeypatch):
    """开关关 ⇒ 逐字回到按行计数（3 条同 id = 3）。"""
    monkeypatch.setattr(AB, "_m480_cap_dedup_on", lambda: False)
    _trace(tmp_path, [_row("patch_x"), _row("patch_x"), _row("patch_x")])
    assert AB.approved_today(str(tmp_path)) == 3


def test_m480_cap_no_id_rows_still_counted(tmp_path):
    """无 patch_id 的行无处去重 ⇒ 逐行计（不因缺字段而少算额度）。"""
    _trace(tmp_path, [_row(""), _row("")])
    assert AB.approved_today(str(tmp_path)) == 2


# ============ 刀4：落盘后读回校验 ============

def test_m480_readback_happy_path(tmp_path):
    """正常落盘 ⇒ ok=True、status 真的变 approved。"""
    pm_ = _pm(tmp_path)
    _write_pending(pm_, [_mk_patch()])
    _r = pm_.approve_patch(0, signer="auto:bridge_447", judged_by="m480_test",
                           source="auto")
    assert _r.get("ok") is True, _r
    with open(pm_._pending_file, encoding="utf-8") as _f:
        _rows = json.load(_f)
    assert _rows[0]["status"] == "approved"


def test_m480_readback_catches_silent_overwrite(tmp_path, monkeypatch):
    """核心判据：**条数没变但内容被覆盖**必须被抓住（改动前会假成功）。"""
    pm_ = _pm(tmp_path)
    _write_pending(pm_, [_mk_patch()])

    # 模拟并发写者：保存"成功"（返回 True），但盘上仍是 verified
    monkeypatch.setattr(pm_, "_save_patch_list", lambda *_a, **_k: True)

    _r = pm_.approve_patch(0, signer="auto:bridge_447", judged_by="m480_test",
                           source="auto")
    assert _r.get("ok") is False, "条数没变但内容没写成，必须按失败返回"
    assert "读回" in str(_r.get("reason") or "")


def test_m480_readback_off_restores_false_success(tmp_path, monkeypatch):
    """开关关 ⇒ 逐字回到旧行为（假成功），证明刀4 是唯一变量。"""
    pm_ = _pm(tmp_path)
    _write_pending(pm_, [_mk_patch()])
    monkeypatch.setattr(pm_, "_save_patch_list", lambda *_a, **_k: True)
    monkeypatch.setattr(PatchManager, "_m480_readback_on", staticmethod(lambda: False))

    _r = pm_.approve_patch(0, signer="auto:bridge_447", judged_by="m480_test",
                           source="auto")
    assert _r.get("ok") is True, "关掉开关应回到假成功（A/B 对照）"


# ============ 刀5：审批读-改-写加锁 ============

def test_m480_approve_lock_switch_and_entry():
    assert PatchManager._m480_approve_lock_on() is True
    assert callable(getattr(PatchManager, "_m480_approve_by_id_unlocked", None)), \
        "加锁入口与内部实现必须分离"


def test_m480_approve_by_id_still_works_under_lock(tmp_path):
    """加锁后功能不变：approve_patch_by_id 仍能真的把状态写进去。"""
    pm_ = _pm(tmp_path)
    _write_pending(pm_, [_mk_patch(pid="patch_m480_lock")])
    _r = pm_.approve_patch_by_id("patch_m480_lock", by="auto:bridge_447",
                                 reason="m480 lock test",
                                 judged_by="m480_test")
    assert _r.get("ok") is True, _r
    with open(pm_._pending_file, encoding="utf-8") as _f:
        _rows = json.load(_f)
    _row0 = next(x for x in _rows if x.get("id") == "patch_m480_lock")
    assert _row0["status"] == "approved"
    assert _row0["approved_source"] == "auto"


def test_m480_approve_by_id_unknown_pid_returns_error(tmp_path):
    pm_ = _pm(tmp_path)
    _write_pending(pm_, [_mk_patch(pid="patch_m480_only")])
    _r = pm_.approve_patch_by_id("patch_not_exist", by="auto:bridge_447")
    assert _r.get("ok") is False
    assert "找不到" in str(_r.get("reason") or "")
