# -*- coding: utf-8 -*-
"""主线第31批 门控测试：多步检索成功率低修复（T2 / P2-184）。

背景（本批 T0 实测）：
  1. 检索词退化 —— 三个多步推理入口都用 `re.finditer(r'[\\u4e00-\\u9fff]{2,6}', q)`
     做定长贪婪切片，长问题被切成错位碎片：
       「请分析深度学习的原理和应用场景」→ 「请分析深度学」/「习的原理和应」…
  2. 分支生成旁路 —— `_generate_branch_with_model` 只读 `REMOTE_API_CONFIG`，
     绕过渠道池（优先级/并发管控/熔断），且只认 `TTP_REMOTE_API_KEY` 一个环境变量。
  3. 失败原因不具体 —— v2 每步只记「成功/兜底」，无法区分卡在哪一环。
"""
import os
import sys
import textwrap
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

_IW_FAMILY = [
    os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseInnerWorld.py"),
    os.path.join(_PROJECT_ROOT, "organs", "brain", "pulse_inner_world_support.py"),
    os.path.join(_PROJECT_ROOT, "organs", "brain", "pulse_inner_world_knowledge.py"),
]
# ★主线第139批 T-139b：知识检索簇已平移至 KnowledgeMixin，
#   故源码断言须拼接 IW 全家族（主文件 + 两个 Mixin），否则平移即假失败。
_IW_SRC = "\n".join(
    open(_p, encoding="utf-8").read() for _p in _IW_FAMILY
)

# 真实长问题样本（本批 T0 实测用的同一批）
_Q_LONG = "请分析深度学习的原理和应用场景，并与传统机器学习进行对比"
_Q_SEARCH = "帮我查一下量子计算在药物研发领域的应用进展和主要挑战"
_Q_COMPARE = "比较微服务架构和单体架构在可维护性和性能上的优劣"


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


def _mk_iw():
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._log = lambda *a, **k: None
    return iw


def _legacy_extract(q, n=3):
    """修复前的定长切片（对照用）。"""
    import re
    out = []
    for m in re.finditer(r'[\u4e00-\u9fff]{2,6}', q):
        w = m.group()
        if w not in out and w not in ["什么是", "是什么", "如何", "怎么", "这个", "那个"]:
            out.append(w)
    return out[:n]


class TestKeyTermExtraction(unittest.TestCase):
    """★核心：检索词不再退化为「整段问题碎片」。"""

    def test_01_no_char_fragments(self):
        """提取结果不得是跨词边界的定长碎片。"""
        legacy = _legacy_extract(_Q_LONG, 3)
        now = PulseInnerWorld._m31_extract_key_terms(_Q_LONG, 3)
        # 旧结果含「请分析深度学」这类跨词碎片
        self.assertIn("请分析深度学", legacy)
        self.assertNotIn("请分析深度学", now)
        for w in now:
            self.assertNotIn("请分析", w, "不得含指令残留")

    def test_02_real_words_expected(self):
        self.assertEqual(PulseInnerWorld._m31_extract_key_terms(_Q_LONG, 3),
                         ["深度", "学习", "原理"])

    def test_03_stopwords_filtered(self):
        """指令词/代词噪音必须被滤掉（首版 jieba.cut + 长度优先会漏出「我查」）。"""
        now = PulseInnerWorld._m31_extract_key_terms(_Q_SEARCH, 3)
        for bad in ("我查", "帮我", "一下", "查一下", "请"):
            self.assertNotIn(bad, now, f"停用词 {bad} 未被过滤")

    def test_04_connective_filtered(self):
        now = PulseInnerWorld._m31_extract_key_terms(_Q_COMPARE, 3)
        for bad in ("比较", "对比", "优劣", "区别"):
            self.assertNotIn(bad, now)

    def test_05_order_preserved(self):
        """保持原文出现顺序（前半段承载核心实体）。"""
        now = PulseInnerWorld._m31_extract_key_terms(_Q_SEARCH, 3)
        self.assertEqual(now[0], "量子")

    def test_06_limit_respected(self):
        for n in (1, 2, 3, 5):
            self.assertLessEqual(len(PulseInnerWorld._m31_extract_key_terms(_Q_LONG, n)), n)
        self.assertEqual(len(PulseInnerWorld._m31_extract_key_terms(_Q_LONG, 1)), 1)

    def test_07_empty_and_degenerate(self):
        self.assertEqual(PulseInnerWorld._m31_extract_key_terms("", 3), [])
        self.assertEqual(PulseInnerWorld._m31_extract_key_terms(None, 3), [])
        # 全是停用词 → 空列表（调用方需自行处理）
        self.assertEqual(PulseInnerWorld._m31_extract_key_terms("什么是", 3), [])

    def test_08_fallback_without_jieba(self):
        """jieba 完全不可用 → 回退「标点/虚词切分」，且**绝不退回定长切片**。"""
        _saved = {}
        for _m in ("jieba", "jieba.posseg"):
            _saved[_m] = sys.modules.get(_m, "__ABSENT__")
            sys.modules[_m] = None
        try:
            now = PulseInnerWorld._m31_extract_key_terms(_Q_LONG, 3)
        finally:
            for _m, _v in _saved.items():
                if _v == "__ABSENT__":
                    sys.modules.pop(_m, None)
                else:
                    sys.modules[_m] = _v
        self.assertTrue(now, "回退路径不应返回空")
        for w in now:
            self.assertNotIn("请分析深度学", w, "回退路径也不得产出定长碎片")


