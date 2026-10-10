# -*- coding: utf-8 -*-
"""★主线第90批 T-90b：日志问题「调用点定位」4 条既有 bug 修复的门控单测。

被测对象 = **真实源码**（`LogAnalyzer` / `SelfInspector`），不复刻任何逻辑。
`.bak_batch90` 仅用于「改前行为对照」，缺失时 skipTest；
改前算法的**判据本身**同时内联固化在本文件（`_legacy_*`），
这样「红」的证据不依赖 git-ignored 的 scratch 目录。

4 条 bug（任务书 T-90b 原文）：
  bug#1 `LogAnalyzer.py` 调用定位时 message 还是空串（填充在其后两行），
        且 `_locate_attempted` 使定位「只试一次、永不重试」；
  bug#2 `self_inspector.py` 1c 段只用中文器官名索引（243 条），
        真实日志标签多为**类名** ⇒ 恒不命中；第87批建好的全项目类索引（1007 类）
        从未接上来；
  bug#3 中文标签匹配写的是**前缀**关系（「胃」vs「脉冲驱动胃」不命中）；
  bug#4 `LogAnalyzer.py` 取 Traceback **第一帧** = 调用层，而非致错点。

覆盖六组：
  A. bug#1 时序 + 有界重试；   B. bug#3 前缀→包含；
  C. bug#2 类索引接入；         D. bug#4 首帧→末帧；
  E. 定位覆盖率（真实日志）；   F. 开关契约与逐字回退。
"""
import importlib.machinery
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.evolution.LogAnalyzer import (
    _TRACEBACK_FILE_RE,
    LogAnalyzer,
    _m90_log_locate_v2_on,
)
from nucleus.self_inspector import SelfInspector

_SWITCH = "ENABLE_M90_LOG_LOCATE_V2"
_BAK = os.path.join(ROOT, ".bak_batch90")
_BAK_SI = os.path.join(_BAK, "nucleus", "self_inspector.py.bak")
_BAK_LA = os.path.join(_BAK, "nucleus", "evolution", "LogAnalyzer.py.bak")
_LOG = os.path.join(ROOT, "logs", "pulse.log")

_SYN_LOG = (
    "2026-09-20 14:00:31 [胃] ERROR: JSON 解析失败: Expecting value: line 1\n"
    "2026-09-20 14:00:32 [胃] ERROR: JSON 解析失败: Expecting value: line 1\n"
)

_TB3 = (
    "Traceback (most recent call last):\n"
    '  File "a_first_level1.py", line 11, in _level1\n'
    "    _level2()\n"
    '  File "b_second_level2.py", line 22, in _level2\n'
    "    _level3()\n"
    '  File "c_third_level3.py", line 33, in _level3\n'
    '    raise ValueError("boom")\n'
)

# 冻结清单：真实日志不可用时兜底（取自 T0 实测 logs/pulse.log 的 Top TAG）。
_FROZEN_TAGS = [
    "肝", "代码学习", "InfoField", "内在世界", "控制器", "兴趣模型", "框架",
    "潜意识", "胃", "PulseSnapshot", "肺", "PulseNodePool", "动机循环",
    "SafeEvolutionExecutor", "心脏", "双腿", "self_inspector", "PatchManager",
    "verification_learning_hub", "pulse",
]

_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})\s+\[([^\]]{1,60})\]")


# ---------------------------------------------------------- 改前算法（内联固化）
def _legacy_should_locate(issue: dict) -> bool:
    """改前判据：`not file_path and not _locate_attempted`（试一次即永久封死）。"""
    return not issue.get("file_path") and not issue.get("_locate_attempted")


def _legacy_pair_prefix(cn: str, tag: str) -> bool:
    """改前模糊匹配的**单对**判据：双向 startswith。"""
    return cn.startswith(tag) or tag.startswith(cn)


def _legacy_cn_prefix_hit(tag: str, idx: dict) -> bool:
    """改前中文名匹配：exact → 遍历索引取双向 startswith（**无**包含、**无**类索引）。"""
    if tag in idx:
        return True
    for _cn in idx:
        if _legacy_pair_prefix(_cn, tag):
            return True
    return False


