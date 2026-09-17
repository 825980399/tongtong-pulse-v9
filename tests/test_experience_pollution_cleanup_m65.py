# -*- coding: utf-8 -*-
"""主线第65批 T1/P1：经验库污染专项修复 门控单测（8例）。

覆盖：分类收窄（白名单跳过 / SERP 样板高覆盖判 write_side / 普通文本不误标 /
       legacy 模板句沿用旧判据）/ 清理闭环（dry_run 计数 + 非 dry_run 改写 &
       is_cleaned 不再恒 False + 隔离文件落盘）/ 历史重评（batch=56）/ 配置项。

隔离：ExperiencePool 用显式 base_dir（临时目录），写盘守卫放行；生产清理须在停机窗口执行。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                     # noqa: E402
from nucleus.data import experience_cleanup as _ec                # noqa: E402
from nucleus.mnemosyne.experience_pool import ExperiencePool      # noqa: E402

_TPL = "我曾因维持系统平衡而行动，获得了cognitive奖赏，感受到平静"
# 仅由 SERP 样板 token 构成 → 覆盖度 1.0（≥0.8 阈值）
_SERP_ALL = "搜索结果相关搜索广告赞助商链接百度一下相关推荐"
_SERP_LOW = "搜索结果" + "正常内容" * 20  # 仅 1 个样板 token，覆盖度极低 → 应恢复


def _e(**kw):
    d = {"id": kw.pop("id", "e"), "summary": "干净内容" * 8, "timestamp": 1.0}
    d.update(kw)
    return d


class _CfgSwitch:
    def __init__(self, **kw):
        self._kw, self._old = kw, {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


# ============================================================ 分类收窄
class TestClassifyNarrow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m65_t1c_")
        self.p = ExperiencePool(base_dir=self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_10_whitelist_source_skipped(self):
        """白名单来源（code_learning）即便含 SERP 样板也不判污染。"""
        _exp = _e(source="code_learning", summary=_SERP_ALL)
        self.assertEqual(self.p._serp_classify(_exp), "",
                         "白名单来源应直接跳过分类")

    def test_11_serp_boilerplate_high_coverage(self):
        """全 SERP 样板文本 → 收窄判据命中 write_side（覆盖度≈1.0）。"""
        _exp = _e(summary=_SERP_ALL)
        self.assertEqual(self.p._serp_classify(_exp), _ec.CLASS_WRITE_SIDE)

    def test_12_normal_text_not_polluted(self):
        """正常文本不得被宽口径误标（第56批 82.6% 误标根因）。"""
        _exp = _e(summary="今天学习了新的算法设计模式并做了复盘")
        self.assertEqual(self.p._serp_classify(_exp), "")

    def test_13_legacy_template_kept(self):
        """legacy 模板句沿用既有判据（CLASS_TEMPLATE），不丢失旧分类。"""
        _exp = _e(summary=_TPL)
        self.assertEqual(self.p._serp_classify(_exp), _ec.CLASS_TEMPLATE)

    def test_14_low_coverage_restored_class(self):
        """仅 1 个样板 token 且覆盖度低 → 不判（交给清理闭环恢复）。"""
        _exp = _e(summary=_SERP_LOW)
        self.assertEqual(self.p._serp_classify(_exp), "")


# ============================================================ 清理闭环
class TestCleanupLoop(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m65_t1l_")
        self.p = ExperiencePool(base_dir=self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _seed(self, high, low):
        self.p._experiences = [
            _e(id="q", polluted=True, is_cleaned=False, summary=_SERP_ALL),   # 高覆盖 → 隔离
            _e(id="r", polluted=True, is_cleaned=False, summary=_SERP_LOW),   # 低覆盖 → 恢复
            _e(id="c", polluted=False),                                       # 干净 → 不动
        ]

    def test_20_dry_run_counts(self):
        self._seed(True, True)
        _res = self.p.run_pollution_cleanup(dry_run=True)
        self.assertEqual(_res["quarantined"], 1)
        self.assertEqual(_res["restored"], 1)
        self.assertEqual(_res["processed"], 2)
        # dry_run 不得改写内存态
        _still = [x for x in self.p._experiences if x["id"] in ("q", "r")]
        self.assertTrue(all(x.get("is_cleaned") is False for x in _still))

    def test_21_is_cleaned_no_longer_false(self):
        """★核心：清理后被处理记录的 is_cleaned 不再恒 False（改为 True）。

        隔离记录（高覆盖）移出活跃池、转入 _quarantine 文件（可恢复）；
        恢复记录（低覆盖）留在活跃池并取消污染标记。
        """
        self._seed(True, True)
        _res = self.p.run_pollution_cleanup(dry_run=False)
        self.assertEqual(_res["quarantined"] + _res["restored"], 2)
        _ids = {x["id"] for x in self.p._experiences}
        self.assertNotIn("q", _ids, "隔离记录须移出活跃池（转入 _quarantine）")
        self.assertIn("r", _ids)
        self.assertIn("c", _ids)
        _r = [x for x in self.p._experiences if x["id"] == "r"][0]
        self.assertIs(_r["is_cleaned"], True, "恢复记录 is_cleaned 须置 True")
        self.assertIs(_r["polluted"], False, "恢复记录须取消污染标记")
        # 隔离文件落盘（显式 base_dir，写盘守卫放行）
        _qdir = os.path.join(self.d, "_quarantine")
        self.assertTrue(os.path.isdir(_qdir), "隔离目录须创建")
        _files = [f for f in os.listdir(_qdir) if f.startswith("quarantine_")]
        self.assertEqual(len(_files), 1, "应写出 1 个隔离文件")
        _qdoc = json.load(io.open(os.path.join(_qdir, _files[0]), encoding="utf-8"))
        _q = [x for x in _qdoc if x.get("id") == "q"]
        self.assertEqual(len(_q), 1)
        self.assertIs(_q[0]["is_cleaned"], True, "隔离记录 is_cleaned 须置 True")
        self.assertTrue(_q[0]["cleanup_action"].startswith("quarantined"),
                        "隔离记录 cleanup_action 须以 quarantined 开头（高/中覆盖分级）")

    def test_22_switch_off_noop(self):
        self._seed(True, True)
        with _CfgSwitch(ENABLE_EXPERIENCE_POLLUTION_CLEANUP=False):
            _res = self.p.run_pollution_cleanup(dry_run=False)
        self.assertEqual(_res["processed"], 0)
        self.assertEqual(_res["quarantined"], 0)
        self.assertEqual(_res["restored"], 0)


# ============================================================ 历史重评
class TestReevalHistory(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m65_t1h_")
        self.p = ExperiencePool(base_dir=self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_30_reevaluate_batch56(self):
        """用收窄判据重评 cleanup_batch==56 的记录：仍命中→保留，否则→恢复。"""
        self.p._experiences = [
            _e(id="hit", cleanup_batch=56, polluted=True, is_cleaned=False, summary=_SERP_ALL),
            _e(id="miss", cleanup_batch=56, polluted=True, is_cleaned=False, summary="复盘确认对话质量良好"),
            _e(id="other", cleanup_batch=55, polluted=True, is_cleaned=False, summary=_SERP_ALL),
        ]
        _res = self.p.reevaluate_history_batch(batch_no=56, dry_run=False)
        self.assertEqual(_res["total"], 2)
        self.assertEqual(_res["retained"], 1)
        self.assertEqual(_res["restored"], 1)
        _by_id = {x["id"]: x for x in self.p._experiences}
        self.assertIs(_by_id["miss"]["polluted"], False, "不再命中窄判据须恢复")
        self.assertIs(_by_id["miss"]["is_cleaned"], True)
        # batch=55 不受影响
        self.assertIs(_by_id["other"]["is_cleaned"], False)


# ============================================================ 配置项
class TestConfigItems(unittest.TestCase):
    def test_40_config_items_present(self):
        self.assertIs(getattr(config, "ENABLE_EXPERIENCE_POLLUTION_CLEANUP", None), True)
        self.assertIsInstance(getattr(config, "EXPERIENCE_POLLUTION_CLEANUP_INTERVAL", None), (int, float))
        self.assertIsInstance(getattr(config, "EXPERIENCE_POLLUTION_HIGH_CONFIDENCE", None), (int, float))
        self.assertIsInstance(getattr(config, "EXPERIENCE_POLLUTION_MEDIUM_CONFIDENCE", None), (int, float))
        _wl = getattr(config, "EXPERIENCE_POLLUTION_WHITELIST_SOURCES", None)
        self.assertIsInstance(_wl, (list, tuple))
        self.assertIn("code_learning", _wl)

    def test_41_cleanup_enabled_default(self):
        self.p = ExperiencePool(base_dir=tempfile.mkdtemp(prefix="m65_t1cfg_"))
        self.assertTrue(self.p._cleanup_enabled())
        shutil.rmtree(self.p._pool_file and os.path.dirname(self.p._pool_file), ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
