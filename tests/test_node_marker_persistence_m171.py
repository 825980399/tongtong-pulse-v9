# -*- coding: utf-8 -*-
"""171批刀6 · 节点标记持久化 回归单测（T-节点标记持久化-1）。

验收：加载路径保留 quality_flag/trust_score（快照重写后标记不丢）；
覆盖「落盘 → 重启 → 复扫」三态：
  ① 落盘：PulseNode.to_dict 序列化 quality_flag / trust_score；
  ② 重启：PulseNode.from_dict 还原后取值一致（含占位符标记
         quality_flag == "placeholder_alias" 与 trust_score == 0.0）；
  ③ 复扫：重载节点仍可被占位符判定谓词识别（quality_flag 字面量不丢）。
节点完整性：往返不增删字段、不破坏其它标记（clean 节点仍 clean）。

注：placeholder_alias 以 quality_flag 字面量形式落盘（cleanup_alias_placeholder_nodes.py
/ PollutionTagger 同款），故 quality_flag 的持久化即覆盖占位符标记持久化。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.knowledge.PollutionTagger import FLAG_PLACEHOLDER_ALIAS
from nucleus.mnemosyne.PulseNode import PulseNode


def _node_with_marker():
    _n = PulseNode(value="示例占位符内容 [器官别名] 节点", keywords=["k"])
    _n.quality_flag = FLAG_PLACEHOLDER_ALIAS
    _n.trust_score = 0.0
    return _n


def test_to_dict_serializes_marker_fields():
    """① 落盘：to_dict 含 quality_flag / trust_score 且值正确。"""
    _n = _node_with_marker()
    _d = _n.to_dict()
    assert _d["quality_flag"] == FLAG_PLACEHOLDER_ALIAS
    assert _d["trust_score"] == 0.0


def test_from_dict_restores_marker_after_restart():
    """② 重启：from_dict 还原后占位符标记与信任分不丢、其余字段完整。"""
    _n = _node_with_marker()
    _d = _n.to_dict()
    _n2 = PulseNode.from_dict(_d)
    assert _n2.quality_flag == FLAG_PLACEHOLDER_ALIAS, _n2.quality_flag
    assert _n2.trust_score == 0.0
    # 关键字段往返一致（节点完整性）
    assert _n2.node_id == _n.node_id
    assert _n2.value == _n.value
    assert _n2.evol_level == _n.evol_level


def test_rescan_reidentifies_marker():
    """③ 复扫：重载节点仍被占位符判定谓词识别；clean 节点不被误标。"""
    _n = _node_with_marker()
    _n2 = PulseNode.from_dict(_n.to_dict())
    # 复扫判据 = quality_flag 字面量（与 PollutionTagger / PlaceholderSanitizer 同口径）
    assert _n2.quality_flag == FLAG_PLACEHOLDER_ALIAS

    _clean = PulseNode(value="正常节点", keywords=[])
    _clean2 = PulseNode.from_dict(_clean.to_dict())
    assert _clean2.quality_flag == "clean"
