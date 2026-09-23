# -*- coding: utf-8 -*-
"""第97批 T-97c 门控单测：额度类型（fixed / daily_reward）+ 每日 11 点补充。

覆盖：
  * quota_type 从 config 正确读取（协作奖励= daily_reward，固定/缺失= fixed）
  * 固定额度用完即止 → is_exhausted 为真
  * 协作奖励（daily_reward）超量也永不暂停、入池不降优先级
  * 协作奖励每日本地 QUOTA_DAILY_RESET_HOUR(默认11) 点后自动清零用量（幂等）
  * 固定额度渠道不做每日重置

隔离：ChannelQuotaMonitor 用显式临时 usage_path，不污染生产 data/channel_quota_usage.json。
"""
import datetime
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.llm.ChannelQuotaMonitor import ChannelQuotaMonitor  # noqa: E402


def _mk():
    _d = tempfile.mkdtemp(prefix="m97_quota_")
    return ChannelQuotaMonitor(usage_path=os.path.join(_d, "q.json"))


class TestQuotaType(unittest.TestCase):
    def test_quota_type_from_config(self):
        m = _mk()
        # ★标注的协作奖励渠道
        self.assertEqual(m.quota_type_of("ark-ds-v4-flash"), "daily_reward")
        self.assertEqual(m.quota_type_of("ark-seed-evolving"), "daily_reward")
        self.assertEqual(m.quota_type_of("ark-ds-v4-pro"), "daily_reward")
        self.assertEqual(m.quota_type_of("ark-glm-5.2"), "daily_reward")
        self.assertEqual(m.quota_type_of("ark-seed-character"), "daily_reward")
        self.assertEqual(m.quota_type_of("ark-seed-21-turbo"), "daily_reward")
        # 固定额度（明确标注 / 默认）
        self.assertEqual(m.quota_type_of("ark-seed-21-pro"), "fixed")
        self.assertEqual(m.quota_type_of("ark-ds-v4.1-flash"), "fixed")
        self.assertEqual(m.quota_type_of("ark-glm-5.3-flash"), "fixed")
        self.assertEqual(m.quota_type_of("nonexistent"), "fixed")

    def test_fixed_channel_exhausts(self):
        """固定额度（ark-seed-21-pro 限额 230 万）用尽即被暂停。"""
        m = _mk()
        m.record_usage("ark-seed-21-pro", 2300000, 0)
        self.assertTrue(m.is_exhausted("ark-seed-21-pro"))

    def test_daily_reward_never_exhausted(self):
        """协作奖励（daily_reward）即便远超限额也永不因额度暂停。"""
        m = _mk()
        m.record_usage("ark-ds-v4-flash", 999999999, 0)
        self.assertFalse(m.is_exhausted("ark-ds-v4-flash"))

    def test_daily_reward_not_degraded_in_pool(self):
        """协作奖励入池不按用量降优先级（priority 不被 +3）。"""
        m = _mk()
        m.record_usage("ark-ds-v4-flash", 999999999, 0)
        _out = m.apply_to_channels([
            {"name": "ark-ds-v4-flash", "priority": 5,
             "api_url": "https://x", "model": "y"}])
        self.assertEqual(len(_out), 1)
        self.assertEqual(_out[0]["priority"], 5, "协作奖励不应降优先级")

    def test_daily_reset_clears_usage(self):
        """★协作奖励每日重置：到重置时刻且今日未重置 → 清零用量。"""
        m = _mk()
        m._usage["ark-ds-v4-flash"] = {
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens_used": 1000000, "last_updated": 0.0}
        m._daily_reset["ark-ds-v4-flash"] = "2000-01-01"  # 模拟陈旧日期
        _fake = datetime.datetime(2026, 9, 21, 12, 0, 0)  # 已过 11 点
        m._ensure_daily_reset("ark-ds-v4-flash", now=_fake)
        self.assertEqual(m.get_used("ark-ds-v4-flash"), 0)
        self.assertEqual(m._daily_reset["ark-ds-v4-flash"], "2026-09-21")

    def test_daily_reset_idempotent_same_day(self):
        """同日重复触发不重复清零（幂等）。"""
        m = _mk()
        m._usage["ark-ds-v4-flash"] = {
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens_used": 1000000, "last_updated": 0.0}
        m._daily_reset["ark-ds-v4-flash"] = "2026-09-21"
        _fake = datetime.datetime(2026, 9, 21, 15, 0, 0)
        m._ensure_daily_reset("ark-ds-v4-flash", now=_fake)  # 今日已重置
        self.assertEqual(m.get_used("ark-ds-v4-flash"), 1000000, "同日内不应重置")

    def test_fixed_reset_noop(self):
        """固定额度渠道不做每日重置（用量保留）。"""
        m = _mk()
        m._usage["ark-seed-21-pro"] = {
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens_used": 100000, "last_updated": 0.0}
        m._daily_reset["ark-seed-21-pro"] = "2000-01-01"
        _fake = datetime.datetime(2026, 9, 21, 12, 0, 0)
        m._ensure_daily_reset("ark-seed-21-pro", now=_fake)
        self.assertEqual(m.get_used("ark-seed-21-pro"), 100000)

    def test_daily_reset_persisted(self):
        """每日重置日期随用量落盘（重启可恢复）。"""
        _d = tempfile.mkdtemp(prefix="m97_qpersist_")
        _p = os.path.join(_d, "q.json")
        m = ChannelQuotaMonitor(usage_path=_p)
        m._usage["ark-ds-v4-flash"] = {
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens_used": 500000, "last_updated": 0.0}
        m._daily_reset["ark-ds-v4-flash"] = "2026-09-21"
        m.save(force=True)
        m2 = ChannelQuotaMonitor(usage_path=_p)
        self.assertEqual(m2._daily_reset.get("ark-ds-v4-flash"), "2026-09-21")


if __name__ == "__main__":
    unittest.main(verbosity=2)
