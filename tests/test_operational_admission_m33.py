# -*- coding: utf-8 -*-
"""主线第33批 T5（P2-191）：操作指令多步检索准入 效果评估 —— 可复现表征测试。

评测结论（量化，详见交付报告与 tmp/scan_m33_t5_eval.py）：
    · 开关 OFF（当前默认）：8 条典型操作指令中 **6 条被门槛挡回** → 直接走
      上层的单次大模型兜底（1 次模型调用），不再产生"3 步全失败"的低质答案；
    · 开关 ON：6 条放行，其中检索无命中时 **每条产生 3 次模型兜底调用**
      且最终答案仍是"分3步、每步⚠️失败"的形态 → **比直接兜底更贵、更差**；
    · ★但 OFF 未能完全消除风险：含「查」等基础关键词的操作指令
      （如"帮我查一下今天的天气，然后提醒我带伞"）**在 OFF 下仍会进入 v2**。

本文件把上述结论固化为可复现断言（不依赖大模型：模型兜底以计数器打桩）。
"""
import importlib
import os
import sys
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import config  # noqa: E402

_PIW = importlib.import_module("organs.brain.PulseInnerWorld")
PulseInnerWorld = _PIW.PulseInnerWorld

_SWITCH = "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION"

#: 典型"先…再…然后"操作指令：会被 `_m29_has_multi_step_signal` 判定为操作类
_OP_CLASSIC = "先打开设置，再点击蓝牙，然后配对设备。"
#: ★含基础关键词「查」的操作指令：**即使准入关闭也会通过** v2 的基础门槛
_OP_WITH_BASE_KW = "帮我查一下今天的天气，然后提醒我带伞。"


class _Switch:
    def __init__(self, value):
        self._v = value

    def __enter__(self):
        self._old = getattr(config, _SWITCH, None)
        setattr(config, _SWITCH, self._v)

    def __exit__(self, *a):
        if self._old is None:
            try:
                delattr(config, _SWITCH)
            except AttributeError:
                pass
        else:
            setattr(config, _SWITCH, self._old)
        return False


def _mk(retrieval_hit=False):
    """轻量实例；模型兜底以计数器打桩（禁止真实 HTTP）。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.node_pool = None
    iw._logs = []
    iw._log = lambda level, msg: iw._logs.append(str(msg))
    iw._model_calls = []
    if retrieval_hit:
        iw._knowledge_retrieve = (lambda q: (
            f"【知识库命中】与「{q[:20]}」相关的既有知识：这是一段足够长的"
            "可用检索结果，用于验证质量门可以通过。"))

    def _fake_branch(**kw):
        iw._model_calls.append(kw.get("branch_name", "?"))
        return None            # 模拟「模型兜底同样失败」的最坏情形
    iw._generate_branch_with_model = _fake_branch
    return iw


def _run(iw, q):
    try:
        return PulseInnerWorld._multi_step_execute_v2(iw, q)
    except Exception as e:                       # pragma: no cover
        return f"__EXC__{type(e).__name__}: {e}"


class TestAdmissionEffect(unittest.TestCase):
    def test_01_default_is_off(self):
        """当前默认关闭（第32批按内部协作者裁决对齐）。"""
        self.assertFalse(getattr(config, _SWITCH, True),
                         f"{_SWITCH} 默认应为 False")

    def test_02_off_blocks_classic_operational(self):
        """★OFF：经典操作指令被门槛挡回（返回 None）→ 交给上层单次兜底。"""
        with _Switch(False):
            iw = _mk()
            self.assertIsNone(_run(iw, _OP_CLASSIC))
            self.assertEqual(iw._model_calls, [],
                             "挡回时不应发生任何模型兜底调用")

    def test_03_on_all_steps_fail_returns_none(self):
        """★第34批 T1（P2-196）：ON 且检索全失败 → **返回 None**，交回上层单次兜底。

        修复前返回「分N步、每步⚠️失败」的降级叙述（第33批实测），
        会**阻断**上层单次大模型兜底 → 更贵更差。
        （注意：模型兜底调用仍按步发生 —— 那是 v2 的内部实现；
        变化的是**最终出口**，不再把低质叙述当答案返回。）
        """
        with _Switch(True):
            iw = _mk(retrieval_hit=False)
            _out = _run(iw, _OP_CLASSIC)
            _steps = len(PulseInnerWorld._generate_task_plan(iw, _OP_CLASSIC)
                         or [])
            self.assertGreaterEqual(_steps, 2)
            self.assertEqual(len(iw._model_calls), _steps,
                             "检索全失败时，每步仍各触发一次模型兜底调用")
            self.assertIsNone(_out, "全步失败应返回 None（P2-196）")

    def test_04_on_succeeds_when_retrieval_hits(self):
        """★对照组：检索有命中时，v2 能产出有效答案且**零**模型调用。"""
        with _Switch(True):
            iw = _mk(retrieval_hit=True)
            _out = _run(iw, _OP_CLASSIC)
            self.assertIsInstance(_out, str)
            self.assertGreater(len(_out.strip()), 15)
            self.assertEqual(iw._model_calls, [])

    def test_05_off_blocks_base_keyword_ops(self):
        """★第34批 T2（P2-198）：含基础关键词的操作指令在 OFF 下**也被拦截**。

        第33批实测的残余风险：8 条操作指令中有 2 条（含「查」等基础词）
        在 OFF 下仍进 v2，仍付 N 次模型兜底调用。
        第34批增加「操作类二次判定」后已拦截（零模型调用）。
        """
        with _Switch(False):
            iw = _mk(retrieval_hit=False)
            _out = _run(iw, _OP_WITH_BASE_KW)
            self.assertIsNone(_out, "P2-198 修复后：OFF 下应拦截操作类指令")
            self.assertEqual(iw._model_calls, [],
                             "拦截后不应发生任何模型兜底调用")


class TestKnownGapCharacterization(unittest.TestCase):
    """判据表征测试。

    ★主线第34批：第33批在此表征的**两处缺口均已修复**（P2-197 判据放宽 /
    P2-198 入口二次判定），断言已同步更新为「修复后的期望行为」。
    """

    def test_10_long_gap_operational_now_recognized(self):
        """★第34批 T2（P2-197）：间隔 ≤15 的真实操作指令不再漏判。

        第33批表征的缺口：动作序列正则 `.{0,10}?` 过窄，使本条（「打开」→「然后」
        间隔 12 字）判 False，**即使打开准入也进不去 v2**。
        第34批 T0 实测间隔 **15 为饱和点**（再放宽到 30 零额外收益），放宽后判 True。
        """
        _q = "打开微信，找到张总的聊天窗口，然后把这段话发给他。"
        self.assertTrue(PulseInnerWorld._m29_has_multi_step_signal(_q))

    def test_11_classic_operational_recognized(self):
        """反证：短间隔的"先…再…"仍能被正确识别（判据本身有效）。"""
        self.assertTrue(
            PulseInnerWorld._m29_has_multi_step_signal("先打开设置，再点击蓝牙"))

    def test_12_structured_discourse_not_treated_as_operational(self):
        """结构化论述要求不得被判为操作指令（第30批 T1 的回归护栏）。"""
        self.assertFalse(PulseInnerWorld._m29_has_multi_step_signal(
            "请分三部分论述：第一部分讲原理，第二部分讲应用。"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