def _legacy_pick_frame(frames: list):
    """改前取帧：`.search()` = 第一帧 = 最外层调用者。"""
    return frames[0] if frames else None


# ------------------------------------------------------------------ 加载辅助
def _load(path, name):
    if not os.path.isfile(path):
        return None
    _ld = importlib.machinery.SourceFileLoader(name, path)
    _sp = importlib.util.spec_from_loader(_ld.name, _ld)
    _mod = importlib.util.module_from_spec(_sp)
    _ld.exec_module(_mod)
    return _mod


class _Switch:
    def __init__(self, value):
        self._value = value
        self._old = None
        self._had = False

    def __enter__(self):
        self._had = hasattr(config, _SWITCH)
        self._old = getattr(config, _SWITCH, None)
        setattr(config, _SWITCH, self._value)
        return self

    def __exit__(self, *a):
        if self._had:
            setattr(config, _SWITCH, self._old)
        else:
            try:
                delattr(config, _SWITCH)
            except AttributeError:
                pass
        return False


_INSPECTOR = None


def _inspector() -> SelfInspector:
    """全项目类索引构建约 6.8s ⇒ 用例间复用同一实例（索引自带缓存）。"""
    global _INSPECTOR
    if _INSPECTOR is None:
        _INSPECTOR = SelfInspector()
        _INSPECTOR._project_root = ROOT
    return _INSPECTOR


def _spy_scan(la_mod, tag: str):
    """跑真 `_scan_log_file`，用 spy 子类捕获 `_locate_for_issue` 实收的 (organ, msg)。"""
    _seen = []

    class _Spy(la_mod.LogAnalyzer):
        def _locate_for_issue(self, organ, msg):
            _seen.append((organ, msg))
            return None          # 强制不命中，专测「传了什么参数」

    _fd, _path = tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(_fd, "w", encoding="utf-8", newline="\n") as _f:
            _f.write(_SYN_LOG)
        _la = _Spy(ROOT)
        _issues: dict = {}
        _la._scan_log_file(_path, _issues)
    finally:
        try:
            os.remove(_path)
        except OSError:
            pass
    return _seen, list(_issues.values()), _la


def _run_runtime_metrics(la_mod):
    """跑真 `_scan_runtime_metrics`，注入三层嵌套 traceback，返回问题列表。

    载荷模拟真实 error_snapshot：`traceback`（嵌套三层）+ `error`（异常摘要）。
    """
    import nucleus.runtime_metrics as _rtm

    class _Fake:
        def get_snapshot(self):
            return {"error_snapshots": [{"traceback": _TB3, "timestamp": 1.0,
                                         "error": "ValueError: boom",
                                         "pulse_type": "unit_test"}]}

    _la = la_mod.LogAnalyzer(ROOT)
    _issues: dict = {}
    with mock.patch.object(_rtm, "get_runtime_metrics", lambda: _Fake()):
        _la._scan_runtime_metrics(_issues)
    return list(_issues.values())


def _real_tags():
    """真实日志的唯一 TAG 按频次降序；日志不可用时回落到冻结清单。"""
    if not os.path.isfile(_LOG):
        return list(_FROZEN_TAGS), False
    _cnt = {}
    try:
        for _l in io.open(_LOG, encoding="utf-8", errors="ignore"):
            _m = _LINE_RE.match(_l)
            if _m:
                _cnt[_m.group(2)] = _cnt.get(_m.group(2), 0) + 1
    except OSError:
        return list(_FROZEN_TAGS), False
    if not _cnt:
        return list(_FROZEN_TAGS), False
    return sorted(_cnt, key=lambda k: -_cnt[k]), True


