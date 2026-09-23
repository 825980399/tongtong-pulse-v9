# -*- coding: utf-8 -*-
"""主线第34批门控单测：多步检索兜底链路根治 + 准入判据完善 + L3 边界。

覆盖：
    T1（P2-196）：`_multi_step_execute_v2` 全步失败 → 返回 `None`（灰度双向）
    T2（P2-197）：操作类判据放宽（间隔 15 + 35 动词 + 「把/将」并列链），负样本不误判
    T2（P2-198）：v2 入口「操作类二次判定」（灰度双向）
    T5（选项 A 第 1 项）：L3 分层队列上限边界
"""
import importlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import config  # noqa: E402

_PIW = importlib.import_module("organs.brain.PulseInnerWorld")
PulseInnerWorld = _PIW.PulseInnerWorld
_IF = importlib.import_module("nucleus.field.InfoField")
InfoField = _IF.InfoField
_SRC = io.open(os.path.join(_PROJ, "organs", "brain", "PulseInnerWorld.py"),
               encoding="utf-8").read()

_OP = "先打开设置，再点击蓝牙，然后配对设备。"


class _Switch:
    """按名字临时改写 config 开关（支持多开关）。"""

    def __init__(self, **kw):
        self._kw = kw

    def __enter__(self):
        self._old = {k: getattr(config, k, None) for k in self._kw}
        for k, v in self._kw.items():
            setattr(config, k, v)

    def __exit__(self, *a):
        for k, v in self._old.items():
            if v is None:
                try:
                    delattr(config, k)
                except AttributeError:
                    pass
            else:
                setattr(config, k, v)
        return False


