# -*- coding: utf-8 -*-
"""第49批 T2 门控测试：P2-328 双腿学习治理（去重/过滤/反馈/降频）

覆盖任务书四项修复方向，全部开关双向门控。
"""
import io
import os
import sys
import threading
import unittest
from collections import deque

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                     # noqa: E402
from organs.motor.PulseLegs import PulseLegs      # noqa: E402

_LS = io.open(os.path.join(_ROOT, "organs/motor/PulseLegs.py"),
              encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def _bare():
    """轻量实例（项目惯例：__new__ 绕 __init__ 只挂被测状态）。"""
    l = PulseLegs.__new__(PulseLegs)
    l.organ_name = "双腿"
    l._learn_queue = []
    l._learn_queue_lock = threading.Lock()
    l._learn_active_directions = set()
    l._m49_recent_topics = deque(maxlen=32)
    l._m49_skipped_dup = 0
    l._m49_skipped_irrelevant = 0
    l._m49_digest_rejected = 0
    l._m49_digest_accepted = 0
    l._m49_stomach_ref = None
    l._m49_feedback_seen = set()
    l._log = lambda level, msg: None
    return l


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


class _FakeStomach:
    def __init__(self, ring=None):
        self._rejected_digestion_ring = deque(ring or [], maxlen=50)


# ============================================================ D 降频
class TestLearningInterval(unittest.TestCase):
    def test_10_config_values(self):
        self.assertEqual(config.LEARNING_INTERVAL_SECONDS, 300)
        self.assertEqual(config.LEARNING_FAST_INTERVAL_SECONDS, 45)
        self.assertEqual(config.LEARNING_SLOW_INTERVAL_SECONDS, 600)

    def test_11_init_reads_config(self):
        """__init__ 必须从 config 读取（而非硬编码 90）。"""
        self.assertIn('getattr(\n                _m49_cfg, "LEARNING_INTERVAL_SECONDS"',
                      _LS)
        i = _LS.find("self._learn_interval = 90.0")
        self.assertGreater(i, 0, "应保留默认值 90 作为回退")
        j = _LS.find('getattr(\n                _m49_cfg, "LEARNING_INTERVAL_SECONDS', i)
        self.assertGreater(j, i, "默认值应先行、配置随后覆盖（配置优先顺序）")


# ============================================================ A 去重
class TestTopicDedup(unittest.TestCase):
    _T = "自我优化: 通用领域 - 将规则推理的成功模式扩展到更多领域"

    def test_20_exact_duplicate_skipped(self):
        l = _bare()
        l._add_learn_task(self._T)
        n = len(l._learn_queue)
        l._add_learn_task(self._T)
        self.assertEqual(len(l._learn_queue), n)
        self.assertEqual(l._m49_skipped_dup, 1)

    def test_21_near_duplicate_skipped(self):
        """★核心：相似度 > 0.8 即跳过（原实现只有精确匹配）。"""
        l = _bare()
        l._add_learn_task(self._T)
        l._add_learn_task(self._T[:-2] + "更多范围")
        self.assertEqual(l._m49_skipped_dup, 1, "近似主题应被去重")

    def test_22_distinct_topic_admitted(self):
        l = _bare()
        l._add_learn_task(self._T)
        l._add_learn_task("分布式共识算法的容错边界分析")
        self.assertEqual(len(l._learn_queue), 2)

    def test_23_switch_off_no_dedup(self):
        """零回归：关闭开关 → 近似主题不再被去重。"""
        l = _bare()
        with _CfgSwitch(ENABLE_LEARNING_TOPIC_DEDUP=False):
            l._add_learn_task(self._T)
            l._add_learn_task(self._T[:-2] + "更多范围")
        self.assertEqual(l._m49_skipped_dup, 0)

    def test_24_window_limits_scope(self):
        """窗口外的旧主题不应参与去重。"""
        l = _bare()
        l._add_learn_task(self._T)
        with _CfgSwitch(LEARNING_TOPIC_DEDUP_WINDOW=0):
            l._add_learn_task(self._T[:-2] + "更多范围")
        # 窗口 0 → 无历史可比 → 不去重
        self.assertEqual(l._m49_skipped_dup, 0)

    def test_25_recent_topics_recorded(self):
        l = _bare()
        l._add_learn_task("知识图谱的增量构建")
        self.assertIn("知识图谱的增量构建", list(l._m49_recent_topics))


# ============================================================ B 相关性过滤
class TestTopicFilter(unittest.TestCase):
    def test_30_dialog_fragments_rejected(self):
        """★实测污染样本：`我 是`（用户输入被拆成单字）。"""
        l = _bare()
        for bad in ("我 是", "你好 未来的"):
            l._add_learn_task(bad)
        self.assertEqual(l._m49_skipped_irrelevant, 2)
        self.assertEqual(len(l._learn_queue), 0)

    def test_31_too_short_rejected(self):
        l = _bare()
        l._add_learn_task("在")
        self.assertEqual(l._m49_skipped_irrelevant, 1)

    def test_32_dialog_cue_rejected(self):
        l = _bare()
        l._add_learn_task("谢谢")
        self.assertEqual(l._m49_skipped_irrelevant, 1)

    def test_33_tech_topic_admitted(self):
        l = _bare()
        l._add_learn_task("知识图谱的增量构建与一致性维护")
        self.assertEqual(l._m49_skipped_irrelevant, 0)
        self.assertEqual(len(l._learn_queue), 1)

    def test_34_switch_off_admits_all(self):
        l = _bare()
        with _CfgSwitch(ENABLE_LEARNING_TOPIC_FILTER=False):
            l._add_learn_task("我 是")
        self.assertEqual(l._m49_skipped_irrelevant, 0)

    def test_35_judgement_is_deterministic(self):
        """判据必须纯粹（同输入同输出），不得依赖随机。"""
        l = _bare()
        a = l._m49_is_relevant_topic("我 是")
        b = l._m49_is_relevant_topic("我 是")
        self.assertEqual(a, b)
        self.assertFalse(a)


# ============================================================ B2 域名黑名单
class TestDomainBlocklist(unittest.TestCase):
    def test_40_blocklist_configured(self):
        for dom in ("guancha.cn", "tophub.today", "thepaper.cn"):
            self.assertIn(dom, config.LEARNING_DOMAIN_BLOCKLIST)

    def test_41_fetch_url_checks_blocklist(self):
        self.assertIn("LEARNING_DOMAIN_BLOCKLIST", _LS)
        i = _LS.find("LEARNING_DOMAIN_BLOCKLIST")
        window = _LS[i - 600:i + 300]
        self.assertIn("return None", window, "命中黑名单应拒绝抓取")


# ============================================================ C 入库反馈
class TestDigestFeedback(unittest.TestCase):
    def _ring(self, recs):
        return _FakeStomach(recs)

    def test_50_rejection_attributed(self):
        l = _bare()
        l.set_stomach(self._ring([{
            "ts": 1.0, "source_organ": "双腿",
            "trigger_reason": "active_learn:search:技术架构", "reason": "关键词不足",
        }]))
        self.assertEqual(l._m49_sync_digest_feedback(), 1)
        self.assertEqual(l._m49_digest_rejected, 1)

    def test_51_idempotent(self):
        l = _bare()
        l.set_stomach(self._ring([{
            "ts": 1.0, "source_organ": "双腿",
            "trigger_reason": "active_learn:search:x", "reason": "r",
        }]))
        l._m49_sync_digest_feedback()
        self.assertEqual(l._m49_sync_digest_feedback(), 0, "同一记录不得重复计数")

    def test_52_other_sources_ignored(self):
        l = _bare()
        l.set_stomach(self._ring([
            {"ts": 1.0, "source_organ": "潜意识",
             "trigger_reason": "active_learn:search:x", "reason": "r"},
            {"ts": 2.0, "source_organ": "双腿",
             "trigger_reason": "file_digest", "reason": "r"},
        ]))
        self.assertEqual(l._m49_sync_digest_feedback(), 0)

    def test_53_no_stomach_safe(self):
        l = _bare()
        self.assertEqual(l._m49_sync_digest_feedback(), 0)

    def test_54_empty_ring_safe(self):
        l = _bare()
        l.set_stomach(self._ring([]))
        self.assertEqual(l._m49_sync_digest_feedback(), 0)

    def test_55_switch_off_no_feedback(self):
        l = _bare()
        l.set_stomach(self._ring([{
            "ts": 1.0, "source_organ": "双腿",
            "trigger_reason": "active_learn:search:x", "reason": "r",
        }]))
        with _CfgSwitch(ENABLE_LEARNING_DIGEST_FEEDBACK=False):
            self.assertEqual(l._m49_sync_digest_feedback(), 0)

    def test_56_false_success_log_fixed(self):
        """★任务书核心诉求：被门槛拦截不得再报「学习成功」。"""
        self.assertIn("主动学习已提交消化", _LS)
        self.assertNotIn("主动学习成功: {direction}", _LS)

    def test_57_stomach_ring_is_existing_api(self):
        """★本批零改动胃：复用既有 `_rejected_digestion_ring`。"""
        p = os.path.join(_ROOT, "organs/body/PulseStomach.py")
        src = io.open(p, encoding="utf-8", errors="replace").read()
        self.assertIn("_rejected_digestion_ring", src)


# ============================================================ 接线
class TestWiring(unittest.TestCase):
    def test_60_set_stomach_defined(self):
        self.assertEqual(_LS.count("def set_stomach(self, stomach)"), 1)

    def test_61_main_injects_stomach(self):
        main = io.open(os.path.join(_ROOT, "main.py"),
                       encoding="utf-8", errors="replace").read()
        self.assertIn("callable(_m49_set)", main)
        self.assertIn('getattr(self.legs, "set_stomach", None)', main)

    def test_62_no_bare_except_pass_added(self):
        """新代码不得引入裸 except: pass（项目门禁惯例）。"""
        import re
        self.assertEqual(
            len(re.findall(r"except\s+[^\n:]+:\s*\n\s*pass\s*(\n|$)", _LS)), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