# ============================================================ A bug#1
class TestT90bBug1MessageTimingAndRetry(unittest.TestCase):
    """bug#1：message 必须**先**填、定位后**有界重试**。"""

    def test_10_legacy_first_call_receives_empty_message(self):
        """★先红：改前首次定位拿到的 message 是空串（内联判据 + 真跑改前实现）。"""
        # (a) 判据层：改前在 message 为空时**照样**发起定位
        self.assertTrue(_legacy_should_locate({"message": "", "_locate_attempted": False}))
        # (b) 端到端：真跑改前的 LogAnalyzer
        _leg = _load(_BAK_LA, "m90_legacy_loganalyzer_b1")
        if _leg is None:
            self.skipTest(".bak_batch90 缺失，跳过改前端到端对照")
        _seen, _issues, _ = _spy_scan(_leg, "legacy")
        self.assertEqual(_seen[0][1], "", "改前首调 message 应为空串（红）")
        self.assertTrue(_issues[0]["_locate_attempted"])

    def test_11_new_first_call_receives_real_message(self):
        """★后绿：改后首次定位拿到的 message 非空，且带得动方法名推断。"""
        _seen, _issues, _la = _spy_scan(sys.modules[LogAnalyzer.__module__], "now")
        self.assertEqual(len(_seen), 1, "两行同 key 日志只应触发一次定位：{}".format(_seen))
        self.assertEqual(_seen[0][0], "胃")
        self.assertTrue(_seen[0][1].strip(), "改后首调 message 不得为空（绿）")
        self.assertIn("JSON 解析失败", _seen[0][1])
        # message 非空 ⇒ 方法名推断链路才可能产出
        _insp = _inspector()
        self.assertIsInstance(_insp.guess_method_from_message("胃", _seen[0][1]), str)
        self.assertTrue(_issues[0]["message"].strip())

    def test_12_message_survives_failed_locate(self):
        """定位失败也不能让 message 丢失（下游 SafeEvolutionExecutor 靠它归因）。"""
        _, _issues, _ = _spy_scan(sys.modules[LogAnalyzer.__module__], "now")
        self.assertTrue(_issues[0]["message"].strip())

    def test_20_defers_when_message_empty(self):
        """message 为空 → 不发起定位（等 message 到位再试，而不是白试一次）。"""
        _la = LogAnalyzer(ROOT)
        self.assertFalse(_la._m90_should_locate(
            {"message": "", "_locate_attempted": False, "_locate_attempts": 0}))

    def test_21_retries_after_message_refined(self):
        """message 被细化后可再试（改前是永久封死）。"""
        _la = LogAnalyzer(ROOT)
        _iss = {"message": "第一版消息", "_locate_attempted": True,
                "_locate_attempts": 1, "_locate_tried_msg": "第一版消息"}
        self.assertFalse(_legacy_should_locate(_iss), "改前应封死（红）")
        _iss["message"] = "细化后的消息（含方法名）"
        self.assertTrue(_la._m90_should_locate(_iss), "改后应允许重试一次（绿）")

    def test_22_no_retry_when_message_unchanged(self):
        """message 未变 → 不重复劳动（避免纯开销）。"""
        _la = LogAnalyzer(ROOT)
        self.assertFalse(_la._m90_should_locate(
            {"message": "同一句", "_locate_attempted": True,
             "_locate_attempts": 1, "_locate_tried_msg": "同一句"}))

    def test_23_stops_at_max_attempts(self):
        """重试**有界**：达到上限后不再试（不是无限重试）。"""
        _la = LogAnalyzer(ROOT)
        _iss = {"message": "m2", "_locate_attempted": True,
                "_locate_attempts": LogAnalyzer._LOCATE_MAX_ATTEMPTS,
                "_locate_tried_msg": "m1"}
        self.assertFalse(_la._m90_should_locate(_iss))
        self.assertGreaterEqual(LogAnalyzer._LOCATE_MAX_ATTEMPTS, 2,
                                "上限至少 2 才谈得上「重试」")

    def test_24_already_located_never_retries(self):
        """已定位到文件 → 绝不重试（与改前一致，不引入额外开销）。"""
        _la = LogAnalyzer(ROOT)
        self.assertFalse(_la._m90_should_locate(
            {"file_path": "organs/body/PulseStomach.py", "message": "x",
             "_locate_attempted": False, "_locate_attempts": 0}))

    def test_25_mark_located_records_attempt(self):
        """登记尝试：次数 +1、记录 message、兼容旧字段 `_locate_attempted`。"""
        _la = LogAnalyzer(ROOT)
        _iss = {"message": "abc", "_locate_attempts": 0}
        _la._m90_mark_located(_iss)
        self.assertTrue(_iss["_locate_attempted"])
        self.assertEqual(_iss["_locate_attempts"], 1)
        self.assertEqual(_iss["_locate_tried_msg"], "abc")
        _la._m90_mark_located(_iss)
        self.assertEqual(_iss["_locate_attempts"], 2)

    def test_26_apply_location_writes_five_fields(self):
        """写回语义与改前逐字一致（5 字段 + 置信度阈值判「需人工确认」）。"""
        _la = LogAnalyzer(ROOT)
        _iss = {"message": "m"}
        _la._m90_apply_location(_iss, {"file": "a/b.py", "line": 7,
                                       "method": "f", "confidence": 0.4})
        self.assertEqual(_iss["file_path"], "a/b.py")
        self.assertEqual(_iss["line"], 7)
        self.assertEqual(_iss["method"], "f")
        self.assertAlmostEqual(_iss["locate_confidence"], 0.4)
        self.assertTrue(_iss["needs_human_confirm"],
                        "0.4 < 阈值 {:.1f} 应标记需人工确认".format(LogAnalyzer._LOCATE_CONF_THRESHOLD))
        # 命中高置信度 → 不需人工确认
        _iss2 = {"message": "m"}
        _la._m90_apply_location(_iss2, {"file": "a/b.py", "confidence": 0.9})
        self.assertFalse(_iss2["needs_human_confirm"])
        # 空结果不写任何字段
        _iss3 = {"message": "m"}
        _la._m90_apply_location(_iss3, None)
        self.assertNotIn("file_path", _iss3)

    def test_27_issues_seeded_with_retry_counters(self):
        """`_get_or_create` 必须初始化重试计数与上次 message（否则 25/21 失效）。"""
        _la = LogAnalyzer(ROOT)
        _issues: dict = {}
        _iss = _la._get_or_create(_issues, "k", "", 0, "", "ERROR")
        self.assertEqual(_iss["_locate_attempts"], 0)
        self.assertEqual(_iss["_locate_tried_msg"], "")


