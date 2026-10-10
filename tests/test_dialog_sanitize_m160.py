"""第160批上A 刀4（票2 融合输出格式化）出口清洗验收测试。

覆盖任务书验收判据：
  ① 内部片段（关联知识/归纳升华/数据流）出口清洗后命中 0；
  ② 原始格式裸露标记（入口方法:/叶子方法:/内部调用/已理解/代码片段:）被剥离；
  ③ chat_service 推理技能画像跳过 unknown/空/None 名称（"擅长unknown" 出现 0）；
  ④ 句末标点后紧跟 ，、 归一（"。，" 序列 0）；
  ⑤ 防误伤：合法用户以"关联知识："开头的回答正文被保留。
"""
from __future__ import annotations

import os
import sys

import pytest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402


def _bare_iw():
    """构造未走 __init__ 的空壳，仅用于测纯文本处理方法。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._active_search_correlation = {}
    iw._log = lambda *a, **k: None
    return iw


class TestDialogSanitizeKnife4:
    # ---- ① 内部片段命中 0 ----
    @pytest.mark.parametrize("text", [
        "[internal]关联知识：内部知识节点原文内容",
        "[internal][归纳升华] 内部归纳升华内容",
        "[internal][数据流·胃] 已理解12个方法。入口方法: foo。叶子方法: bar。内部调用3次。",
    ])
    def test_internal_fragments_stripped(self, text):
        out = _bare_iw()._sanitize_internal_content(text, text)
        assert "关联知识" not in out, out
        assert "归纳升华" not in out, out
        assert "数据流" not in out, out

    # ---- ① 变体：legacy 方括号形式 关联知识：[x] 仍被剥离 ----
    def test_legacy_bracketed_associated_knowledge_stripped(self):
        out = _bare_iw()._sanitize_internal_content(
            "关联知识：[节点A] 这是内部内容不该外露", "q")
        assert "关联知识" not in out

    # ---- ② 原始格式裸露标记剥离 ----
    def test_no_raw_format_leak(self):
        out = _bare_iw()._sanitize_internal_content(
            "入口方法: foo。叶子方法: bar。内部调用3次。已理解5个方法。代码片段: x=1。",
            "q")
        assert "入口方法" not in out
        assert "叶子方法" not in out
        assert "内部调用" not in out
        assert "已理解" not in out
        assert "代码片段" not in out

    # ---- ④ 标点符号归一（"。，" -> "。"）----
    @pytest.mark.parametrize("text,needle", [
        ("规律。，值得。", "。，"),
        ("趋势！，明显。", "！，"),
        ("结论？，正确。", "？，"),
    ])
    def test_punctuation_normalization(self, text, needle):
        out = _bare_iw()._sanitize_internal_content(text, "q")
        assert needle not in out, out

    # ---- ⑤ 防误伤：合法用户"关联知识："正文被保留 ----
    def test_user_associated_knowledge_preserved(self):
        user_text = ("关联知识：用户的长篇合法回答内容，这里包含很多信息，"
                     "应该被完整保留给用户。")
        out = _bare_iw()._sanitize_internal_content(user_text, "q")
        assert "关联知识：" in out, out
        assert "应该被完整保留" in out, out


class TestChatReasoningUnknownSkip:
    """③ chat_service 推理技能画像跳过 unknown/空/None 名称。"""

    def test_unknown_name_skipped(self, capsys):
        try:
            from functions.chat.chat_service import ChatService
        except Exception as _e:  # 重依赖环境下优雅跳过，不阻断其他验收
            pytest.skip("chat_service 不可导入（环境依赖），跳过 ③: {!r}".format(_e))

        cs = ChatService.__new__(ChatService)
        _iw = type("O", (), {})()
        _iw.get_reasoning_skill_portrait = lambda: {
            "strong_types": [{"name": "unknown"}, {"name": "数学"}],
            "weak_types": [{"name": "物理"}, {"name": ""}, {"name": None}],
        }
        _framework = type("F", (), {})()
        _framework.node_pool = type("P", (), {})()
        _framework.node_pool.get_stats = lambda: {
            "evol_distribution": {}, "instinct_count": 0, "total_nodes": 0}
        _framework.organs = {"内在世界": _iw}
        cs.framework = _framework
        cs.info_field = None
        cs._short_term_memory = {}
        cs.pulse_core = None

        cs._show_status()
        _out = capsys.readouterr().out
        assert "unknown" not in _out, _out
        assert "擅长:数学" in _out, _out
        assert "待加强:物理" in _out, _out
