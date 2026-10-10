# -*- coding: utf-8 -*-
"""第54批 T3 门控测试：补丁自应用 P1（T3.1 状态清理 / T3.2 过期检测 / T3.3 开关生效 / T3.4 deprecated）。

背景
----
* **T3.1**：status="verified" 是 deprecated 状态 —— 实测 patch_history 中**从未出现**
  （真实状态是 approved / runtime_verified），但 chat_service 的状态映射仍把它当正常状态。
* **T3.2**：过期检测此前**从未真正生效** —— PatchAutoApprover 里已有完整实现
  （is_stale / _is_stale_by_age / classify），但该类 deprecated 且零生产调用。
  本批在**生产链路** PatchManager 加 `_is_stale()`。
* **T3.3**：`auto_apply_enabled` 在 PatchManager 中**原本完全没被读取**（只存在于
  config / PulseCodeLearner / SafeEvolutionExecutor）→ 审批侧现在读取并体现在返回值。
* **T3.4**：PatchAutoApprover 第53批已标 deprecated（零生产调用点），本批不改代码，
  迁移说明见交付报告。
"""
import contextlib
import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib  # noqa: E402
import io  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ★铁律 33：包 __init__ 可能导出同名类 → 用 importlib 取模块
_pm_mod = importlib.import_module("nucleus.reasoning.PatchManager")
PatchManager = getattr(_pm_mod, "PatchManager")
_m54_stale_days = getattr(_pm_mod, "_m54_stale_days")
_m54_auto_apply_on = getattr(_pm_mod, "_m54_auto_apply_on")

_CHAT_REL = os.path.join("functions", "chat", "chat_service.py")

_DAY = 86400.0