# ============================================================ B bug#3
class TestT90bBug3PrefixToContains(unittest.TestCase):
    """bug#3：中文标签与头部名常是**包含**关系而非前缀。"""

    def test_30_legacy_prefix_miss_on_real_tags(self):
        """★先红：真实日志里出现过的「包含型」标签，改前双向 startswith 皆不命中。

        这些标签取自 T0 实测（它们确实在 logs/pulse.log 里高频出现），
        是**硬编码的真实样本**，不是从「不命中」这个性质反推出来的 ——
        故本断言非空转。
        """
        _idx = _inspector()._build_organ_name_index()
        for _tag in ("胃", "心脏", "肺", "框架"):
            # 前提：确有某个头部中文名**包含**该标签
            _hosts = [_cn for _cn in _idx if _tag in _cn]
            self.assertTrue(_hosts, "{!r} 不在任何头部名里 ⇒ 样本失效".format(_tag))
            # 结论：改前算法对它们恒不命中（红）
            self.assertFalse(_legacy_cn_prefix_hit(_tag, _idx),
                             "改前算法不该命中 {!r}（红）".format(_tag))
            # 且「包含」而「非前缀」是常态：逐对确认宿主名并非以该标签开头
            self.assertFalse(any(_legacy_pair_prefix(_cn, _tag) for _cn in _hosts),
                             "{!r} 其实是前缀型，样本选错".format(_tag))

    def test_31_suffix_inclusion_pairs_exist(self):
        """结构性事实（动态）：索引里普遍存在「后缀包含」型键对，
        而改前的双向 startswith 对它们恒为假 —— 说明 bug#3 不是孤例。"""
        _idx = _inspector()._build_organ_name_index()
        _pairs = []
        for _cn in _idx:
            for _k in (2, 3):
                if len(_cn) > _k:
                    _short = _cn[-_k:]
                    if _short in _cn and not _legacy_pair_prefix(_cn, _short):
                        _pairs.append((_cn, _short))
        self.assertGreaterEqual(len(_pairs), 3,
                                "索引里「后缀包含」型键对只有 %d 个" % len(_pairs))
        for _cn, _short in _pairs:
            self.assertTrue(_cn.endswith(_short))

    def test_32_new_contains_hit(self):
        """★后绿：改后同一批包含型标签能命中真实文件，且文件存在。"""
        _insp = _inspector()
        _idx = _insp._build_organ_name_index()
        _has = [c for c in _idx if c.startswith("脉冲驱动")]
        self.assertTrue(_has, "索引里应有「脉冲驱动*」头部名")
        for _cn in _has:
            _short = _cn.replace("脉冲驱动", "")
            _r = _insp.locate_issue(_short, "")
            self.assertTrue(_r.get("file"), "{!r} 应命中（绿）".format(_short))
            self.assertTrue(os.path.isfile(os.path.join(ROOT, _r["file"])),
                            "命中路径必须真实存在：{}".format(_r["file"]))

    def test_33_known_real_cases(self):
        """真实日志里出现过的包含型标签（胃/心脏/肺/框架）逐一命中。"""
        _insp = _inspector()
        for _tag in ("胃", "心脏", "肺", "框架"):
            _r = _insp.locate_issue(_tag, "")
            self.assertTrue(_r.get("file"), "{!r} 应命中".format(_tag))


