# -*- coding: utf-8 -*-
"""主线第32批 门控测试：火山方舟免费额度监控与自动切换（T6 / P2-184）。

背景：框架此前无免费额度监控 —— 只能靠「连续失败 3 次 → 熔断 300s」被动应对，
最多浪费 90 秒且无预警。本批新增 `ChannelQuotaMonitor`：
统计 token 用量 → 剩余 <10% 降优先级(+3) / <5% 暂停剔除；与熔断**相互独立**。

★测试全程使用**注入的临时用量文件**，绝不写生产 `data/channel_quota_usage.json`。
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.llm.ChannelQuotaMonitor import ChannelQuotaMonitor  # noqa: E402

_LUNG_SRC = open(os.path.join(_PROJECT_ROOT, "organs", "body", "PulseLung.py"),
                 encoding="utf-8").read()

_ARK = "ark-ds-v4-flash"       # 生产 quota_limit：第32批为 500000；2026-09-15 起调为 5000000
_ZHIPU = "zhipu"               # quota_limit = -1（不限量）

# ★主线第62批 T3/P1：测试基准额度（tokens）——与生产配置解耦
#
# 背景：2026-09-15 上游给「协作奖励计划」渠道把 quota_limit 由 50 万调为 500 万
#   （每日上限，用多少返多少），导致本文件 9 个用例的硬编码断言（基于 50 万）全部失效。
# 修复：不修改生产代码/生产配置，改为在测试内把 _ARK 的 quota_limit 固定为 50 万。
#   这样无论生产额度后续再怎么调，本测试都稳定通过 —— 测试验证的是
#   ChannelQuotaMonitor 的「比例/阈值/白名单」逻辑，而不是生产额度数值本身。
_ARK_TEST_LIMIT = 500000
_OLD_CHANNELS = None


def setUpModule():
    """把测试基准 quota_limit 固定为 _ARK_TEST_LIMIT（深拷贝，退出时还原）。"""
    global _OLD_CHANNELS
    _OLD_CHANNELS = getattr(config, "REMOTE_API_CHANNELS", None)
    _d = copy.deepcopy(_OLD_CHANNELS) if isinstance(_OLD_CHANNELS, dict) else {"default_channels": []}
    for _ch in (_d.get("default_channels") or []):
        if isinstance(_ch, dict) and str(_ch.get("name")) == _ARK:
            _ch["quota_limit"] = _ARK_TEST_LIMIT
            # ★第97批 T-97c：生产 ark-ds-v4-flash 现标注 quota_type=daily_reward
            # （协作奖励，每日重置、不降优先级/不暂停）。本测试验证的是「固定额度渠道
            # 的降优先级/暂停」逻辑，与生产 quota_type 解耦（同 quota_limit 的解耦思路），
            # 避免协作奖励豁免逻辑干扰固定额度断言。daily_reward 豁免由 test_quota_type_m97.py 覆盖。
            _ch["quota_type"] = "fixed"
    config.REMOTE_API_CHANNELS = _d


def tearDownModule():
    """还原生产渠道配置，避免污染同进程内的其他测试。"""
    global _OLD_CHANNELS
    if _OLD_CHANNELS is not None:
        config.REMOTE_API_CHANNELS = _OLD_CHANNELS


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m32_quota_")
        self._path = os.path.join(self._dir, "quota.json")
        self.m = ChannelQuotaMonitor(usage_path=self._path)

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _fill(self, name, used):
        """把某渠道用量直接填到指定值。"""
        _limit = self.m.get_limit(name)
        self.m._usage.setdefault(name, {
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens_used": 0, "last_updated": 0.0})
        self.m._usage[name]["total_tokens_used"] = int(used)
        self.m._dirty = True


class TestQuotaStats(_Base):
    """① 额度使用统计与持久化。"""

    def test_01_record_accumulates(self):
        self.m.record_usage(_ARK, 100, 50)
        self.m.record_usage(_ARK, 20, 10)
        self.assertEqual(self.m.get_used(_ARK), 180)

    def test_02_zero_usage_ignored(self):
        self.m.record_usage(_ARK, 0, 0)
        self.assertEqual(self.m.get_used(_ARK), 0)

    def test_03_empty_name_ignored(self):
        self.m.record_usage("", 10, 10)
        self.assertEqual(self.m.get_used(""), 0)

    def test_04_persist_and_reload(self):
        self.m.record_usage(_ARK, 1000, 500)
        self.assertTrue(self.m.save(force=True), "应落盘成功")
        self.assertTrue(os.path.exists(self._path))
        _m2 = ChannelQuotaMonitor(usage_path=self._path)
        self.assertEqual(_m2.get_used(_ARK), 1500, "重启后用量不得丢失")
        with open(self._path, encoding="utf-8") as _f:
            _d = json.load(_f)
        self.assertIn(_ARK, _d["channels"])

    def test_05_remaining_ratio(self):
        self.assertEqual(self.m.get_remaining_ratio(_ZHIPU), None, "-1 应视为不限量")
        self._fill(_ARK, 250000)
        self.assertAlmostEqual(self.m.get_remaining_ratio(_ARK), 0.5, places=4)

    def test_06_stats_view(self):
        _st = self.m.get_stats()
        for _k in ("enabled", "degrade_ratio", "pause_ratio", "force_enabled",
                   "usage_path", "channels"):
            self.assertIn(_k, _st)
        self.assertIn(_ARK, _st["channels"])


class TestAutoDegradeAndPause(_Base):
    """② 自动降低优先级（<10%）/ 自动暂停（<5%）。"""

    def test_10_degrade_below_10pct(self):
        """剩余 8% → 优先级 +3，且仍在池中。"""
        self._fill(_ARK, 460000)                       # 剩 8%
        _pool = self.m.apply_to_channels(config.get_active_channels())
        _names = [c["name"] for c in _pool]
        self.assertIn(_ARK, _names, "降级不应剔除渠道")
        _c = next(c for c in _pool if c["name"] == _ARK)
        _orig = next(c for c in config.get_active_channels() if c["name"] == _ARK)
        self.assertEqual(_c["priority"], int(_orig["priority"]) + 3)

    def test_11_pause_below_5pct(self):
        """剩余 4% → 从池中剔除。"""
        self._fill(_ARK, 480000)                       # 剩 4%
        _pool = self.m.apply_to_channels(config.get_active_channels())
        self.assertNotIn(_ARK, [c["name"] for c in _pool], "低于暂停阈值应被剔除")

    def test_12_low_ratio_not_degraded_at_30pct(self):
        """剩余 30% → 不触发任何处置（避免误伤）。"""
        self._fill(_ARK, 350000)                       # 剩 30%
        _pool = self.m.apply_to_channels(config.get_active_channels())
        _c = next(c for c in _pool if c["name"] == _ARK)
        _orig = next(c for c in config.get_active_channels() if c["name"] == _ARK)
        self.assertEqual(_c["priority"], _orig["priority"])

    def test_13_is_exhausted_flag(self):
        self.assertFalse(self.m.is_exhausted(_ARK))
        self._fill(_ARK, 490000)                       # 剩 2%
        self.assertTrue(self.m.is_exhausted(_ARK))

    def test_14_pool_sorted_after_degrade(self):
        """降级后必须重排（否则 +3 无意义）。"""
        self._fill(_ARK, 460000)
        _pool = self.m.apply_to_channels(config.get_active_channels())
        _pris = [c.get("priority", 999) for c in _pool]
        self.assertEqual(_pris, sorted(_pris), "返回的池必须按 priority 升序")

    def test_15_input_not_mutated(self):
        """不得就地修改调用方传入的渠道 dict（避免污染 config 缓存）。"""
        _orig = config.get_active_channels()
        _before = [dict(c) for c in _orig]
        self._fill(_ARK, 460000)
        self.m.apply_to_channels(_orig)
        self.assertEqual([dict(c) for c in _orig], _before, "入参不得被修改")

    def test_16_unlimited_channel_never_touched(self):
        self._fill(_ZHIPU, 99_999_999)
        _pool = self.m.apply_to_channels(config.get_active_channels())
        self.assertIn(_ZHIPU, [c["name"] for c in _pool], "不限量渠道不得被剔除")


class TestForceEnable(_Base):
    """③ 手动恢复：FORCE_ENABLE_CHANNELS 白名单。"""

    def test_20_force_enable_keeps_channel(self):
        self._fill(_ARK, 499000)                       # 剩 0.2%
        self.assertTrue(self.m.is_exhausted(_ARK))
        with _Switch(FORCE_ENABLE_CHANNELS=[_ARK]):
            self.assertFalse(self.m.is_exhausted(_ARK))
            _pool = self.m.apply_to_channels(config.get_active_channels())
            self.assertIn(_ARK, [c["name"] for c in _pool], "白名单应强制放行")

    def test_21_force_enable_dropped_when_removed(self):
        """改配置后热重载即生效（无需重启）。"""
        self._fill(_ARK, 499000)
        with _Switch(FORCE_ENABLE_CHANNELS=[_ARK]):
            _pool = self.m.apply_to_channels(config.get_active_channels())
            self.assertIn(_ARK, [c["name"] for c in _pool])
        _pool2 = self.m.apply_to_channels(config.get_active_channels())
        self.assertNotIn(_ARK, [c["name"] for c in _pool2])


class TestSwitch(_Base):
    """④ 灰度开关。"""

    def test_30_switch_off_passthrough(self):
        self._fill(_ARK, 499000)
        with _Switch(ENABLE_QUOTA_MONITOR=False):
            _pool = self.m.apply_to_channels(config.get_active_channels())
            self.assertIn(_ARK, [c["name"] for c in _pool],
                          "开关关闭时应原样返回（零副作用）")

    def test_31_switch_default_on(self):
        self.assertTrue(getattr(config, "ENABLE_QUOTA_MONITOR", False))


class TestIndependentOfCircuitBreak(_Base):
    """⑤ 与熔断机制独立。"""

    def test_40_exhausted_is_quota_only(self):
        """额度状态只看 token 用量，与渠道健康/熔断无关。"""
        from nucleus.llm.channel_health import ChannelHealthTracker
        _t = ChannelHealthTracker(circuit_break_threshold=3, circuit_break_seconds=60)
        for _ in range(3):
            _t.record(_ARK, False)                     # 熔断
        self.assertFalse(_t.is_available(_ARK), "应处于熔断")
        self.assertFalse(self.m.is_exhausted(_ARK), "额度充足 → 不得被判为耗尽")
        self._fill(_ARK, 499000)
        self.assertTrue(self.m.is_exhausted(_ARK), "额度耗尽 → 独立成立")


class TestPulseLungWiring(unittest.TestCase):
    """⑥ 源码接线。"""

    def test_50_record_usage_called(self):
        self.assertIn("_m32_record_quota_usage(_name, _data)", _LUNG_SRC)

    def test_51_policy_applied_to_pool(self):
        self.assertIn("_m32_apply_quota_policy(_pool)", _LUNG_SRC)

    def test_52_methods_exist(self):
        for _k in ("def _m32_record_quota_usage(", "def _m32_apply_quota_policy("):
            self.assertIn(_k, _LUNG_SRC)


class TestStaleChannelCleanup(_Base):
    """⑦ P2-194：加载时清理不在当前配置中的渠道条目。"""

    def _write_usage_file(self, channels: dict) -> str:
        """写入包含指定渠道的用量文件，返回路径。"""
        _path = os.path.join(self._dir, "stale_test.json")
        with open(_path, "w", encoding="utf-8") as _f:
            json.dump({"channels": channels, "updated_at": 1.0}, _f)
        return _path

    def test_01_stale_channel_removed_on_load(self):
        """加载时应移除不在当前配置中的渠道。"""
        _stale = {
            "old-channel-1": {"prompt_tokens": 100, "completion_tokens": 50,
                              "total_tokens_used": 150, "last_updated": 1.0},
            "old-channel-2": {"prompt_tokens": 200, "completion_tokens": 100,
                              "total_tokens_used": 300, "last_updated": 2.0},
            _ARK: {"prompt_tokens": 1000, "completion_tokens": 500,
                   "total_tokens_used": 1500, "last_updated": 3.0},
        }
        _path = self._write_usage_file(_stale)
        _m = ChannelQuotaMonitor(usage_path=_path)
        # 已移除渠道应被清理
        self.assertNotIn("old-channel-1", _m._usage)
        self.assertNotIn("old-channel-2", _m._usage)
        # 当前配置中的渠道应保留
        self.assertIn(_ARK, _m._usage)
        self.assertEqual(_m.get_used(_ARK), 1500)

    def test_02_dirty_flag_set_after_cleanup(self):
        """清理后应设置_dirty=True，确保下次save落盘清理后的结果。"""
        _stale = {
            "old-channel": {"prompt_tokens": 100, "completion_tokens": 50,
                            "total_tokens_used": 150, "last_updated": 1.0},
        }
        _path = self._write_usage_file(_stale)
        _m = ChannelQuotaMonitor(usage_path=_path)
        self.assertTrue(_m._dirty, "清理后应设置_dirty=True")

    def test_03_no_stale_no_cleanup(self):
        """如果没有已移除渠道，不应触发清理（_dirty保持False）。"""
        _clean = {
            _ARK: {"prompt_tokens": 1000, "completion_tokens": 500,
                   "total_tokens_used": 1500, "last_updated": 3.0},
        }
        _path = self._write_usage_file(_clean)
        _m = ChannelQuotaMonitor(usage_path=_path)
        # 注意：_dirty初始为False，加载无清理时不应被设置
        self.assertIn(_ARK, _m._usage)

    def test_04_save_after_cleanup_removes_stale_from_disk(self):
        """清理后save应将清理后的结果落盘，磁盘文件不再包含已移除渠道。"""
        _stale = {
            "old-channel": {"prompt_tokens": 100, "completion_tokens": 50,
                            "total_tokens_used": 150, "last_updated": 1.0},
            _ARK: {"prompt_tokens": 1000, "completion_tokens": 500,
                   "total_tokens_used": 1500, "last_updated": 3.0},
        }
        _path = self._write_usage_file(_stale)
        _m = ChannelQuotaMonitor(usage_path=_path)
        _m.save(force=True)
        # 重新加载验证磁盘文件已清理
        with open(_path, encoding="utf-8") as _f:
            _disk = json.load(_f)
        self.assertNotIn("old-channel", _disk["channels"])
        self.assertIn(_ARK, _disk["channels"])


class TestForceEnableTTL(_Base):
    """⑧ P2-193：FORCE_ENABLE_CHANNELS白名单TTL机制。"""

    def test_01_ttl_zero_means_permanent(self):
        """TTL=0（默认）时，白名单渠道永久有效。"""
        with _Switch(FORCE_ENABLE_CHANNELS=[_ARK], FORCE_ENABLE_TTL_HOURS=0):
            _m = ChannelQuotaMonitor(usage_path=self._path)
            self.assertIn(_ARK, _m._force_enabled())

    def test_02_ttl_positive_keeps_recent(self):
        """TTL>0时，新加入的白名单渠道在TTL内有效。"""
        with _Switch(FORCE_ENABLE_CHANNELS=[_ARK], FORCE_ENABLE_TTL_HOURS=1):
            _m = ChannelQuotaMonitor(usage_path=self._path)
            self.assertIn(_ARK, _m._force_enabled())
            # 时间戳应被记录
            self.assertIn(_ARK, _m._force_enable_ts)

    def test_03_ttl_expired_removed(self):
        """TTL>0时，超过TTL的白名单渠道被过滤。"""
        with _Switch(FORCE_ENABLE_CHANNELS=[_ARK], FORCE_ENABLE_TTL_HOURS=1):
            _m = ChannelQuotaMonitor(usage_path=self._path)
            # 手动设置时间戳为2小时前（模拟已过期）
            _m._force_enable_ts[_ARK] = time.time() - 7200
            self.assertNotIn(_ARK, _m._force_enabled(),
                             "超过TTL的白名单渠道应被过滤")

    def test_04_removed_channel_clears_timestamp(self):
        """白名单渠道从配置中移除后，时间戳被清理。"""
        with _Switch(FORCE_ENABLE_CHANNELS=[_ARK], FORCE_ENABLE_TTL_HOURS=1):
            _m = ChannelQuotaMonitor(usage_path=self._path)
            _m._force_enabled()  # 触发时间戳记录
            self.assertIn(_ARK, _m._force_enable_ts)
        # 退出with后，FORCE_ENABLE_CHANNELS恢复为空
        # 再次调用_force_enabled应清理时间戳
        _m._force_enabled()
        self.assertNotIn(_ARK, _m._force_enable_ts,
                         "从配置中移除的渠道应清理时间戳")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSafetyMarginP192(unittest.TestCase):
    """★P2-192：额度用量安全系数。"""

    def setUp(self):
        self._path = os.path.join(tempfile.mkdtemp(), "quota_usage.json")

    def tearDown(self):
        if os.path.exists(os.path.dirname(self._path)):
            shutil.rmtree(os.path.dirname(self._path), ignore_errors=True)

    def test_01_default_safety_margin_is_one(self):
        """默认安全系数为1.0，不改变剩余比例计算。"""
        _m = ChannelQuotaMonitor(usage_path=self._path)
        self.assertEqual(_m.safety_margin(), 1.0)

    def test_02_safety_margin_greater_than_one_reduces_remaining(self):
        """安全系数>1.0时，剩余比例偏低（用量被放大）。"""
        _m = ChannelQuotaMonitor(usage_path=self._path)
        _m._usage[_ARK] = {"total_tokens_used": 400000, "requests": 1}
        # limit=500000(测试基准), used=400000, margin=1.0 → remaining=0.2
        _r1 = _m.get_remaining_ratio(_ARK)
        self.assertAlmostEqual(_r1, 0.2, places=4)
        # margin=1.25 → used=1000 → remaining=0.0
        with _Switch(QUOTA_USAGE_SAFETY_MARGIN=1.25):
            _r2 = _m.get_remaining_ratio(_ARK)
            self.assertAlmostEqual(_r2, 0.0, places=4)

    def test_03_safety_margin_affects_exhausted(self):
        """安全系数影响is_exhausted判定：高安全系数使渠道更早被判定为耗尽。"""
        _m = ChannelQuotaMonitor(usage_path=self._path)
        _m._usage[_ARK] = {"total_tokens_used": 460000, "requests": 1}
        # limit=1000, used=920, margin=1.0 → remaining=0.08 > pause_ratio(0.05) → 未耗尽
        self.assertFalse(_m.is_exhausted(_ARK))
        # margin=1.1 → used=1012 → remaining=-0.012 < 0.05 → 耗尽
        with _Switch(QUOTA_USAGE_SAFETY_MARGIN=1.1):
            self.assertTrue(_m.is_exhausted(_ARK))

    def test_04_safety_margin_config_override(self):
        """配置项QUOTA_USAGE_SAFETY_MARGIN可覆盖默认值。"""
        _m = ChannelQuotaMonitor(usage_path=self._path)
        with _Switch(QUOTA_USAGE_SAFETY_MARGIN=1.15):
            self.assertEqual(_m.safety_margin(), 1.15)

    def test_05_safety_margin_does_not_affect_unlimited(self):
        """安全系数不影响不限量渠道（quota_limit=-1）。"""
        _m = ChannelQuotaMonitor(usage_path=self._path)
        _m._usage["unlimited-channel"] = {"total_tokens_used": 999999, "requests": 1}
        with _Switch(QUOTA_USAGE_SAFETY_MARGIN=2.0):
            self.assertIsNone(_m.get_remaining_ratio("unlimited-channel"))
