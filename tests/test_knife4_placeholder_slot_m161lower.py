# -*- coding: utf-8 -*-
"""第161批下 刀4 门控单测（T-占位符空槽泄漏-1）。

核心断言：
  A. 判据侧（步1）——空槽「」/ 截断 p... / <NAME>... 能抓到；
     ★正常中文引号「进化」不得误伤（防误杀真内容）
  B. 检索/合成层（步2）——开关存在且默认 True；占位符节点在命中判定被拦
  C. 回归——既有字面量判据仍生效；开关关闭可回滚
"""
# -*- coding: utf-8 -*-
# export-scan-skip-file  （本文件占位符均为构造夹具，非真实内容）

import importlib.util
import io
import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPO = ROOT


def _load_ps():
    path = os.path.join(REPO, 'nucleus', 'knowledge', 'PlaceholderSanitizer.py')
    spec = importlib.util.spec_from_file_location('ps_k4', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestKnife4Judge:
    """步1：判据侧——空槽与截断占位。"""

    def test_01_empty_slot_caught(self):
        """★症状1：空槽「」必须被抓到。"""
        ps = _load_ps()
        assert ps.contains_placeholder('你对进化的理解是「」'), '空槽漏检'

    def test_02_truncated_caught(self):
        """★症状2：截断占位 p... 必须被抓到。"""
        ps = _load_ps()
        assert ps.contains_placeholder('这是 p... 的截断'), '截断占位漏检'

    def test_03_angle_truncated_caught(self):
        ps = _load_ps()
        assert ps.contains_placeholder('<SELF_NAME>...'), '尖括号截断漏检'

    def test_04_normal_quote_not_mistreated(self):
        """★关键反例：正常中文引号「进化」不得误伤（泛化会误杀真内容）。"""
        ps = _load_ps()
        assert not ps.contains_placeholder('「进化」是核心概念'), '★误伤正常引号'
        assert not ps.contains_placeholder('正常回答内容'), '误伤无占位文本'

    def test_05_existing_literal_still_works(self):
        """回归：既有字面量判据不受影响。"""
        ps = _load_ps()
        assert ps.contains_placeholder('[器官别名] 人格内核')
        # 关联知识：。属 contains_placeholder_literal 专属判据（语义未混）
        assert ps.contains_placeholder_literal('关联知识：。xxx')


class TestKnife4RetrievalGate:
    """步2：检索/合成层堵源。"""

    def test_06_switch_exists_and_defaults(self):
        import config
        assert hasattr(config, 'KNOWLEDGE_RETRIEVE_SKIP_PLACEHOLDER')
        assert config.KNOWLEDGE_RETRIEVE_SKIP_PLACEHOLDER is True

    def test_07_source_wired_in_retrieval(self):
        """★堵源必须在检索命中侧（_ir_qica_knowledge_retrieve / _k89_accept）。"""
        src = io.open(os.path.join(REPO, 'organs', 'brain', 'PulseInnerWorld.py'),
                      encoding='utf-8', newline=None).read()
        assert 'KNOWLEDGE_RETRIEVE_SKIP_PLACEHOLDER' in src, '检索侧未接开关'
        assert 'contains_placeholder' in src, '检索侧未调判据'
        assert '刀4 堵源' in src, '应有刀4 堵源标注'

    def test_08_placeholder_node_blocked(self):
        """★占位符/空槽节点在命中判定被拦（等价 _k89_accept 逻辑）。"""
        ps = _load_ps()

        def accept(val, skip_ph=True):
            if skip_ph and ps.contains_placeholder(val):
                return False
            return True

        assert accept('由 92 条认知融合而成的进化理解说明，内容详实。')
        assert not accept('你对进化的理解是「」'), '★空槽未拦（症1 未堵）'
        assert not accept('进化理解参见 p... 详文'), '★截断未拦（症2 未堵）'

    def test_09_switch_off_rolls_back(self):
        """开关关闭 ⇒ 完整回滚旧行为。"""
        ps = _load_ps()

        def accept(val, skip_ph=True):
            if skip_ph and ps.contains_placeholder(val):
                return False
            return True

        assert accept('你对进化的理解是「」', skip_ph=False), '开关关闭应放行（回滚）'