# ============================================================ C bug#2
class TestT90bBug2ClassIndexWired(unittest.TestCase):
    """bug#2：类名标签必须走「全项目类索引」。"""

    _CLS_TAGS = ["InfoField", "PulseSnapshot", "PulseNodePool",
                 "SafeEvolutionExecutor", "SelfAwarenessDailyScheduler"]

    def test_40_class_name_tags_hit_real_files(self):
        """类名标签 → 命中真实文件，且类名确在该文件里。"""
        _insp = _inspector()
        for _tag in self._CLS_TAGS:
            _r = _insp.locate_issue(_tag, "")
            self.assertTrue(_r.get("file"), "{!r} 应命中（绿）".format(_tag))
            _abs = os.path.join(ROOT, _r["file"])
            self.assertTrue(os.path.isfile(_abs), "命中路径不存在：{}".format(_abs))
            _src = io.open(_abs, encoding="utf-8", errors="ignore").read()
            self.assertIn("class {}".format(_tag), _src,
                          "{} 里查无 `class {}`".format(_r["file"], _tag))

    def test_41_legacy_could_not_hit_class_names(self):
        """★先红：改前实现（真跑）对类名标签恒 MISS，且中文索引里根本没有这类键。"""
        _idx = _inspector()._build_organ_name_index()
        for _tag in self._CLS_TAGS:
            self.assertNotIn(_tag, _idx, "中文名索引不该含类名 {!r}".format(_tag))
            self.assertFalse(_legacy_cn_prefix_hit(_tag, _idx),
                             "改前算法不该命中 {!r}（红）".format(_tag))
        _leg = _load(_BAK_SI, "m90_legacy_self_inspector_b2")
        if _leg is None:
            self.skipTest(".bak_batch90 缺失，跳过改前真跑对照")
        _old = _leg.SelfInspector()
        _old._project_root = ROOT
        _miss = [_t for _t in self._CLS_TAGS if not _old.locate_issue(_t, "").get("file")]
        self.assertEqual(_miss, self._CLS_TAGS,
                         "改前应全部 MISS，实际只 MISS {}".format(_miss))

    def test_42_confidence_priority_ordering(self):
        """置信度优先级：消息带路径 > 类索引(0.75) > 中文名精确(0.7) > 模糊包含(0.6)。

        注：`locate_issue` 尾部有「方法名找到 +0.15 / 找不到 -0.2」的调整，
        故此处一律用**不含方法名**的消息，取到的是纯粹的文件级置信度；
        并且断言的是**序关系**（不写死绝对值），避免把调整系数写进用例。
        """
        _insp = _inspector()
        # 精确命中样本取自真实索引键（不硬编码，索引演化时自动跟随）
        _exact_tag = next(k for k in _insp._build_organ_name_index() if len(k) >= 3)
        _c_path = _insp.locate_issue("胃", 'File "organs/body/PulseStomach.py"')["confidence"]
        _c_cls = _insp.locate_issue("InfoField", "")["confidence"]
        _c_cn = _insp.locate_issue(_exact_tag, "")["confidence"]
        _c_fuzz = _insp.locate_issue("胃", "")["confidence"]
        self.assertGreater(_c_path, _c_cls, "带路径({}) 应高于类索引({})".format(_c_path, _c_cls))
        self.assertGreater(_c_cls, _c_cn,
                           "类索引({}) 应高于中文名精确({}, tag={})".format(_c_cls, _c_cn, _exact_tag))
        self.assertGreater(_c_cn, _c_fuzz, "中文名精确({}) 应高于模糊包含({})".format(_c_cn, _c_fuzz))
        self.assertGreaterEqual(_c_fuzz, 0.5,
                                "模糊包含 {} 低于「需人工确认」阈值，会挡掉自动修复".format(_c_fuzz))

    def test_43_class_tag_also_yields_method(self):
        """类名标签命中后还能给出方法名（供补丁素材提取使用）。"""
        _insp = _inspector()
        _m = _insp.guess_method_from_message("InfoField", "KeyError: 'x'")
        self.assertIsInstance(_m, str)


