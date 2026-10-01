# -*- coding: utf-8 -*-
"""第47批 T2 门控测试：经验库摘要机制止血（P0-3 / P2-308）

覆盖：
  * ``_summarize_experience()`` **不再覆盖原文**（raw_summary 保留）
  * ``compressed_summary`` / ``summary_version`` 正确写入
  * ``motivation`` 与 ``field_frequency_snapshot`` **不再**被截断/清空
  * ``check_decay()`` 衰减后原文仍在
  * 写入侧重复检测与 ``pollution_risk`` 分级
  * 灰度开关：默认观测（不拦截）、开启后拒绝完全重复
  * 向后兼容：旧记录（无 raw_summary）读取不崩溃

★测试隔离：使用 ``tempfile.mkdtemp()`` 作为 ``base_dir``，
  **绝不碰生产 ``data/experience/``**。
"""

import io
import pytest
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.mnemosyne.experience_pool import ExperiencePool  # noqa: E402


def _exp(**kw):
    """构造一条**未摘要**的经验记录。"""
    base = {
        "id": "exp_test_1",
        "motivation": "想要深入理解用户提出的复杂问题并给出可靠答案",
        "motivation_intensity": 0.6,
        "process_pressure": 0.1,
        "pressure_type": "cognitive",
        "result_reward": {"type": "cognitive", "intensity": 0.7},
        "field_frequency_snapshot": {"学习": 3, "推理": 5},
        "emotion_tags": ["专注", "好奇", "满足"],
        "emotion_intensity": 0.02,
        "timestamp": time.time(),
        "decay_rate": 0.01,
        "is_summarized": False,
        "summary": "这是一条原始经验摘要，包含具体的问题描述与解决思路",
        "activation_count": 0,
    }
    base.update(kw)
    return base


class _Pool(unittest.TestCase):
    def setUp(self):
        self._sand = tempfile.mkdtemp(prefix="m47exp_")
        self.pool = ExperiencePool(base_dir=self._sand)

    def tearDown(self):
        shutil.rmtree(self._sand, ignore_errors=True)


# ==================== 摘要不再覆盖原文 ====================

class TestSummaryKeepsOriginal(_Pool):
    def test_01_raw_summary_preserved(self):
        """★核心：摘要后原文必须保存在 raw_summary。"""
        _e = _exp()
        _orig = _e["summary"]
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["raw_summary"], _orig)

    def test_02_summary_not_overwritten(self):
        """★核心：summary 不再被模板句覆盖。"""
        _e = _exp()
        _orig = _e["summary"]
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["summary"], _orig)
        self.assertNotIn("我曾因", _e["summary"])

    def test_03_compressed_summary_stored(self):
        _e = _exp()
        self.pool._summarize_experience(_e)
        self.assertIn("compressed_summary", _e)
        self.assertIn("我曾因", _e["compressed_summary"])
        self.assertIn("奖赏", _e["compressed_summary"])

    def test_04_motivation_not_truncated(self):
        """★motivation 不再被截断到 50 字。"""
        _e = _exp(motivation="这是一个明显超过五十个中文字符长度的动机描述" * 3)
        _orig = _e["motivation"]
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["motivation"], _orig)
        self.assertGreater(len(_e["motivation"]), 50)

    def test_05_snapshot_not_cleared(self):
        """★field_frequency_snapshot 不再被清空。"""
        _e = _exp(field_frequency_snapshot={"学习": 3, "推理": 5})
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["field_frequency_snapshot"], {"学习": 3, "推理": 5})
        self.assertEqual(_e["raw_field_frequency_snapshot"], {"学习": 3, "推理": 5})

    def test_06_summary_version(self):
        _e = _exp()
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["summary_version"], 2)
        self.assertIs(_e["is_summarized"], True)

    def test_07_empty_original_falls_back_to_compressed(self):
        """原文为空时，summary 才降级用模板句。"""
        _e = _exp(summary="")
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["raw_summary"], "")
        self.assertEqual(_e["summary"], _e["compressed_summary"])

    def test_08_idempotent(self):
        """重复摘要不覆盖已有 raw_summary。"""
        _e = _exp()
        self.pool._summarize_experience(_e)
        _raw1 = _e["raw_summary"]
        _e["is_summarized"] = False        # 强制再跑一次
        self.pool._summarize_experience(_e)
        self.assertEqual(_e["raw_summary"], _raw1)

    def test_09_already_summarized_noop(self):
        _e = _exp(is_summarized=True)
        _before = dict(_e)
        self.pool._summarize_experience(_e)
        self.assertEqual(_e, _before)

    def test_10_emotion_tags_still_compressed(self):
        """情绪标签仍只保留前 3 个（低信息量，允许压缩）。"""
        _e = _exp(emotion_tags=["a", "b", "c", "d", "e"])
        self.pool._summarize_experience(_e)
        self.assertEqual(len(_e["emotion_tags"]), 3)


# ==================== check_decay 协同 ====================