def _read(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace").read()


@contextlib.contextmanager
def _cfg_switch(attr, value):
    """临时改写 config 属性，退出还原（★铁律 49：相关断言必须写在块内）。"""
    had = hasattr(config, attr)
    old = getattr(config, attr, None)
    setattr(config, attr, value)
    try:
        yield
    finally:
        if had:
            setattr(config, attr, old)
        else:
            delattr(config, attr)


@pytest.mark.production_data
class TestStatusCleanupT31(unittest.TestCase):
    """T3.1：status="verified" deprecated 状态清理。"""

    def test_01_runtime_verified_in_status_map(self):
        """状态映射补上了真实存在的 runtime_verified。"""
        self.assertIn('"runtime_verified"', _read(_CHAT_REL))

    def test_02_verified_marked_deprecated(self):
        """verified 被明确标注为 deprecated（不再当正常状态）。"""
        src = _read(_CHAT_REL)
        self.assertIn("verified", src)
        self.assertIn("deprecated", src,
                      "verified 应被标注为 deprecated")

    def test_03_no_verified_status_in_patch_history(self):
        """★数据事实：patch_history 中不存在 status="verified"。"""
        _hist = os.path.join(_ROOT, "data", "evolution", "patch_history.json")
        if not os.path.exists(_hist):
            self.skipTest("patch_history.json 不存在（框架未产生补丁历史）")
        with open(_hist, encoding="utf-8") as f:
            _rows = json.load(f)
        _rows = _rows if isinstance(_rows, list) else _rows.get("patches", [])
        _bad = [r for r in _rows if isinstance(r, dict) and r.get("status") == "verified"]
        self.assertEqual(_bad, [], "patch_history 中仍有 status=verified 记录")


class TestStaleDetectionT32(unittest.TestCase):
    """T3.2：补丁过期检测（生产链路首次生效）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m54_pm_")
        self.mgr = PatchManager(self.tmp)

    def test_04_stale_after_threshold(self):
        """超过阈值天数 → 判为过期。"""
        _old = time.time() - (_m54_stale_days() + 1) * _DAY
        self.assertTrue(self.mgr._is_stale({"created_at": _old}))

    def test_05_fresh_not_stale(self):
        """未超阈值 → 不过期（不误伤）。"""
        self.assertFalse(self.mgr._is_stale({"created_at": time.time() - 3600}))

    def test_06_no_timestamp_not_stale(self):
        """★无可用时间戳 → 视为未过期（历史补丁不误伤）。"""
        self.assertFalse(self.mgr._is_stale({"file": "x.py"}))

    def test_07_threshold_follows_config(self):
        """★防漂移：改配置值，判据跟随（断言在 with 块内）。"""
        with _cfg_switch("PATCH_AUTO_APPROVE_STALE_DAYS", 1):
            self.assertEqual(_m54_stale_days(), 1.0)
            self.assertTrue(self.mgr._is_stale({"created_at": time.time() - 2 * _DAY}))

    def test_08_approve_marks_stale(self):
        """★真实调用：审批过期补丁 → 返回 stale=True 并落盘标记。"""
        with open(self.mgr._pending_file, "w", encoding="utf-8") as f:
            json.dump([{"file": "a.py", "method": "m",
                        "created_at": time.time() - (_m54_stale_days() + 1) * _DAY,
                        "original_code": "def m():\n    return 1\n",
                        "modified_code": "def m():\n    return 2\n"}], f)
        _r = self.mgr.approve_patch(0)
        self.assertTrue(_r.get("ok"), _r)
        self.assertTrue(_r.get("stale"), "过期补丁审批应标记 stale")
        with open(self.mgr._pending_file, encoding="utf-8") as f:
            self.assertTrue(json.load(f)[0].get("stale"))

    def test_09_stale_mark_switch_off(self):
        """★灰度：关闭 ENABLE_PATCH_STALE_MARK → 不打 stale（复现改造前行为）。"""
        with open(self.mgr._pending_file, "w", encoding="utf-8") as f:
            json.dump([{"file": "a.py", "method": "m",
                        "created_at": time.time() - (_m54_stale_days() + 1) * _DAY}], f)
        with mock.patch.object(config, "ENABLE_PATCH_STALE_MARK", False):
            _r = self.mgr.approve_patch(0)
        self.assertFalse(_r.get("stale"), "关闭开关时应复现改造前行为")


class TestAutoApplySwitchT33(unittest.TestCase):
    """T3.3：auto_apply_enabled 在 PatchManager 生效。"""

    def test_10_default_false(self):
        """默认（config 为 False）→ _m54_auto_apply_on() 返回 False。"""
        self.assertIs(config.EVOLUTION_CONFIG.get("auto_apply_enabled"), False)
        self.assertIs(_m54_auto_apply_on(), False)

    def test_11_follows_config(self):
        """★防漂移：config 置 True → 读取结果跟随（断言在 with 块内）。"""
        _fake = dict(config.EVOLUTION_CONFIG)
        _fake["auto_apply_enabled"] = True
        with _cfg_switch("EVOLUTION_CONFIG", _fake):
            self.assertIs(_m54_auto_apply_on(), True)

    def test_12_switch_off_forces_false(self):
        """★灰度：ENABLE_PATCH_APPROVE_AUTO_APPLY 关闭 → 恒 False（不读配置）。"""
        _fake = dict(config.EVOLUTION_CONFIG)
        _fake["auto_apply_enabled"] = True
        with _cfg_switch("EVOLUTION_CONFIG", _fake):
            with mock.patch.object(config, "ENABLE_PATCH_APPROVE_AUTO_APPLY", False):
                self.assertIs(_m54_auto_apply_on(), False)

    def test_13_approve_returns_auto_apply_flag(self):
        """审批结果带 auto_apply 字段（默认 False = 只审批不应用）。"""
        _tmp = tempfile.mkdtemp(prefix="m54_pm2_")
        _mgr = PatchManager(_tmp)
        with open(_mgr._pending_file, "w", encoding="utf-8") as f:
            json.dump([{"file": "a.py", "method": "m",
                        "original_code": "def m():\n    return 1\n",
                        "modified_code": "def m():\n    return 2\n"}], f)
        _r = _mgr.approve_patch(0)
        self.assertTrue(_r.get("ok"), _r)
        self.assertIn("auto_apply", _r, "审批结果应带 auto_apply 字段")
        self.assertIs(_r["auto_apply"], False)


class TestDeprecatedT34(unittest.TestCase):
    """T3.4：PatchAutoApprover 保持 deprecated（回归保护，不误启用）。"""

    def test_14_still_deprecated(self):
        """PatchAutoApprover 仍标注 deprecated（★防止后续误接入生产链路）。"""
        _src = _read(os.path.join("nucleus", "evolution", "PatchAutoApprover.py"))
        self.assertIn("deprecated", _src,
                      "PatchAutoApprover 的 deprecated 标注被移除了（需星轨重新裁决）")


if __name__ == "__main__":
    unittest.main()