# ============================================================ D bug#4
class TestT90bBug4TracebackLastFrame(unittest.TestCase):
    """bug#4：取 Traceback **最后一帧**（致错点），而非第一帧（调用层）。"""

    def test_50_regex_group_order_is_file_line_func(self):
        """自检：`findall` 的元组顺序必须是 (file, line, func)。"""
        _frames = _TRACEBACK_FILE_RE.findall(_TB3)
        self.assertEqual(len(_frames), 3, "应识别出 3 帧：{}".format(_frames))
        self.assertEqual(_frames[0][0], "a_first_level1.py")
        self.assertEqual(_frames[-1][0], "c_third_level3.py")
        self.assertEqual(_frames[-1][1], "33")
        self.assertEqual(_frames[-1][2], "_level3")

    def test_51_legacy_picks_caller_frame(self):
        """★先红：改前算法取首帧 = 最外层调用者（红）。"""
        _frames = _TRACEBACK_FILE_RE.findall(_TB3)
        self.assertEqual(_legacy_pick_frame(_frames)[2], "_level1")
        _leg = _load(_BAK_LA, "m90_legacy_loganalyzer_b4")
        if _leg is None:
            self.skipTest(".bak_batch90 缺失，跳过改前真跑对照")
        _r = _run_runtime_metrics(_leg)
        self.assertEqual(len(_r), 1)
        self.assertEqual(_r[0]["method"], "_level1", "改前应取调用层（红）")

    def test_52_new_picks_error_site_frame(self):
        """★后绿：改后真跑取末帧 = 致错点（绿）。"""
        _r = _run_runtime_metrics(sys.modules[LogAnalyzer.__module__])
        self.assertEqual(len(_r), 1, "三层嵌套应只产出一条问题：{}".format(_r))
        _iss = _r[0]
        self.assertEqual(_iss["method"], "_level3")
        self.assertEqual(_iss["line"], 33)
        self.assertEqual(_iss["file_path"], "c_third_level3.py")
        self.assertEqual(_iss["error_type"], "Exception")
        # message 走 `error` 字段（与文件位置无关），此处确认载荷未被丢弃
        self.assertIn("ValueError", _iss["message"])
        self.assertEqual(_iss["pulse_type"], "unit_test")

    def test_53_switch_off_restores_first_frame(self):
        """开关关闭 → 逐字回到首帧行为。"""
        with _Switch(False):
            _r = _run_runtime_metrics(sys.modules[LogAnalyzer.__module__])
            self.assertEqual(_r[0]["method"], "_level1")


