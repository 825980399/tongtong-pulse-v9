# -*- coding: utf-8 -*-
"""385 刀1：破零观察位多值 + 自愈重建 + 缺失告警。

## 这一刀修的是什么

384 刀2 依规清零 `pending_patches.json`，把**唯一**观察位 `o3_b31b31fee790d6e9`
一并删掉。此后 `watch_candidate` 对每个位都返 `not_in_pool`，而那是**正常返回值**
（不报警）⇒ 破零这件事静默失明，直到有人翻报告才发现。

## 两条设计取舍（都有反向守卫）

1. **healthy 只看 `pending`，不看 `history`**：
   破零观察的是「这个补丁会不会被**选中进沙箱**」，只有 pending 里的才会被派单；
   history 里的已结案条目（wont_fix / approved / verified）永远 selected=0，
   把它当"活着"会让自愈永不触发 —— 那就又回到静默失明了。

2. **告警按 `changed` 去重**：
   `_m351_watch_zero_break` 每拍派单都调一次，无条件打 WARNING 会在一次停机里
   刷出成百上千条同义告警。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: F401  （本文件用不到，但保留与本批其它件一致的可读性）

from nucleus.evolution import zero_break_judge as zb  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    zb.reset_watch_runtime()
    yield
    zb.reset_watch_runtime()


# ------------------------------------------------------------ ① 结构要求
def test_m385_d1_watch_ids_is_multi():
    """★票面第 1 条：`WATCH_IDS` 必须从单值改成多值（至少 2 个真实 ID）。"""
    assert isinstance(zb.WATCH_IDS, (tuple, list))
    assert len(zb.WATCH_IDS) >= 2, "仍是单值：%s" % (zb.WATCH_IDS,)
    for _x in zb.WATCH_IDS:
        assert str(_x).startswith("o3_"), "静态种子只放真实 O3 ID，不编：%r" % (_x,)


def test_m385_d1_capacity_at_least_three():
    """★票面「至少 3 个候选位」的落法：容量常量 ≥3。"""
    assert zb.WATCH_CAPACITY >= 3


def test_m385_d1_active_ids_seed_first():
    """未自愈时，生效位就是静态种子（去重保序）。"""
    assert zb.active_watch_ids() == tuple(zb.WATCH_IDS)


# ------------------------------------------------------------ ② 自愈
def test_m385_d1_heals_from_pending():
    """★核心：静态位全不在 pending，而 pending 里有 O3 候选 ⇒ 自动补位。"""
    _r = zb.self_heal_watch([{"id": "o3_aaaabbbbccccdddd", "issue_type": "dead_code"}], [])
    assert _r["state"] == "healed"
    assert _r["added"] == "o3_aaaabbbbccccdddd"
    assert "o3_aaaabbbbccccdddd" in zb.active_watch_ids()
    assert _r["changed"] is True


def test_m385_d1_no_candidate_when_pending_has_no_o3():
    """★当前真实状态：pending 有货但 0 个 O3 ⇒ no_candidate（调用方据此告警）。"""
    _r = zb.self_heal_watch(
        [{"id": "patch_llm_1", "issue_type": "silent_exception"}], [])
    assert _r["state"] == "no_candidate"
    assert _r["why"] == "pending_has_no_o3_candidate"
    assert _r["added"] == ""
    assert zb.active_watch_ids() == tuple(zb.WATCH_IDS), "没候选就不许乱补"


def test_m385_d1_no_pending_when_empty():
    _r = zb.self_heal_watch([], [])
    assert _r["state"] == "no_pending"


# ------------------------------------------ ③ healthy 只看 pending（反向守卫）
def test_m385_d1_history_only_is_not_healthy():
    """★反向守卫：只在 history 里有（已结案）**不算活着**。

    这正是我第一版写错的地方 —— 把 history 命中当 healthy，
    于是自愈永不触发，等于没修。
    """
    _hist = [{"id": "o3_b31b31fee790d6e9", "status": "wont_fix"}]
    _r = zb.self_heal_watch([], _hist)
    assert _r["state"] != "healthy", "已结案条目被当成活观察位 ⇒ 自愈永不触发"
    assert _r["state"] == "no_pending"


def test_m385_d1_pending_hit_is_healthy():
    _r = zb.self_heal_watch([{"id": zb.WATCH_IDS[0]}], [])
    assert _r["state"] == "healthy"
    assert _r["why"] == "at_least_one_in_pending"


# ------------------------------------------------------ ④ 幂等 / 容量 / 去重
def test_m385_d1_idempotent():
    """重复调用不许重复追加；第二次该位已在 pending ⇒ 判 healthy。

    注意 `changed` 此时为 True 是**对的**：`healed → healthy` 是真实状态变化。
    防刷屏不靠这里，而靠「healthy 分支不打告警」+「同一失效状态只报一次」
    （见 `test_m385_d1_changed_dedup_same_state_twice`）。
    """
    _pend = [{"id": "o3_aaaabbbbccccdddd"}]
    _r1 = zb.self_heal_watch(_pend, [])
    assert _r1["state"] == "healed"
    _first = list(zb._WATCH_RUNTIME)
    _r2 = zb.self_heal_watch(_pend, [])
    assert list(zb._WATCH_RUNTIME) == _first, "重复调用不许重复追加"
    assert _r2["state"] == "healthy", "补进来的位已在 pending ⇒ 应判健康"


def test_m385_d1_capacity_respected():
    for _i in range(zb.WATCH_CAPACITY + 2):
        zb._WATCH_RUNTIME.append("o3_fill%014d" % _i)
    _r = zb.self_heal_watch([{"id": "o3_zzzz"}], [], capacity=zb.WATCH_CAPACITY)
    assert _r["state"] == "no_candidate"
    assert _r["why"].startswith("capacity_full")


def test_m385_d1_changed_dedup_same_state_twice():
    """同一失效状态连报两次，只有第一次 changed=True。"""
    _pend = [{"id": "patch_x", "issue_type": "silent_exception"}]
    _a = zb.self_heal_watch(_pend, [])
    _b = zb.self_heal_watch(_pend, [])
    assert (_a["changed"], _b["changed"]) == (True, False)


def test_m385_d1_reset_clears_both():
    zb.self_heal_watch([{"id": "o3_aaaabbbbccccdddd"}], [])
    zb.reset_watch_runtime()
    assert zb._WATCH_RUNTIME == []
    assert zb._LAST_HEAL_STATE[0] == ""


# --------------------------------------------------------- ⑤ 诊断口
def test_m385_d1_watch_available_reports_both_sources():
    _rows = zb.watch_available([{"id": zb.WATCH_IDS[0]}],
                               [{"id": zb.WATCH_IDS[1]}])
    _by = {r["patch_id"]: r for r in _rows}
    assert _by[zb.WATCH_IDS[0]]["in_pending"] is True
    assert _by[zb.WATCH_IDS[1]]["in_history"] is True
    assert all(r["runtime"] is False for r in _rows)


# ------------------------------------------- ⑥ 接线与告警（源码级守卫）
def test_m385_d1_watch_batch_uses_dynamic_ids():
    """`watch_batch` 必须用动态生效位，不能还盯着常量。"""
    _src = io.open(os.path.join(ROOT, "nucleus/evolution/zero_break_judge.py"),
                   encoding="utf-8").read()
    assert "for _pid in (patch_ids or active_watch_ids()):" in _src
    assert "for _pid in (patch_ids or WATCH_IDS):" not in _src


def test_m385_d1_caller_warns_on_missing_and_no_candidate():
    """★票面第 2/3 条：调用方必须对「已自愈」「无法自愈」「not_in_pool」都留痕。"""
    _src = io.open(os.path.join(ROOT, "nucleus/evolution/patch_execution_dispatch.py"),
                   encoding="utf-8").read()
    _seg = _src[_src.index("def _m351_watch_zero_break"):]
    _seg = _seg[:_seg.index("def dispatch_o3_approved")]
    assert "_zb351.self_heal_watch(pending_rows, _hrows)" in _seg, "没接自愈"
    assert "[385刀1] 破零观察位在 pending 里全失效 ⇒ 已自愈补位" in _seg
    assert "破零判据已失明，需人工补位" in _seg
    assert '"not_in_pool"]' in _seg, "缺失位没有单独告警"
    assert 'if _heal.get("changed"):' in _seg, "告警未按 changed 去重（会刷屏）"


def test_m385_d1_judge_module_stays_pure():
    """纯判据模块不许引入 logging/IO（自 351 起的设计，刀1 不改）。"""
    _src = io.open(os.path.join(ROOT, "nucleus/evolution/zero_break_judge.py"),
                   encoding="utf-8").read()
    assert "import logging" not in _src
    assert "_module_logger" not in _src