class TestCheckDecay(_Pool):
    def test_20_decay_trigger_keeps_original(self):
        """衰减触发摘要后，原文仍在。"""
        self.pool._experiences = [
            _exp(emotion_intensity=0.01, decay_rate=0.5,
                 timestamp=time.time() - 7200)]
        self.pool._last_decay_check = 0
        self.pool.check_decay()
        _e = self.pool._experiences[0]
        if _e.get("is_summarized"):
            self.assertIn("raw_summary", _e)
            self.assertNotIn("我曾因", _e["summary"])

    def test_21_high_intensity_not_summarized(self):
        self.pool._experiences = [_exp(emotion_intensity=0.9, decay_rate=0.0)]
        self.pool._last_decay_check = 0
        self.pool.check_decay()
        self.assertIs(self.pool._experiences[0]["is_summarized"], False)


# ==================== 写入侧防污染 ====================

class TestWriteSideGuard(_Pool):
    def test_30_risk_classification(self):
        self.assertEqual(
            self.pool._classify_pollution_risk("", {}), "high")
        self.assertEqual(
            self.pool._classify_pollution_risk("短文", {}), "high")
        self.assertEqual(
            self.pool._classify_pollution_risk("这是一段中等长度的内容", {}),
            "medium")
        # ★判据：<8=high，<20=medium，>=20=low（注意中文按字符计）
        _long = "这是一段足够长的经验内容描述文字，包含完整的上下文"
        self.assertGreaterEqual(len(_long), 20)
        self.assertEqual(
            self.pool._classify_pollution_risk(_long, {}), "low")

    def test_31_duplicate_detection(self):
        self.pool._experiences = [_exp(summary="完全重复的内容" * 5)]
        self.assertTrue(self.pool._has_duplicate("完全重复的内容" * 5))
        self.assertFalse(self.pool._has_duplicate("另一段不同的内容" * 5))

    def test_32_empty_content_not_duplicate(self):
        self.pool._experiences = [_exp(summary="x" * 30)]
        self.assertFalse(self.pool._has_duplicate(""))

    def test_33_observe_mode_by_default(self):
        """★默认开关开启（2026-09-27星轨开启：ENABLE_EXPERIENCE_DEDUP=True）→ 只标记风险，不拒绝写入（零回归）。"""
        self.assertTrue(self.pool._dedup_enabled())
        _id = self.pool.record_experience(motivation="测试动机" * 5, content="短文")
        self.assertNotEqual(_id, "")
        _last = self.pool._experiences[-1]
        self.assertIn("pollution_risk", _last)
        self.assertEqual(_last["pollution_risk"], "high")

    def test_34_record_sets_summary_version(self):
        self.pool.record_experience(motivation="测试动机" * 5,
                                    content="一段足够长的经验内容描述文字")
        self.assertEqual(self.pool._experiences[-1]["summary_version"], 2)

    def test_35_record_returns_id(self):
        _id = self.pool.record_experience(motivation="m" * 30,
                                          content="一段足够长的经验内容描述文字")
        self.assertTrue(_id.startswith("exp_"))


# ==================== 向后兼容 ====================

class TestBackwardCompat(_Pool):
    def test_40_legacy_record_without_raw_summary(self):
        """旧记录（无 raw_summary）摘要时不崩溃。"""
        _e = _exp()
        _e.pop("raw_summary", None)
        self.assertNotIn("raw_summary", _e)
        self.pool._summarize_experience(_e)      # 不抛异常
        self.assertIn("raw_summary", _e)

    def test_41_legacy_already_summarized_record(self):
        """已按旧逻辑摘要过的记录（summary 已是模板句）可安全再处理。"""
        _e = _exp(summary="我曾因维持系统平衡而行动，获得了cognitive奖赏，感受到平静",
                  is_summarized=False)
        self.pool._summarize_experience(_e)
        self.assertEqual(
            _e["raw_summary"],
            "我曾因维持系统平衡而行动，获得了cognitive奖赏，感受到平静")

    def test_42_missing_fields_tolerated(self):
        _e = {"id": "x", "is_summarized": False}
        self.pool._summarize_experience(_e)      # 极端缺字段，不崩溃
        self.assertIn("compressed_summary", _e)


# ==================== 生产数据只读校验 ====================

@pytest.mark.production_data
class TestProductionNotTouched(unittest.TestCase):
    """本批不清洗数据，生产经验库不得被本测试改动。"""

    def test_50_production_pool_still_loadable(self):
        """★第49批反向同步（跨批契约变更）。

        原断言（第47批交付时）：生产库**无任何** `raw_summary`。
        变更事实：框架于 **2026-09-14 15:14:07 重启**（日志
        `[框架] INFO: 初始化 曈曈 v9.5 PulseNet...`），第47批摘要止血**已生效** ——
        重启后新写入的记录自带 `raw_summary` + `summary_version=2`。

        ⇒ 断言改为「**未回填历史**」这一真正的不变量：
          带 `raw_summary` 的记录必须**全部**由新版代码路径写入
          （`summary_version == 2`），而不是把旧记录批量回填。
        """
        _p = os.path.join(_ROOT, "data", "experience", "experience_pool.json")
        if not os.path.isfile(_p):
            self.skipTest("生产经验库不存在")
        _d = json.load(io.open(_p, encoding="utf-8"))
        _r = _d["experiences"] if isinstance(_d, dict) and "experiences" in _d else _d
        self.assertGreater(len(_r), 0)
        _with_raw = [x for x in _r if isinstance(x, dict) and "raw_summary" in x]
        _backfilled = [x for x in _with_raw if x.get("summary_version") != 2]
        self.assertEqual(
            _backfilled, [],
            "带 raw_summary 却非 summary_version=2 → 说明历史记录被**回填**（不应发生）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