class TestBranchEndpoint(unittest.TestCase):
    """★核心：分支生成读渠道配置，不再只认 REMOTE_API_CONFIG。"""

    def test_10_channel_first_by_default(self):
        _ep = PulseInnerWorld._m31_branch_endpoint()
        self.assertIsNotNone(_ep, "本机应至少有一个可用渠道")
        self.assertTrue(_ep[3].startswith("channel:"),
                        f"应优先取渠道池，实际 source={_ep[3]}")
        self.assertTrue(_ep[0] and _ep[1] and _ep[2])

    def test_11_switch_off_falls_back_to_single_endpoint(self):
        with _Switch(ENABLE_BRANCH_GEN_CHANNEL_FIRST=False):
            _ep = PulseInnerWorld._m31_branch_endpoint()
        self.assertIsNotNone(_ep)
        self.assertEqual(_ep[3], "remote_config")

    def test_12_source_labels_distinct(self):
        _on = PulseInnerWorld._m31_branch_endpoint()
        with _Switch(ENABLE_BRANCH_GEN_CHANNEL_FIRST=False):
            _off = PulseInnerWorld._m31_branch_endpoint()
        self.assertNotEqual(_on[3], _off[3])

    def test_13_default_on(self):
        self.assertTrue(getattr(config, "ENABLE_BRANCH_GEN_CHANNEL_FIRST", False))


class TestSourceWiring(unittest.TestCase):
    """源码层：三处检索词入口 + 分支生成端点均已接线。"""

    def test_20_three_entry_points_use_new_extractor(self):
        """★主线第32批 T2 后：调用点由 4 处增至 12 处。

        断言改为数**调用形态**（`self._m31_extract_key_terms(`），
        避免 docstring 中的示例文本被计入（原实现数裸方法名 → 脆弱）。

        ★主线第139批 T-139b：计数口径改为 IW 全家族三文件拼接（拆分后跨文件）。
        ★主线第33批 T2（P2-195）：再放宽为 `>= 12` —— 语义是「定长切片已大范围
        被替换」（回退即骤降）；精确计数会因正常重构而假失败。
        """
        self.assertGreaterEqual(_IW_SRC.count("self._m31_extract_key_terms("), 12)

    def test_21_branch_generation_no_longer_reads_old_config(self):
        _i = _IW_SRC.index("    def _generate_branch_with_model")
        _j = _IW_SRC.index("    def _is_valid_branch_content")
        _seg = _IW_SRC[_i:_j]
        self.assertNotIn("_remote_available", _seg)
        # 只允许出现在注释里
        for _line in _seg.splitlines():
            if "REMOTE_API_CONFIG" in _line:
                self.assertTrue(_line.strip().startswith("#"),
                                f"非注释行仍引用旧配置: {_line.strip()}")
        self.assertIn("_m31_branch_endpoint()", _seg)

    def test_22_fail_reason_recorded(self):
        self.assertIn('"fail_reason": _fail_reason', _IW_SRC)
        self.assertIn("四个来源(检索/重试/降级/模型)均无结果", _IW_SRC)


class TestTaskPlanUsesExtractor(unittest.TestCase):
    """`_generate_task_plan` 产出的检索 prompt 不得含错位碎片。"""

    def test_30_plan_prompts_clean(self):
        _plan = _mk_iw()._generate_task_plan(_Q_SEARCH)
        self.assertTrue(_plan, "应生成计划")
        _joined = " ".join(_s["prompt"] for _s in _plan)
        self.assertNotIn("帮我查一下量", _joined)
        self.assertIn("量子", _joined)

    def test_31_compare_plan_still_splits_ab(self):
        """对比类仍按连接词切分 A/B（既有特性不得回归）。"""
        _plan = _mk_iw()._generate_task_plan(_Q_COMPARE)
        self.assertTrue(any("了解A" == _s["name"] for _s in _plan))


