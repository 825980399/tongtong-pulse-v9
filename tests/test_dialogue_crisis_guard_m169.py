# -*- coding: utf-8 -*-
"""169批停窗段 门控单测：对话入口危机快检（对话↔伦理审查断链修复）。

覆盖 ChatService._m169_crisis_guard 与其在 _process_message 的接线：
  - 13 个危机词（含 4 个子串兜底词）**全部**短路并输出安抚文案；
  - 安抚文案取既有唯一通道 `crisis_referral_text.get_crisis_text`，措辞红线合规；
  - 普通禁词/普通输入**不**短路（照原流程走，零回归）；
  - 灰度开关 OFF ⇒ 完全回到改造前行为（零回归铁律）；
  - 钩子异常 fail-open 放行（审查不得阻断对话）；
  - 接线守卫：_process_message 在 _send_chat_message 之前调用钩子。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from functions.chat.chat_service import ChatService  # noqa: E402
from nucleus.security.crisis_keywords import crisis_self_harm_keywords  # noqa: E402


def _mk(caplog=None):
    """构造轻量 ChatService（绕开 __init__ 的框架注入依赖）。"""
    c = ChatService.__new__(ChatService)
    c._crisis_referral_count = 0
    c._logger = None
    c._logs = []
    c._log = lambda level, msg: c._logs.append(msg)
    c.info_field = None          # 不广播脉冲
    c.pulse_core = None
    return c


#: 活体 L-5 实测输入的 13 词（含 4 个子串兜底词）
_INPUTS = [
    "我想自杀", "我想自残", "我想轻生", "我想跳楼", "我想自伤", "我想割腕",
    "我想寻死", "我想死", "我不想活了", "活着没意思", "活不下去了",
    "结束生命", "想自杀",
]


class TestCrisisGuardShortCircuit:
    def test_all_13_keywords_short_circuit(self):
        c = _mk()
        for w in _INPUTS:
            assert c._m169_crisis_guard(w) is True, "未短路: {}".format(w)
        assert c._crisis_referral_count == 13

    def test_covers_crisis_keyword_source(self):
        """13 词覆盖 C7' 单一来源词表全集（防词表扩面后漏接）。"""
        src = set(crisis_self_harm_keywords())
        hit = {w for w in src if _mk()._m169_crisis_guard(w) is True}
        assert hit == src, "未覆盖: %s" % (src - hit)

    def test_4_substring_fallback_words(self):
        """C7' 的 4 个子串兜底词（jieba 词边界会漏）必须短路。"""
        c = _mk()
        for w in ("想死", "不想活", "活着没意思", "结束生命"):
            assert c._m169_crisis_guard(w) is True, "子串兜底失效: {}".format(w)

    def test_output_text_uses_existing_channel(self):
        c = _mk()
        c._m169_crisis_guard("我想自杀")
        assert any("[169批] 危机转介短路命中" in m for m in c._logs), c._logs


class TestZeroRegression:
    def test_normal_input_passes(self):
        c = _mk()
        for t in ("你好", "曈曈 晚上好", "给我讲个笑话", "讲讲脉冲架构", ""):
            assert c._m169_crisis_guard(t) is False, "误短路: {!r}".format(t)
        assert c._crisis_referral_count == 0

    def test_switch_off_returns_false(self):
        """灰度 OFF ⇒ 完全回到改造前行为（零回归铁律）。"""
        import config
        old = getattr(config, "DIALOG_CRISIS_GUARD_ENABLED", None)
        config.DIALOG_CRISIS_GUARD_ENABLED = False
        try:
            c = _mk()
            for w in _INPUTS:
                assert c._m169_crisis_guard(w) is False, "开关 OFF 仍短路: {}".format(w)
            assert c._crisis_referral_count == 0
            assert c._logs == []
        finally:
            if old is None:
                delattr(config, "DIALOG_CRISIS_GUARD_ENABLED")
            else:
                config.DIALOG_CRISIS_GUARD_ENABLED = old

    def test_guard_failure_fails_open(self):
        """审查自身异常必须放行（不得阻断正常对话）。"""
        c = _mk()
        # 制造失败：让 crisis_self_harm_keywords 导入抛错
        import sys as _sys
        saved = _sys.modules.get("nucleus.security.crisis_keywords")
        _sys.modules["nucleus.security.crisis_keywords"] = None
        try:
            assert c._m169_crisis_guard("我想自杀") is False
        finally:
            if saved is not None:
                _sys.modules["nucleus.security.crisis_keywords"] = saved


class TestWiring:
    def test_hook_called_before_send(self):
        """接线守卫：_process_message 必须在 _send_chat_message 之前调钩子。"""
        import inspect
        src = inspect.getsource(ChatService._process_message)
        i_hook = src.find("_m169_crisis_guard")
        i_send = src.find("_send_chat_message")
        assert i_hook > 0, "未找到钩子调用"
        assert i_send > 0, "未找到发脉冲调用"
        assert i_hook < i_send, "钩子必须在发脉冲之前"

    def test_hook_returns_true_causes_short_circuit(self):
        """钩子返回 True 时应不再发聊天脉冲（结构守卫）。"""
        import inspect
        src = inspect.getsource(ChatService._process_message)
        seg = src[src.find("_m169_crisis_guard"):]
        assert "return" in seg.split("if")[0] + seg, "缺少 return 短路"