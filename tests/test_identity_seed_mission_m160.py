# -*- coding: utf-8 -*-
"""★第160批 上A 刀5（T-身份种子使命-1）防回归单测。

钉住：
  1. 根因：SEED_MEMORIES[3]（使命种子）实际路径 /身份/使命/核心，旧逻辑只查
     /身份/自我 前缀将其排除 → 恒报 missing_seeds=['使命']（degraded 复发）。
  2. 修复：查询范围放宽到整棵 /身份 子树（灰度开关 IDENTITY_SEED_CHECK_PREFIX，
     默认 /身份），使命种子被纳入核对 → status=complete。
  3. 回滚验证：若灰度开关被改回 /身份/自我（旧严格域），本测试须能复现 degraded
     （封死「修好后又被悄悄改窄」的回退）。
  4. 关键字匹配追加 node.value 语义包含兜底（keywords 命中 OR value 语义包含）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from unittest import mock
except ImportError:
    import mock

import config  # noqa: E402
from organs.identity.PulseSelfAwareness import PulseSelfAwareness  # noqa: E402


class _FakeNode:
    """最小节点替身：keywords + space_path + value。"""

    def __init__(self, keywords, space_path, value=""):
        self.keywords = list(keywords)
        self.space_path = space_path
        self.value = value


class _PathFakePool:
    """节点池替身：query(space_path_prefix=) 按 startswith 真实过滤。"""

    def __init__(self, nodes):
        self._nodes = list(nodes)

    def query(self, space_path_prefix=None, evol_level=None, limit=100):
        if space_path_prefix:
            return [n for n in self._nodes
                    if (n.space_path or "").startswith(space_path_prefix)]
        return list(self._nodes)


def _seed_keyword_sets():
    return [list(s.get("keywords") or [])
            for s in (getattr(config, "SEED_MEMORIES", None) or [])[:5]]


def _build_seed_nodes():
    """按 config.SEED_MEMORIES 前 5 条真实 path/keywords/value 造替身节点。"""
    _nodes = []
    for _s in (getattr(config, "SEED_MEMORIES", None) or [])[:5]:
        if not isinstance(_s, dict):
            continue
        _nodes.append(_FakeNode(
            keywords=_s.get("keywords") or [],
            space_path=_s.get("space_path", ""),
            value=_s.get("value", ""),
        ))
    return _nodes


class TestIdentitySeedMissionScope(unittest.TestCase):
    """刀5 主修复：使命种子在 /身份 子树内被纳入核对。"""

    def _run_with_prefix(self, prefix):
        _nodes = _build_seed_nodes()
        _organ = PulseSelfAwareness.__new__(PulseSelfAwareness)
        _organ._check_count = 0
        _organ._core_identity_keywords = _seed_keyword_sets()
        _organ.node_pool = _PathFakePool(_nodes)
        _organ._emit = lambda *a, **k: None
        _organ._log = lambda *a, **k: None
        with mock.patch.object(config, "IDENTITY_SEED_CHECK_PREFIX", prefix):
            return _organ._on_check_identity({})

    def test_mission_seed_resolved_under_identity_scope(self):
        """判据2：默认 /身份 范围下，使命种子被纳入 → status=complete。"""
        _res = self._run_with_prefix("/身份")
        self.assertEqual(_res.get("status"), "complete",
                         "使命种子须在 /身份 范围内被找到")
        self.assertEqual(_res.get("missing_seeds"), [],
                         "不应再报 missing_seeds=['使命']")

    def test_legacy_strict_prefix_reproduces_degraded(self):
        """判据3：灰度开关改回 /身份/自我（旧严格域）须能复现 degraded，
        证明根因是路径查询盲区（防回退）。"""
        _res = self._run_with_prefix("/身份/自我")
        self.assertEqual(_res.get("status"), "degraded")
        self.assertIn("使命", _res.get("missing_seeds") or [],
                      "旧 /身份/自我 严格域必漏检使命种子")

    def test_mission_seed_path_outside_self(self):
        """判据1：根因文档化——使命种子真实路径不在 /身份/自我 下。"""
        _mission = None
        for _s in (getattr(config, "SEED_MEMORIES", None) or [])[:5]:
            if isinstance(_s, dict) and "使命" in (_s.get("keywords") or []):
                _mission = _s
                break
        self.assertIsNotNone(_mission, "应存在含'使命'的种子")
        self.assertFalse(
            _mission.get("space_path", "").startswith("/身份/自我"),
            "使命种子路径 {!r} 不在 /身份/自我 下（旧查询盲区根因）".format(_mission.get("space_path")))


class TestIdentitySeedValueFallback(unittest.TestCase):
    """刀5 兜底：node.value 语义包含（keywords 命中 OR value 语义包含）。"""

    def _run_single(self, keywords_set, node_keywords, node_value, node_path):
        _node = _FakeNode(node_keywords, node_path, node_value)
        _organ = PulseSelfAwareness.__new__(PulseSelfAwareness)
        _organ._check_count = 0
        _organ._core_identity_keywords = [list(keywords_set)]
        _organ.node_pool = _PathFakePool([_node])
        _organ._emit = lambda *a, **k: None
        _organ._log = lambda *a, **k: None
        with mock.patch.object(config, "IDENTITY_SEED_CHECK_PREFIX", "/身份"):
            return _organ._on_check_identity({})

    def test_value_fallback_hits_when_keywords_empty(self):
        """关键字为空但 value 含目标词 → 仍命中（语义包含兜底）。"""
        _res = self._run_single(["使命"], [], "我的使命是守护世界", "/身份/自我/x")
        self.assertEqual(_res.get("status"), "complete")
        self.assertEqual(_res.get("missing_seeds"), [])

    def test_value_fallback_miss_when_neither(self):
        """关键字与 value 均无目标词 → 仍 MISS（不误报命中）。"""
        _res = self._run_single(["使命"], [], "无关内容", "/身份/自我/x")
        self.assertEqual(_res.get("status"), "degraded")
        self.assertIn("使命", _res.get("missing_seeds") or [])


if __name__ == "__main__":
    unittest.main()
