# -*- coding: utf-8 -*-
"""★第159批 上B 刀C（T-身份种子-1）防回归单测（隔离标记 m159b）。

钉住三件事（任务书 3.3 判据 1 / 3）：
  1. 判定大小写对齐：期望词含大写占位符（<SELF_NAME> / <CREATOR> /
     <CREATOR_DAUGHTER>）时，仍须命中**大写原样**的 node.keywords
     （修前 ``rkw.lower() in nkw`` 恒 MISS → 恒假 degraded 误报）。
  2. 离线复算：同一五行期望表 + 含占位符的种子节点 → MISS 行数 3 → 0，
     status == "complete"。
  3. ``_load_core_identity_keywords()`` 被调用（monkeypatch 计数，封死活代码，
     防止刀C② 接线被回退成「只留硬编码」）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from unittest import mock
except ImportError:  # pragma: no cover
    import mock  # type: ignore

import config  # noqa: E402
from nucleus._silent_except import silent_exc  # noqa: E402
from organs.identity.PulseSelfAwareness import PulseSelfAwareness  # noqa: E402


class _FakeNode:
    """最小节点替身：只需 keywords。"""

    def __init__(self, keywords):
        self.keywords = list(keywords)


class _FakePool:
    """最小节点池替身：只提供 _on_check_identity 用到的 query()。"""

    def __init__(self, nodes):
        self._nodes = list(nodes)

    def query(self, **_kwargs):
        return list(self._nodes)


def _seed_keyword_sets():
    """与 config.SEED_MEMORIES 前 5 条同源的期望表（刀C② 接线后的唯一真值源）。"""
    return [list(s.get("keywords") or [])
            for s in (getattr(config, "SEED_MEMORIES", None) or [])[:5]]


class TestIdentitySeedJudge(unittest.TestCase):
    """刀C①：判定大小写对齐 + 离线复算 MISS 3 → 0。"""

    def setUp(self):
        self.kw_sets = _seed_keyword_sets()
        # 节点 keywords 保持**大写占位符原样**（真实快照形态，非 lower）
        self.nodes = [_FakeNode(kws) for kws in self.kw_sets]
        # 隔离：事件总线/日志不依赖外部环境
        try:
            from nucleus.events.EventBus import reset_event_bus
            reset_event_bus()
        except Exception as _e:
            # ★cw2「未跟踪盲区」：新 .py 的 except 必须走 silent_exc（禁裸 pass）
            silent_exc(_e, where="test_identity_seed_m159b::setUp reset_event_bus")

    def _run_check(self):
        """构造最轻替身 organ（绕过重 __init__）并跑一次身份校验。"""
        organ = PulseSelfAwareness.__new__(PulseSelfAwareness)
        organ._check_count = 0
        organ._core_identity_keywords = self.kw_sets
        organ.node_pool = _FakePool(self.nodes)
        organ._emit = lambda *a, **k: None
        organ._log = lambda *a, **k: None
        return organ._on_check_identity({})

    def test_upper_placeholder_still_matches(self):
        """判据3-①：期望词含大写占位符 → 仍命中大写形态 keywords。"""
        _res = self._run_check()
        self.assertEqual(_res.get("missing_seeds"), [],
                         "大写占位符行不得 MISS（判定须 casefold 对齐）")
        self.assertEqual(_res.get("status"), "complete")

    def test_offline_recompute_miss_3_to_0(self):
        """判据1：离线复算 MISS 行数 3 → 0（同一快照、同一五行）。"""
        # 修前基线：<SELF_NAME> / <CREATOR> / <CREATOR_DAUGHTER> 三行恒 MISS
        _upper_rows = [s for s in self.kw_sets
                       if any("<" in str(k) for k in s)]
        self.assertGreaterEqual(len(_upper_rows), 3,
                                "期望表应含 ≥3 行占位符行（复算基线）")
        _res = self._run_check()
        self.assertEqual(len(_res.get("missing_seeds") or []), 0,
                         "离线复算 MISS 应 3 → 0")

    def test_report_carries_diagnostics(self):
        """刀C③：上报含 candidates / checked_prefix（误报与真缺一眼可分）。"""
        _res = self._run_check()
        self.assertIn("candidates", _res)
        self.assertIn("checked_prefix", _res)


class TestLoadCoreIdentityKeywordsWired(unittest.TestCase):
    """刀C②：_load_core_identity_keywords() 必须被调用（封死活代码）。"""

    def test_called_on_init(self):
        import organs.identity.PulseSelfAwareness as _mod
        _calls = []
        _orig = _mod.PulseSelfAwareness._load_core_identity_keywords

        def _spy(*a, **k):
            _calls.append(1)
            return _orig(*a, **k)

        with mock.patch.object(_mod.PulseSelfAwareness,
                               "_load_core_identity_keywords",
                               staticmethod(_spy)):
            _mod.PulseSelfAwareness()
        self.assertGreaterEqual(
            len(_calls), 1,
            "__init__ 应调用 _load_core_identity_keywords()（接线不得回退）")

    def test_fallback_matches_config_seed_memories(self):
        """裁决2 防回归（消除双表）：硬编码兜底表必须与 config.SEED_MEMORIES 前 5 行
        keywords 完全一致；否则 config 改了兜底没改=双表回归（正是本批修复的 bug）。"""
        from organs.identity.PulseSelfAwareness import (
            _FALLBACK_CORE_IDENTITY_KEYWORDS,
        )
        _expected = [list(s.get("keywords") or [])
                     for s in (getattr(config, "SEED_MEMORIES", None) or [])[:5]]
        self.assertEqual(
            _FALLBACK_CORE_IDENTITY_KEYWORDS, _expected,
            "兜底表与 config.SEED_MEMORIES 前 5 行 keywords 不一致（双表回归）")


if __name__ == "__main__":
    unittest.main()
