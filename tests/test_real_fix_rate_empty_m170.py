"""170批 C3 门控单测：real_fix_rate 空集/不可判定统一返 None。

验收口径（施工任务书 C3 / 烛微 170）：
- 空集输入（[] 或无 dict 元素）-> real_fix_rate 返回 None（不再虚报 0.0%）。
- 非空但无可判定样本（problem_fixed 均为 None）-> 返回 None（杜绝"空补丁池刷 P0"）。
- 真有可判定样本且全部 False -> 仍返回 float 0.0（真 0% 应告警，区别于 None）。
- 真有可判定样本且全部 True -> 返回 1.0。

注：既有 test_patch_verification_split_knife3_m163.py 仅通过 backfill() 间接覆盖，
本件补齐对 real_fix_rate() 函数的直接覆盖。
"""
import unittest

from nucleus.evolution.patch_verification_split import real_fix_rate


class TestRealFixRateEmptyM170(unittest.TestCase):
    def test_empty_list_returns_none(self):
        self.assertIsNone(real_fix_rate([]))

    def test_nonempty_no_dict_returns_none(self):
        # 命中 :302-303 分支（_n == 0），改后返 None
        self.assertIsNone(real_fix_rate([1, "x", None]))

    def test_nonempty_all_unverifiable_returns_none(self):
        # 无可判定样本（problem_fixed 恒 None）-> 命中 :318 分支，改后返 None
        ps = [{"verification": {"passed": False}}]
        self.assertIsNone(real_fix_rate(ps))

    def test_genuine_zero_returns_float_zero(self):
        # 真有可判定样本且全部 False -> 仍是 float 0.0（真 0% 应告警，区别于 None）
        ps = [{"verification": {"passed": False},
               "baseline_errors": 5, "post_apply_errors": 5}]
        r = real_fix_rate(ps)
        self.assertIsInstance(r, float)
        self.assertEqual(r, 0.0)

    def test_genuine_all_fixed_returns_one(self):
        ps = [{"verification": {"passed": False},
               "baseline_errors": 5, "post_apply_errors": 0}]
        self.assertEqual(real_fix_rate(ps), 1.0)


if __name__ == "__main__":
    unittest.main()
