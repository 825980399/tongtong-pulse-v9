# -*- coding: utf-8 -*-
"""432刀2：补丁管理器沉默误报 —— 根因判据与修复的钉子。

现场（logs/pulse.log，pid 18448）：
  03:04:01 → 06:45:46 共 **41 次**
  `[血管] WARNING: 分级自愈：1 个器官需处置: [('补丁管理器', 3)]`
  节奏恒为 5.4min（= `_escalation_alert_cooldown` 300s + 巡检间隔），
  同期 `[共同决策] 新提议` 仅 1 次（04:09:38）。

根因：`补丁管理器` **不是器官**，是 PatchManager 的 consent 脉冲发射器所用的
  `source_organ` 标签（main.py::_wire_consent_channel:1344 —— 该处注释已写明
  "用器官名会与 PulseConsent 自己的 organ_name 撞上"）。它只在两个时刻发脉冲：
  补丁入队（`consent.propose`，PatchManager.py:3350）与人工审批
  （`consent.decide`，:3577/:3612），天然间隔小时级；而沉默阈值是
  `_silence_threshold(15) × avg_interval(≈13.5s) ≈ 200s` ⇒ 必然周期性误报。
  ⇒ 不是"响应慢"，是"没有节律却按有节律判"。

本文件三条判据，缺一不可：
  1. **豁免命中**：`补丁管理器` 真的进了 `_exempt_organs`（按归一化 key）；
  2. **端到端 + 反证**：同样"600 秒没动静"，`补丁管理器` 不进沉默列表，
     而**同批的假器官必须进** —— 后半句是反证，防"沉默检测整体失效"
     被误当成修好了（铁律 35：判据要挑有判别力的量）；
  3. **放大器**：`alert_count` 有上界（原实现无界，配合失效的复位条件
     ⇒ 一旦到过 4 就永久 level=3，41 次告警全是 level=3）。
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.organ_identity import resolve_organ_key
from organs.body.PulseBloodVessel import PulseBloodVessel


def test_m432_d2_patch_manager_is_exempt():
    """判据 1：豁免表按归一化 key 命中 `补丁管理器`。

    为什么按归一化 key 判而不是直接 `in SILENCE_EXEMPT_ORGANS`：
    血管侧存的是 `{resolve_organ_key(o) for o in SILENCE_EXEMPT_ORGANS}`
    （PulseBloodVessel.py:78，T-112d 命名空间对齐），裸串比对会漏判。
    """
    _v = PulseBloodVessel()
    assert resolve_organ_key("补丁管理器") in _v._exempt_organs, (
        "补丁管理器未被豁免 ⇒ 沉默检测会按 15 拍阈值反复判它沉默（实测 41 次）")


def test_m432_d2_patrol_skips_patch_manager_but_flags_unknown():
    """判据 2：端到端 —— 豁免的真生效，且沉默检测本身**没有**被改坏。

    两个器官同样"600 秒没动静"（远超 15 拍 ≈ 150s 阈值）：
      · `补丁管理器` ⇒ 必须**不**出现在 silent_organs（修复目标）
      · `假器官-未被豁免` ⇒ 必须**出现**（反证：否则说明检测整体失效）
    """
    _v = PulseBloodVessel()
    _v._beat_count = _v._check_interval  # 让 _run_patrol 不 return skipped
    _now = time.time()
    _v._organ_last_active = {
        "补丁管理器": _now - 600.0,
        "假器官-未被豁免": _now - 600.0,
    }

    _rep = _v._run_patrol({"event_type": "heart.beat"})
    _names = [s["organ"] for s in _rep["silent_organs"]]

    assert "补丁管理器" not in _names, (
        f"补丁管理器仍被判沉默 ⇒ 豁免未生效；silent_organs={_names}")
    assert "假器官-未被豁免" in _names, (
        "同批的未豁免器官也没被判沉默 ⇒ 沉默检测整体失效，"
        f"这条修复是假的；silent_organs={_names}")


def test_m432_d2_alert_count_capped():
    """判据 3：`alert_count` 有上界 = `_escalation_degrade_threshold * 2`。

    原实现 `_state["alert_count"] += 1` 无上限；而"3 倍冷却未再告警才复位"
    （PulseBloodVessel.py:313）在本场景**永不成立**——`last_alert` 每轮告警都
    刷新，300s < 900s。⇒ 到过 4 之后永久 level=3。这里连打 12 轮把边界压出来。
    """
    _v = PulseBloodVessel()
    _v._check_interval = 1                  # 每拍都巡检
    _v._escalation_alert_cooldown = 0.0     # 去掉冷却，逼出累积边界
    _v._organ_last_active = {"假器官-未被豁免": time.time() - 600.0}

    for _ in range(12):
        _v._beat_count += 1
        _v._run_patrol({})

    _st = _v._organ_silence_state["假器官-未被豁免"]
    _cap = _v._escalation_degrade_threshold * 2
    assert _st["alert_count"] == _cap, (
        f"alert_count 未封顶：{_st['alert_count']} != {_cap}（12 轮后应停在 {_cap}）")
    assert _st["level"] == 3, (
        f"封顶后仍应因超 degrade 阈值判 level=3，实际 {_st['level']}")
