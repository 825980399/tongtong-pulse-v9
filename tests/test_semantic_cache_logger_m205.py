# -*- coding: utf-8 -*-
"""刀1 验收：semantic_cache_l2 logger 已改挂 pulse 子树。

隔离单测（铁律151）：本件不依赖生产会话，仅断言 logger 命名。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "nucleus", "llm", "semantic_cache.py")

NEW_NAME = "pulse.semantic_cache_l2"
OLD_NAME = "semantic_cache_l2"


def test_source_uses_pulse_prefixed_logger():
    """源码已改为 pulse.semantic_cache_l2，且旧名不再出现。"""
    with open(SRC, "r", encoding="utf-8") as f:
        text = f.read()
    assert 'logging.getLogger("%s")' % NEW_NAME in text
    assert 'logging.getLogger("%s")' % OLD_NAME not in text


def test_module_logger_name_is_pulse_prefixed():
    """运行期模块级 _L 名称为 pulse.semantic_cache_l2。"""
    import logging

    import nucleus.llm.semantic_cache as sc

    assert sc._L.name == NEW_NAME
    # 同一名字的 logger 是全局唯一单例，确认已挂入 pulse 子树
    assert logging.getLogger(NEW_NAME).name == NEW_NAME
    assert not NEW_NAME.startswith(OLD_NAME + '"')  # sanity