def _mk(hit=False):
    """轻量实例；模型兜底以计数器打桩（禁止真实 HTTP）。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw.node_pool = None
    iw._logs = []
    iw._log = lambda level, msg: iw._logs.append(str(msg))
    iw._model_calls = []
    if hit:
        iw._knowledge_retrieve = (lambda q: (
            f"【知识库命中】与「{q[:20]}」相关的既有知识：这是一段足够长的"
            "可用检索结果，用于验证质量门可以通过。"))

    def _fake_branch(**kw):
        iw._model_calls.append(kw.get("branch_name", "?"))
        return None            # 模拟「模型兜底同样失败」的最坏情形
    iw._generate_branch_with_model = _fake_branch
    return iw


class TestFailReturnNone(unittest.TestCase):
    """T1（P2-196）：全步失败 → None；开关关闭 → 复现降级叙述。"""

    def test_01_switch_default_on(self):
        self.assertTrue(getattr(config, "ENABLE_MULTI_STEP_FAIL_RETURN_NONE", False))
        self.assertTrue(PulseInnerWorld._m34_fail_return_none_on())

    def test_02_all_steps_fail_returns_none(self):
        """★核心：检索全失败 → 返回 None（不再把降级叙述当答案）。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True,
                     ENABLE_MULTI_STEP_FAIL_RETURN_NONE=True):
            iw = _mk(hit=False)
            _out = PulseInnerWorld._multi_step_execute_v2(iw, _OP)
            self.assertIsNone(_out, "全步失败必须返回 None，交回上层单次兜底")
            self.assertTrue(any("返回 None" in m for m in iw._logs),
                            "应留下可观测日志（兼作探针）")

    def test_03_switch_off_restores_degraded_narrative(self):
        """灰度关闭 → 复现修复前的降级叙述（零回归）。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True,
                     ENABLE_MULTI_STEP_FAIL_RETURN_NONE=False):
            iw = _mk(hit=False)
            _out = PulseInnerWorld._multi_step_execute_v2(iw, _OP)
            self.assertIsNotNone(_out, "关闭修复时应返回旧的降级叙述")
            self.assertGreater(len(str(_out)), 15)

    def test_04_hit_still_returns_answer(self):
        """对照组：检索有命中 → 仍返回有效答案（修复不影响正常路径）。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True,
                     ENABLE_MULTI_STEP_FAIL_RETURN_NONE=True):
            iw = _mk(hit=True)
            _out = PulseInnerWorld._multi_step_execute_v2(iw, _OP)
            self.assertIsInstance(_out, str)
            self.assertGreater(len(_out.strip()), 15)

    def test_05_partial_success_still_returns_narrative(self):
        """部分成功（≥1 步）→ 仍应返回叙述（只有**全步失败**才回落）。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True,
                     ENABLE_MULTI_STEP_FAIL_RETURN_NONE=True):
            iw = _mk(hit=False)
            _calls = {"n": 0}

            def _half_branch(**kw):
                _calls["n"] += 1
                iw._model_calls.append(kw.get("branch_name", "?"))
                # 第一/三步成功、中间失败 → 非「全步失败」
                return (None if _calls["n"] == 2
                        else "这是一段足够长的模型兜底结果，用于制造部分成功场景。")
            iw._generate_branch_with_model = _half_branch
            _out = PulseInnerWorld._multi_step_execute_v2(iw, _OP)
            self.assertIsNotNone(_out, "存在成功步骤时不应回落为 None")

    def test_06_callers_treat_falsy_as_not_found(self):
        """★T0 依据：`_detect_multi_step_task` 三处调用点均按 falsy 判定「未拿到答案」。"""
        _i = _SRC.index("def _detect_multi_step_task")
        _j = _SRC.index("\n    def ", _i + 10)      # 方法体切片（到下一个方法）
        _seg = _SRC[_i:_j]
        import re as _re
        _guards = len(_re.findall(r"_result = self\._multi_step_execute\(", _seg))
        self.assertEqual(_guards, 3, f"应有 3 处调用点（实际 {_guards}）")
        _truthy = _seg.count("if _result:")
        _falsy = _seg.count("if not _result:")
        self.assertEqual(_truthy + _falsy, 3,
                         f"3 处调用都应紧跟 falsy 守卫（实际 {_truthy}+{_falsy}）")


class TestWidenedSignal(unittest.TestCase):
    """T2（P2-197）：判据放宽 + 负样本不误判。"""

    POSITIVE = [
        "先打开设置，再点击蓝牙",
        "先打开设置，再点击蓝牙，然后配对设备。",
        "第一步打开浏览器，第二步搜索天气预报，第三步把结果告诉我。",
        "打开微信，找到张总的聊天窗口，然后把这段话发给他。",
        "帮我打开记事本，然后把刚才那段文字粘贴进去。",
        "先复制这个文件到D盘，再把它重命名为备份。",
        "帮我查一下今天的天气，然后提醒我带伞。",
        "把这个表格里的重复行删掉，然后按日期排序。",
        "先在项目目录执行构建脚本，再运行测试用例，最后提交代码。",
        "把这段文字复制到剪贴板，然后粘贴到文档开头。",
        "打开设置页面，找到通知管理入口，然后把所有推送关掉。",
        "先登录后台，然后配置渠道参数，保存后重启服务。",
        "帮我下载那个安装包，然后解压到桌面。",
        "打开微信，进入张总的聊天窗口，把这段话发送给他。",
        "先在终端安装依赖，再运行测试脚本。",
    ]

    NEGATIVE = [
        "请分析深度学习的原理和应用场景，并与传统机器学习进行对比。",
        "量子计算在药物研发领域有哪些主要挑战？",
        "请分三部分论述：第一部分讲原理，第二部分讲应用。",
        "为什么会出现内存泄漏？如何排查？",
        "请解释一下 TCP 三次握手的机制。",
        "配置中心的设计原则有哪些？",
        "这个方案的优缺点分别是什么？",
        "如何理解注意力机制的核心思想？",
        "请对比微服务与单体架构在可维护性上的差异。",
        "运行时的日志级别应该如何设计？",
        "删除操作在数据库中是如何保证一致性的？",
        "请论述程序化配置管理相比手工配置的优势。",
        "启动过程为什么需要预热？",
        "复制语义与移动语义在 C++ 中的区别是什么？",
        "请说明连接池的配对策略与失效处理。",
    ]

    def test_10_switch_default_on(self):
        self.assertTrue(getattr(config, "ENABLE_MULTI_STEP_SIGNAL_V2", False))
        self.assertTrue(PulseInnerWorld._m34_widened_signal_on())

    def test_11_positive_recall_full(self):
        """★正样本 15/15 全部识别为操作类。"""
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=True):
            _miss = [q for q in self.POSITIVE
                     if not PulseInnerWorld._m29_has_multi_step_signal(q)]
        self.assertEqual(_miss, [], f"漏判: {_miss}")

    def test_12_negative_no_false_positive(self):
        """★负样本 0 误判（不得把知识/分析类问题当操作指令）。"""
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=True):
            _fp = [q for q in self.NEGATIVE
                   if PulseInnerWorld._m29_has_multi_step_signal(q)]
        self.assertEqual(_fp, [], f"误判: {_fp}")

    def test_13_long_gap_case_now_true(self):
        """回归用例（任务书要求）：间隔 12 字的操作指令必须判 True。"""
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=True):
            self.assertTrue(PulseInnerWorld._m29_has_multi_step_signal(
                "打开微信，找到张总的聊天窗口，然后把这段话发给他。"))

    def test_14_new_verbs_covered(self):
        """补充动词生效：删掉 / 粘贴 / 复制 / 发送 / 查。"""
        _cases = [
            "把这个表格里的重复行删掉，然后按日期排序。",
            "把这段文字复制到剪贴板，然后粘贴到文档开头。",
            "帮我查一下今天的天气，然后提醒我带伞。",
        ]
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=True):
            for _q in _cases:
                self.assertTrue(
                    PulseInnerWorld._m29_has_multi_step_signal(_q), _q)

    def test_15_ba_chain_pattern(self):
        """「把/将」并列动作链（无 再/然后 连接词）识别。"""
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=True):
            self.assertTrue(PulseInnerWorld._m29_has_multi_step_signal(
                "打开微信，进入张总的聊天窗口，把这段话发送给他。"))

    def test_16_switch_off_restores_narrow_judgement(self):
        """灰度关闭 → 复现修复前判据（长间隔仍漏判）。"""
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=False):
            self.assertFalse(PulseInnerWorld._m29_has_multi_step_signal(
                "打开微信，找到张总的聊天窗口，然后把这段话发给他。"))
            # 短间隔仍应识别（原判据本身有效）
            self.assertTrue(PulseInnerWorld._m29_has_multi_step_signal(
                "先打开设置，再点击蓝牙"))

    def test_17_structured_discourse_still_exempt(self):
        """第30批 T1 的论述豁免不得因本批放宽而失效。"""
        with _Switch(ENABLE_MULTI_STEP_SIGNAL_V2=True):
            for _q in ("请分三部分论述：第一部分讲原理。",
                       "请论证这个方案的可行性。",
                       "请分别说明两者的差异。"):
                self.assertFalse(
                    PulseInnerWorld._m29_has_multi_step_signal(_q), _q)


class TestEntryRecheck(unittest.TestCase):
    """T2（P2-198）：v2 入口「操作类二次判定」。"""

    _BASE_KW_OP = "帮我查一下今天的天气，然后提醒我带伞。"

    def test_20_switch_default_on(self):
        self.assertTrue(getattr(config, "ENABLE_MULTI_STEP_OP_RECHECK", False))
        self.assertTrue(PulseInnerWorld._m34_op_recheck_on())

    def test_21_off_blocks_base_keyword_operational(self):
        """★核心：含基础关键词的操作指令，在准入关闭时被拦截（零模型调用）。

        判据用「是否走到生成计划」证明是**入口门槛**拦下的，
        而非被 T1 的「全步失败返回 None」掩盖。
        """
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=False,
                     ENABLE_MULTI_STEP_OP_RECHECK=True):
            iw = _mk(hit=False)
            self.assertIsNone(
                PulseInnerWorld._multi_step_execute_v2(iw, self._BASE_KW_OP))
            self.assertFalse(any("计划:" in m for m in iw._logs),
                             "应在入口门槛处被拦截（未曾生成计划）")
            self.assertEqual(iw._model_calls, [], "拦截后不应有模型兜底调用")

    def test_22_off_still_allows_plain_knowledge_question(self):
        """反证：**非操作类**的知识问题在关闭时仍按基础关键词放行。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=False,
                     ENABLE_MULTI_STEP_OP_RECHECK=True):
            iw = _mk(hit=True)
            _out = PulseInnerWorld._multi_step_execute_v2(
                iw, "帮我查一下深度学习的原理和应用场景。")
            self.assertIsNotNone(_out, "知识类问题不应被二次判定误伤")

    def test_23_recheck_off_restores_old_behavior(self):
        """灰度关闭 → 复现修复前行为（基础关键词直接放行、进入多步）。

        断言「门槛放行（已生成计划）」而非最终返回值 ——
        最终返回值已被 T1 的「全步失败 → None」接管，不能用来证明门槛行为。
        """
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=False,
                     ENABLE_MULTI_STEP_OP_RECHECK=False):
            iw = _mk(hit=True)
            _out = PulseInnerWorld._multi_step_execute_v2(iw, self._BASE_KW_OP)
            self.assertTrue(any("计划:" in m for m in iw._logs),
                            "关闭二次判定时应放行并进入多步（旧行为）")
            self.assertIsNotNone(_out, "检索有命中时应产出答案")

    def test_24_on_admits_operational(self):
        """准入开启 → 操作类指令照常放行（修复不改变开启时的行为）。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True,
                     ENABLE_MULTI_STEP_OP_RECHECK=True):
            iw = _mk(hit=True)
            self.assertIsNotNone(
                PulseInnerWorld._multi_step_execute_v2(iw, _OP))


class _FakeQueue:
    def __init__(self, depth):
        self._d = depth

    def qsize(self):
        return self._d


class _FakePool:
    def __init__(self, depth):
        self._work_queue = _FakeQueue(depth)


def _mk_if(l3_limit):
    iw = InfoField.__new__(InfoField)
    iw._layer_pools = {}
    iw._layer_queue_limits = {_IF._PULSE_LAYER_L3: l3_limit,
                              _IF._PULSE_LAYER_L0: 0,
                              _IF._PULSE_LAYER_L1: 500}
    iw._layer_rejected_count = {}
    iw._layer_overflow_allowed = {}
    iw._overflow_priority_allow = 3
    return iw


class TestL3QueueBoundary(unittest.TestCase):
    """T5（选项 A 第 1 项）：L3 分层队列上限边界。"""

    def setUp(self):
        self._lg = patch.object(_IF, "_module_logger", new=MagicMock())
        self._lg.start()
        self.addCleanup(self._lg.stop)

    def test_30_limit_zero_never_blocks(self):
        """L0 生命线（上限 0）→ 永不限流。"""
        iw = _mk_if(300)
        self.assertTrue(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L0, _FakePool(99999)))

    def test_31_under_limit_admitted(self):
        iw = _mk_if(300)
        self.assertTrue(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L3, _FakePool(299)))

    def test_32_at_limit_rejected_for_normal_priority(self):
        """达上限即拒绝（普通优先级）。"""
        iw = _mk_if(300)
        self.assertFalse(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L3, _FakePool(300), priority=5))
        self.assertEqual(iw._layer_rejected_count[_IF._PULSE_LAYER_L3], 1)

    def test_33_high_priority_overflow_allowed(self):
        """高优先级脉冲在满队列时降级放行（P2-31）。"""
        iw = _mk_if(300)
        self.assertTrue(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L3, _FakePool(300), priority=3))
        self.assertEqual(iw._layer_overflow_allowed[_IF._PULSE_LAYER_L3], 1)

    def test_34_l1_soft_limit_double_before_reject(self):
        """L1 软上限：达 2 倍才拒绝（限流不丢包）。"""
        iw = _mk_if(300)
        self.assertTrue(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L1, _FakePool(500), priority=5))
        self.assertTrue(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L1, _FakePool(999), priority=5))
        self.assertFalse(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L1, _FakePool(1000), priority=5))

    def test_35_pool_none_admitted(self):
        iw = _mk_if(300)
        self.assertTrue(InfoField._admit_layer_task(
            iw, _IF._PULSE_LAYER_L3, None))

    def test_36_unset_layer_falls_back_to_zero(self):
        """未配置的层 → 视作无上限（防御性放行）。"""
        iw = _mk_if(300)
        self.assertTrue(InfoField._admit_layer_task(
            iw, "L9-unknown", _FakePool(99999)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