# ============================================================ E 覆盖率
class TestT90bLocateCoverage(unittest.TestCase):
    """验收线：日志定位覆盖率 ≥ 50%（任务书 T-90b）。"""

    @classmethod
    def setUpClass(cls):
        cls.tags, cls.is_real = _real_tags()
        cls.insp = _inspector()
        cls.idx = cls.insp._build_organ_name_index()

    def test_60_coverage_at_least_50pct(self):
        _hit = sum(1 for _t in self.tags if self.insp.locate_issue(_t, "").get("file"))
        _rate = float(_hit) / len(self.tags)
        self.assertGreaterEqual(
            _rate, 0.50,
            "定位覆盖率 %.1f%%（%d/%d，数据源=%s）低于验收线 50%%"
            % (100.0 * _rate, _hit, len(self.tags),
               "真日志" if self.is_real else "冻结清单"))

    def test_61_coverage_improves_over_legacy(self):
        """改后覆盖率必须**严格优于**改前算法（否则修复无收益）。"""
        _old = sum(1 for _t in self.tags if _legacy_cn_prefix_hit(_t, self.idx))
        _new = sum(1 for _t in self.tags if self.insp.locate_issue(_t, "").get("file"))
        self.assertGreater(_new, _old,
                           "改前 %d/%d → 改后 %d/%d，无提升" % (_old, len(self.tags), _new, len(self.tags)))

    def test_62_class_index_contributes_meaningfully(self):
        """类索引本身必须贡献显著命中（证明 bug#2 的修复不是装饰）。"""
        _cls = sum(1 for _t in self.tags if self.insp._lookup_class_in_project(_t))
        self.assertGreaterEqual(_cls, 10,
                                "类索引只贡献 %d 个标签，bug#2 修复效果可疑" % _cls)

    def test_63_reports_remaining_misses(self):
        """仍不可定位的标签必须**可见**（不隐藏缺口，供第91批主能力排期）。"""
        _miss = [_t for _t in self.tags if not self.insp.locate_issue(_t, "").get("file")]
        _msg = "仍不可定位 %d/%d: %s" % (len(_miss), len(self.tags), _miss)
        self.assertLess(len(_miss), len(self.tags), _msg)
        if os.environ.get("M90_SHOW_MISSES"):
            print("\n" + _msg)


# ============================================================ F 开关契约
class TestT90bSwitchContract(unittest.TestCase):
    def test_70_default_on(self):
        self.assertTrue(_m90_log_locate_v2_on())

    def test_71_config_py_registered_by_m91(self):
        """★契约变更（第91批 T-91c）：本开关已**正式登记**进 config.py，默认 True。  # _m91_t91c_switch_registered

        ★历史：第90批的批内红线是「不改 config.py」⇒ 当时断言 `assertNotIn`；
        第91批任务书 T-91c 解除该约束（登记默认值不变，仍为 True）⇒ 断言反转。
        """
        _src = io.open(os.path.join(ROOT, "config.py"), encoding="utf-8",
                       errors="ignore").read()
        self.assertIn(_SWITCH, _src,
                      "第91批 T-91c 起 {} 必须正式登记在 config.py".format(_SWITCH))
        self.assertIn("{} = True".format(_SWITCH), _src, "登记默认值必须为 True（与登记前一致）")
        import config as _cfg_mod2
        self.assertTrue(getattr(_cfg_mod2, _SWITCH))

    def test_72_switch_off_legacy_verdict_for_should_locate(self):
        """关闭 → `_m90_should_locate` 逐字回到改前判据（对同一 issue 结论一致）。"""
        _la = LogAnalyzer(ROOT)
        for _iss in ({"message": "", "_locate_attempted": False},
                     {"message": "m", "_locate_attempted": False},
                     {"message": "m", "_locate_attempted": True},
                     {"message": "m", "_locate_attempted": True,
                      "_locate_attempts": 5, "_locate_tried_msg": "m"},
                     {"file_path": "a.py", "message": "m", "_locate_attempted": False}):
            with _Switch(False):
                self.assertEqual(_la._m90_should_locate(dict(_iss)),
                                 _legacy_should_locate(dict(_iss)),
                                 "开关关闭时判据应与改前一致：{}".format(_iss))

    def test_73_switch_on_defers_and_retries(self):
        """开启 → 与改前**不同**（空 message 不试；message 变化可重试）。"""
        _la = LogAnalyzer(ROOT)
        _a = {"message": "", "_locate_attempted": False}
        self.assertNotEqual(_la._m90_should_locate(dict(_a)), _legacy_should_locate(dict(_a)))
        _b = {"message": "新消息", "_locate_attempted": True,
              "_locate_attempts": 1, "_locate_tried_msg": "旧消息"}
        self.assertNotEqual(_la._m90_should_locate(dict(_b)), _legacy_should_locate(dict(_b)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
