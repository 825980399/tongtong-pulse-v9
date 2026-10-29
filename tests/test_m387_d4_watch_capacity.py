# -*- coding: utf-8 -*-
"""387刀4：破零观察位补到 3 个 —— 自愈从「只补 1 个」改成「补满到容量」。"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution import zero_break_judge as zb  # noqa: E402

SEED_SRC = os.path.join(ROOT, "tools", "m386_seed_o3_watch.py")


def _pend(n: int):
    """n 个 O3 候选（编号可区分）。"""
    return [{"id": "o3_%016d" % _i, "file": "a.py", "method": "m%d" % _i,
             "issue_type": "dead_code_candidate", "status": "pending"}
            for _i in range(1, n + 1)]


def test_m387_d4_heal_fills_to_capacity():
    """★核心：pending 里有 3 个 O3 ⇒ 自愈**全部**补进观察位（不再只补 1 个）。

    386 刀1 的自愈挑到第一个就 `break` —— 387 补了 3 个候选却只有 1 个进观察位，
    "多候选防单点失明"形同虚设。
    """
    zb.reset_watch_runtime()
    try:
        _p = _pend(3)
        _r = zb.self_heal_watch(_p, [])
        assert _r["state"] == "healed", _r
        assert _r["added_all"] == _r["added_all"][:1] + _r["added_all"][1:]
        assert len(_r["added_all"]) == 3, "应补满 3 个，实际 %s" % _r["added_all"]
        _act = zb.active_watch_ids()
        for _i in range(1, 4):
            assert ("o3_%016d" % _i) in _act, "第 %d 个候选没进观察位" % _i
    finally:
        zb.reset_watch_runtime()


def test_m387_d4_capacity_still_caps():
    """★反控：候选多于容量时**不许超**（容量是硬上限，不是建议值）。"""
    zb.reset_watch_runtime()
    try:
        _r = zb.self_heal_watch(_pend(20), [], capacity=3)
        # 静态种子 2 个已占位 ⇒ 只剩 3 个位可补
        _runtime = [x for x in zb.active_watch_ids() if x.startswith("o3_00000000000000")]
        assert len(_runtime) <= 3, "超过容量: %s" % _runtime
        assert len(zb.active_watch_ids()) <= 3 + len(zb.WATCH_IDS)
    finally:
        zb.reset_watch_runtime()


def test_m387_d4_second_call_is_idempotent():
    """补满后再调用 ⇒ **不重复追加**（state=healthy，added 为空）。

    ★注意 `changed`：它比较的是**状态串**，healed→healthy 是真实状态变化
    ⇒ changed=True 是对的（防刷屏指的是"同一失效态不重复打告警"，
      而不是"返回值永远不变"）。
    """
    zb.reset_watch_runtime()
    try:
        _r1 = zb.self_heal_watch(_pend(2), [])
        assert _r1["state"] == "healed"
        _r2 = zb.self_heal_watch(_pend(2), [])
        assert _r2["state"] == "healthy", _r2
        assert _r2["added"] == "", "已补满就不该再补: %s" % _r2
        assert _r2.get("added_all") is None, "healthy 分支不该有 added_all"
        # 观察位数量不变（没被重复追加）
        assert len(zb.active_watch_ids()) == len(_r1["active"])
    finally:
        zb.reset_watch_runtime()


def test_m387_d4_backup_filename_has_timestamp():
    """★387 实测缺陷：备份名固定时，连续补 2 个位的第二次备份会**覆盖**第一次
    ⇒ 中间状态无法回退。备份名必须带时间戳。"""
    _s = io.open(SEED_SRC, encoding="utf-8").read()
    assert "pending_before_%s.json" in _s, "备份名应带时间戳"
    assert 'pending_before_386_d1.json"' not in _s, "不该再有固定备份名"


def test_m387_d4_report_fields():
    """报告字段向前兼容：`added` 仍是首个（老调用方不破），新增 `added_all`。"""
    zb.reset_watch_runtime()
    try:
        _r = zb.self_heal_watch(_pend(2), [])
        assert _r["added"] == _r["added_all"][0]
        assert isinstance(_r["added_all"], list)
    finally:
        zb.reset_watch_runtime()