class TestOperationalAdmission(unittest.TestCase):
    """★T3 微调：真操作指令必须能进入「真多步推理 v2」。

    端到端实测根因（logs/pulse.log 2027-2031）：
      cortex 的 QICA 已把「先打开设置，再点击蓝牙，然后配对设备。」正确路由到
      multi_step_execute，但 v2 入口门槛只认疑问/分析类关键词 → 返回 None →
      回落默认路径（最终由大模型兜底）。本批补上操作指令准入。
    """

    _Q_OP = "先打开设置，再点击蓝牙，然后配对设备。"
    _START = "        _q = str(question or \"\").strip()\n"
    _END = "        _plan = self._generate_task_plan(question)\n"

    def _probe(self):
        """切出 v2 入口段（★注意 _END 在 v1 也出现，必须从 _START 之后找）。"""
        _i = _IW_SRC.index(self._START)
        _seg = textwrap.dedent(_IW_SRC[_i:_IW_SRC.index(self._END, _i)])
        assert _seg, "v2 入口切片不得为空"
        _code = ("def _probe(self, question):\n"
                 + textwrap.indent(_seg, "    ") + "    return 'ADMITTED'\n")
        _ns = {"str": str, "any": any, "bool": bool, "len": len}
        exec(compile(_code, "<m31-v2gate>", "exec"), _ns)
        return _ns["_probe"]

    def test_40_operational_signal_is_true(self):
        """前置事实：第30批的操作指令正则化对真操作指令判定为 True。"""
        self.assertTrue(PulseInnerWorld._m29_has_multi_step_signal(self._Q_OP))

    def test_41_switch_default_off_per_arbitration(self):
        """★星轨裁决（§61.4 第3项）：该准入**默认关闭**（记为债务 P2-191）。

        操作类指令的多步检索价值有限，可能「3 步全失败」劣于大模型兜底，
        故默认走原门槛；如需启用可显式打开开关。
        """
        self.assertFalse(PulseInnerWorld._m31_operational_admission_on())
        self.assertFalse(getattr(config, "ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION", True))

    def test_42_gate_admits_operational_when_enabled(self):
        """开关**显式开启**时，操作指令不再被 v2 入口门槛挡回 None。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True):
            self.assertEqual(self._probe()(_mk_iw(), self._Q_OP), "ADMITTED")

    def test_43_gate_still_rejects_noise(self):
        """短问题仍被挡（长度下限不得被放宽）—— 开关开/关都不得放行。"""
        self.assertIsNone(self._probe()(_mk_iw(), "嗯"))
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=True):
            self.assertIsNone(self._probe()(_mk_iw(), "嗯"))

    def test_44_switch_off_restores_old_gate(self):
        """灰度关闭（当前默认）→ 操作指令被门槛挡回（还原修复前行为）。"""
        with _Switch(ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION=False):
            self.assertIsNone(self._probe()(_mk_iw(), self._Q_OP))


class TestV2EndToEndAdmission(unittest.TestCase):
    """真实调用链：操作指令能越过 v2 入口门槛。

    ★主线第34批 T1（P2-196）后：全步失败会在**出口**返回 None（交回上层单次兜底），
    故本类断言「越过门槛（已生成计划）」，最终返回值语义由
    `tests/test_multi_step_fallback_m34.py` 覆盖。
    """

    def test_50_operational_reaches_v2_when_enabled(self):
        """开关**显式开启**时，操作指令可越过 v2 入口门槛（默认已关闭，见裁决 §61.4-3）。"""
        # ★第81批补2：进入时保存原值、finally 还原原值（不硬编码 False）
        _orig_admission = config.ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION
        config.ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION = True
        _logs = []
        try:
            _iw = PulseInnerWorld.__new__(PulseInnerWorld)
            _iw._log = lambda lvl, msg: _logs.append(str(msg))
            _iw._true_multi_step_enabled = lambda: True
            _iw._knowledge_retrieve = lambda p: None            # 检索全失败
            _iw._generate_branch_with_model = lambda **k: None  # 模型兜底也失败
            _iw._validate_step_result = lambda r, p: False
            _iw._reframe_step_prompt = lambda p, fb: p
            _iw._summarize_step_for_next = lambda s: ""
            _iw._is_valid_branch_content = lambda c: False
            _iw._clean_inference_output = lambda t, **k: t
            _r = _iw._multi_step_execute(self._q)
            # ① 越过入口门槛（生成了计划 —— 门槛挡回时不会有这条日志）
            self.assertTrue(any("计划:" in _m for _m in _logs),
                            "开关开启时操作指令应越过入口门槛并生成计划")
            # ② ★第34批 T1：全步失败 → 出口返回 None（而非旧的降级叙述）
            self.assertIsNone(_r, "全步失败应返回 None（P2-196）")
            self.assertTrue(any("返回 None" in _m for _m in _logs),
                            "应留下「全步失败 → None」的可观测日志")
        finally:
            config.ENABLE_MULTI_STEP_OPERATIONAL_ADMISSION = _orig_admission

    _q = "先打开设置，再点击蓝牙，然后配对设备。"

    def test_51_noise_still_returns_none(self):
        _iw = PulseInnerWorld.__new__(PulseInnerWorld)
        _iw._log = lambda *a, **k: None
        _iw._true_multi_step_enabled = lambda: True
        self.assertIsNone(_iw._multi_step_execute("嗯"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
