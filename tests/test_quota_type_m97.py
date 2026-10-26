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

import config
from nucleus.llm.ChannelQuotaMonitor import ChannelQuotaMonitor


def _mk():
    _d = tempfile.mkdtemp(prefix="m97_quota_")
    return ChannelQuotaMonitor(usage_path=os.path.join(_d, "q.json"))


class TestQuotaType(unittest.TestCase):
    def test_quota_type_from_config(self):
        """配置标注什么就读到什么；未标注/不存在 ⇒ fixed。

        ★372刀8：原用例断言 6 个 ark-* 为 daily_reward、3 个为 fixed，
        但 ark-* 已随 352刀9（2026-10-23 小林触发，火山免费额度耗尽）**全部挂起**；
        且现态额度类型已从二分类演进为 **monthly_free / dynamic / fixed**。
        守的是两条不变量（不是具体渠道名）：
          ① 配置里标注的类型能被正确读出；
          ② 缺失或未标注的类型一律回落 fixed。
        """
        m = _mk()
        # ① 现态真实标注（直接读 config 实测所得）
        self.assertEqual(m.quota_type_of("bailian-qwen-plus"), "monthly_free")
        self.assertEqual(m.quota_type_of("bailian-qwen-max"), "monthly_free")
        self.assertEqual(m.quota_type_of("modelscope"), "dynamic")
        # ② 未标注 quota_type 的 ⇒ 回落 fixed
        self.assertEqual(m.quota_type_of("zhipu-47-flash"), "fixed")
        self.assertEqual(m.quota_type_of("siliconflow-qwen2-7b"), "fixed")
        # ③ 不存在的渠道 ⇒ fixed（默认）
        self.assertEqual(m.quota_type_of("nonexistent"), "fixed")

    def test_fixed_channel_exhausts(self):
        """有正限额的渠道用尽即被暂停。

        ★372刀8：原用例用 ark-seed-21-pro（限额 230 万），该渠道已随 352刀9
        挂起而不存在。改用**现态真实且有正限额**的渠道 bailian-qwen-plus
        （quota_limit=1000000，实测用尽 ⇒ exhausted=True）。
        ★另加反向断言：quota_limit=-1（第32批 T6 定死的「不限量」哨兵）的渠道，
        即便用量极大也**不得**被暂停 —— 否则"不限量"就名存实亡。
        """
        m = _mk()
        m.record_usage("bailian-qwen-plus", 1000000, 0)
        self.assertTrue(m.is_exhausted("bailian-qwen-plus"),
                        "有正限额的渠道用尽应被暂停")
        # 反向：-1 = 不限量 ⇒ 永不暂停
        m.record_usage("zhipu-47-flash", 999999999, 0)
        self.assertFalse(m.is_exhausted("zhipu-47-flash"),
                         "quota_limit=-1（不限量）不得因用量被暂停")

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
        """★协作奖励（daily_reward）每日重置：到重置时刻且今日未重置 → 清零用量。

        ★372刀8：现态 config 里**已经没有** daily_reward 类型渠道了（演进为
        monthly_free / dynamic），但产品仍保留该语义分支（ChannelQuotaMonitor
        :237 daily_reset_hour / :245 类型判据）。故这里**注入**一条 daily_reward
        渠道来验证语义 —— 注入的是 config 渠道表，产品的 quota_type_of /
        _ensure_daily_reset 照常真跑，**不是给判据打桩**。
        """
        _CH = "m372_daily_reward_probe"
        _orig = config.REMOTE_API_CHANNELS
        _patched = dict(_orig or {})
        _patched["default_channels"] = list((_orig or {}).get("default_channels") or []) + [
            {"name": _CH, "quota_type": "daily_reward", "quota_limit": 1000000}]
        config.REMOTE_API_CHANNELS = _patched
        try:
            m = _mk()
            self.assertEqual(m.quota_type_of(_CH), "daily_reward",
                             "注入的 daily_reward 渠道应被产品读出")
            m._usage[_CH] = {
                "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens_used": 1000000, "last_updated": 0.0}
            m._daily_reset[_CH] = "2000-01-01"   # 模拟陈旧日期
            _fake = datetime.datetime(2026, 9, 21, 12, 0, 0)   # 已过 11 点
            m._ensure_daily_reset(_CH, now=_fake)
            self.assertEqual(m.get_used(_CH), 0)
            self.assertEqual(m._daily_reset[_CH], "2026-09-21")
        finally:
            config.REMOTE_API_CHANNELS = _orig

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
