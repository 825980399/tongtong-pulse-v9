# -*- coding: utf-8 -*-
"""★378 批盲测件 · L2 code 判据签名分级（P2）

任务书 §六：`_l2_classify_scene` 的 code 判据（>=2 标记）不是穷举，
单处 SELECT…FROM / 行首 print( / 行首 import / 单行 XxxError: 都会判 chat 放行。
本刀在准入闸另立**分级签名**，旧分类轴一行不改。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ===================================================================== 刀5
_D5_CODE = [
    ("行首 def", "def foo(a):\n    return a + 1\n"),
    ("行首 import", "import os\nos.getcwd()"),
    ("行首 print", "print('hello')\n"),
    ("SQL 成对", "SELECT id, name FROM users WHERE id = 3"),
    ("异常行", "ValueError: invalid literal for int()\n"),
    ("栈回溯", "Traceback (most recent call last):\n  File 'x.py'\n"),
    ("围栏", "```python\nx = 1\n```"),
]
_D5_QA = [
    ("解释型", "关于内存治理的解释：为什么要先止血再清创，怎么判断先后顺序？", "reasoning"),
    ("编程知识问答", "Python 里 import 和 from 的区别是什么？", "reasoning"),
    ("SQL 概念问答", "数据库里 SELECT 和 UPDATE 的区别是什么？", "reasoning"),
    ("print 概念问答", "Python 里 print 和 return 的区别？", "reasoning"),
    ("陈述句", "这条经验说明缓存命中率与污染率存在此消彼长的关系。", "chat"),
]


@pytest.mark.parametrize("tag,prompt", _D5_CODE)
def test_m378_d5_code_shapes_rejected(tag, prompt):
    """★核心：七种代码形态**全部**判 code，且 `system_internal` 一律拒收。"""
    from nucleus.llm.semantic_cache import SemanticCacheL2, _m378_scene_for_gate
    assert _m378_scene_for_gate(prompt) == "code", tag
    assert SemanticCacheL2._m377_origin_gate("system_internal", prompt) == (
        False, "scene_code_not_knowledge_qa"), tag


@pytest.mark.parametrize("tag,prompt,want", _D5_QA)
def test_m378_d5_knowledge_qa_not_over_blocked(tag, prompt, want):
    """★反向：真·知识问答不许被误杀（含"import 和 from 的区别"这种编程问答）。"""
    from nucleus.llm.semantic_cache import SemanticCacheL2, _m378_scene_for_gate
    assert _m378_scene_for_gate(prompt) == want, tag
    assert SemanticCacheL2._m377_origin_gate("system_internal", prompt)[0] is True, tag


def test_m378_d5_old_gate_missed_four_of_five():
    """反向先红：377 旧闸对其中 4 种形态**判 chat 放过**，本刀才需要分级签名。

    这是"改前确实漏"的可复现证据（不是嘴上说漏了）。
    """
    from nucleus.llm.semantic_cache import _m377_scene_for_gate, _m378_scene_for_gate
    _missed = [t for t, p in _D5_CODE
               if _m377_scene_for_gate(p) != "code" and _m378_scene_for_gate(p) == "code"]
    assert sorted(_missed) == sorted(["行首 import", "行首 print", "SQL 成对", "异常行"]), _missed


def test_m378_d5_scene_axis_untouched():
    """口径守恒：`_l2_classify_scene` 仍是"数标记"，没被本刀改（188刀1 遥测分桶靠它）。"""
    from nucleus.llm.semantic_cache import _l2_classify_scene
    assert _l2_classify_scene("SELECT id FROM users") == "chat"      # 只有 1 个标记
    assert _l2_classify_scene("import os\nok") == "chat"            # 仍只数到 1 个
    assert _l2_classify_scene("def f():\n    import os\n") == "code"  # ≥2 才是 code
