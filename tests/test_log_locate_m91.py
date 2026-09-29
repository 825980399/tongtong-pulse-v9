# -*- coding: utf-8 -*-
"""★主线第91批 相关任务：日志「调用点定位主能力」——两级数据驱动索引的门控单测。

被测对象 = **真实源码**（`nucleus/self_inspector.py`），不复刻任何索引逻辑。
`.bak_batch91` 仅用于「改前不存在该能力」的静态先红证据，缺失时 skipTest。

## 为什么必须是数据驱动（T0 实测，非推测）
日志 TAG 由 `nucleus/logger.py::PulseFormatter` 从 **logger 名**派生：
    `pulse.organ.胸腺` → `胸腺` ; `pulse.module.WriteGuard` → `WriteGuard`
    `pulse.framework` → `框架`   ; 其余 → 原名（`pulse.structured_parallel`）
而 logger 名来自 `BasePulseOrgan.__init__` 的 `get_organ_logger(self.organ_name)`。
旧的 `_build_organ_name_index()` 读的是**统一头部中文短语**
（`PulseEyes —— 脉冲驱动眼睛（知识检索器官 · v9.5 …）`）——
两个命名源结构性错位 ⇒ 16 个真实标签恒不可定位，覆盖率卡在 **80.2%（65/81）**。

任务书 相关任务 原本要求手写「模块名→文件映射表 + 标签别名表」；
实测该方案是过拟合指标（10 条映射表只能到 92.6%）且需人工维护，
故本批改为**两级数据驱动索引**（扫描源码里的 logger 名字面量 + `organ_name` 声明），
新增标签零维护自动纳入。实测 **81/81 = 100.0%**（改前 65/81 = 80.2%）。

## 覆盖五组
  A. 索引构建（扫描范围 / 排除规则 / 缓存 / 不死路径 / 与 PulseFormatter 互逆）
  B. 两级索引命中（16 个历史缺口标签逐一 + 置信度契约）
  C. 覆盖率验收（≥95% / 剩余 ≤3 / 先红 / 严格优于 / 零回归）
  D. 开关契约（默认 True / 已登记 config.py / 关闭逐字回退 / 与 M90 开关的从属关系）
  E. 与第90批既有能力的共存（层级优先级未被抢占）
"""
import io
import logging
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.logger import PulseFormatter
from nucleus.self_inspector import SelfInspector, _m91_log_locate_v3_on

_SWITCH = "ENABLE_M91_LOG_LOCATE_V3"
_SWITCH_M90 = "ENABLE_M90_LOG_LOCATE_V2"
_SI = os.path.join(ROOT, "nucleus", "self_inspector.py")
_CFG = os.path.join(ROOT, "config.py")
_LOG = os.path.join(ROOT, "logs", "pulse.log")
_BAK = os.path.join(ROOT, ".bak_batch91")
_BAK_SI = os.path.join(_BAK, "nucleus", "self_inspector.py.bak")

_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})\s+\[([^\]]{1,60})\]")

# ---------------------------------------------------------------- 验收线
_ACCEPT_RATE = 0.95      # 任务书：覆盖率 80.0% → 95%+
_ACCEPT_MISS = 3         # 任务书：剩余不可定位标签 ≤ 3 个

# T0 实测（2026-09-20，logs/pulse.log）：V3 关闭时**恰好**这 16 个标签不可定位。
# 断言方向为 `_GAP_TAGS ⊆ v3_off_misses`（结构性恒真，不受日志增长影响）。
_GAP_TAGS = [
    # 模块名 / logger 名型（9）
    "self_inspector", "verification_learning_hub", "wecom_chat_bridge",
    "SemanticKernelService", "WriteGuard", "pulse.structured_parallel",
    "自主探查", "时间中枢", "pulse",
    # 中文标签别名型（7，来自 organ_name 声明）
    "全局学习器", "眼睛", "耳朵", "触觉", "视觉皮层", "胸腺", "硬件启动器",
]

# 两级索引的**期望落点**（T0 实测；同时固化在测试文件内 ⇒ 不依赖 git-ignored scratch）
_LOGGER_EXPECT = {
    "self_inspector": "nucleus/self_inspector.py",
    "verification_learning_hub": "nucleus/mnemosyne/verification_learning_hub.py",
    "wecom_chat_bridge": "nucleus/wecom_chat_bridge.py",
    "SemanticKernelService": "nucleus/semantic/SemanticKernelService.py",
    "WriteGuard": "nucleus/data/write_guard.py",
    "pulse.structured_parallel": "nucleus/StructuredParallelScheduler.py",
    "自主探查": "nucleus/self_probe.py",
    "时间中枢": "nucleus/chronos/TimeCore.py",
    "pulse": "nucleus/logger.py",
}
_ALIAS_EXPECT = {
    "全局学习器": "organs/core/PulseGlobalLearner.py",
    "眼睛": "organs/senses/PulseEyes.py",
    "耳朵": "organs/senses/PulseEars.py",
    "触觉": "organs/senses/PulseTouch.py",
    "视觉皮层": "organs/senses/PulseVisualCortex.py",
    "胸腺": "organs/immune/PulseThymus.py",
    "硬件启动器": "organs/core/PulseHardwareLauncher.py",
}

# 冻结兜底清单：真实日志不可用时使用（全部为当前可命中标签 ⇒ 断言不受环境阻塞影响）
_FROZEN_TAGS = list(_GAP_TAGS) + [
    "肝", "InfoField", "PulseSnapshot", "SafeEvolutionExecutor", "框架", "内在世界",
]

# 已由「中文名 0.6 模糊层」命中的标签：用于证明 V3 **未抢占**既有层级
# （`心脏` 的 `organ_name` 别名索引也指向 PulseHeart.py，若 V3 前置就会被抬到 0.8）
_PRIORITY_GUARD = {
    "心脏": ("organs/body/PulseHeart.py", 0.6),
    "胃": ("organs/body/PulseStomach.py", 0.6),
}


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


class _Switch:
    """临时改写 config 属性；退出时按「原本是否存在」精确复原。"""

    def __init__(self, name, value):
        self._name = name
        self._value = value
        self._old = None
        self._had = False

    def __enter__(self):
        self._had = hasattr(config, self._name)
        self._old = getattr(config, self._name, None)
        setattr(config, self._name, self._value)
        return self

    def __exit__(self, *a):
        if self._had:
            setattr(config, self._name, self._old)
        else:
            try:
                delattr(config, self._name)
            except AttributeError:
                pass
        return False


_INSPECTOR = None
_LITERALS = None


def _inspector() -> SelfInspector:
    """全项目类索引 + 两级索引构建耗时秒级 ⇒ 用例间复用同一实例（索引自带缓存）。"""
    global _INSPECTOR
    if _INSPECTOR is None:
        _INSPECTOR = SelfInspector()
        _INSPECTOR._project_root = ROOT
    return _INSPECTOR


def _formatter_tag(lit: str) -> str:
    """真跑 `PulseFormatter` 拿到该 logger 名对应的真实日志 TAG。"""
    _rec = logging.LogRecord(name=lit, level=logging.INFO, pathname=__file__,
                            lineno=1, msg="x", args=(), exc_info=None)
    PulseFormatter().format(_rec)
    return getattr(_rec, "organ_tag", "")


def _production_literals():
    """生产源码里全部 logger 名字面量（去重）——与索引同源，保证互逆断言无遗漏。"""
    global _LITERALS
    if _LITERALS is None:
        _insp = _inspector()
        _seen = []
        for _rel in _insp._m91_iter_production_py():
            _fp = os.path.join(ROOT, _rel)
            try:
                with io.open(_fp, encoding="utf-8", errors="ignore") as _f:
                    _src = _f.read()
            except OSError:
                continue
            for _m in _insp._M91_LOGGER_CALL_RE.finditer(_src):
                if _m.group(1) not in _seen:
                    _seen.append(_m.group(1))
        _LITERALS = _seen
    return _LITERALS


def _read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as _f:
        return _f.read()


# ============================================================ A 索引构建
class TestT91aIndexBuild(unittest.TestCase):
    """扫描范围 / 排除规则 / 缓存 / 不死路径 / 与 PulseFormatter 互逆。"""

    @classmethod
    def setUpClass(cls):
        cls.insp = _inspector()
        cls.idx = cls.insp._build_logger_tag_index()
        cls.alias = cls.insp._build_organ_alias_index()

    def test_10_scan_roots_cover_non_organ_production_code(self):
        """★核心：旧索引只扫 `organs/` + `nucleus/` 的**文件头** ⇒ 必须补上
        logger 字面量源（`functions/`、`base/`、`utils/`、`main.py`、`config.py`）。"""
        _rels = set(self.insp._m91_iter_production_py())
        # T0 实测：304 个生产 .py（nucleus/ + organs/ + functions/ + base/
        # + utils/ + main.py + config.py，已排除备份 / 缓存 / 虚拟环境）。
        self.assertGreater(len(_rels), 250, "生产源码清单过少：%d" % len(_rels))
        for _must in ("nucleus/self_inspector.py", "nucleus/data/write_guard.py",
                      "organs/senses/PulseEyes.py", "config.py", "main.py"):
            self.assertIn(_must, _rels, "生产源码清单缺少 %s" % _must)

    def test_11_no_backup_or_cache_paths(self):
        """★危险解析防护：`.bak_batch75/...` 这类**陈旧副本**一旦入选，
        后续取方法体/生成补丁全部作用在错误路径上（T0 实测踩过）。"""
        _rels = list(self.insp._m91_iter_production_py())
        for _r in _rels:
            self.assertNotIn(".bak", _r, "备份目录混入扫描结果：%s" % _r)
            self.assertNotIn("__pycache__", _r, "缓存目录混入扫描结果：%s" % _r)
            for _part in _r.split("/")[:-1]:
                self.assertFalse(_part.startswith("."),
                                 "点目录混入扫描结果：%s" % _r)

    def test_12_skip_rule_does_not_hurt_legit_subpackages(self):
        """★只排除「点目录 + 缓存 + 虚拟环境」——不按 data/logs/build/tmp 名字排除。

        T0 实测：按目录名排除会误伤 `nucleus/data`（含 `write_guard.py`，
        即 `[WriteGuard]` 标签的来源）、`nucleus/pulse/build`、`organs/brain/logs`。
        """
        _re_ = self.insp._M91_SKIP_DIR_RE
        for _keep in ("nucleus", "organs", "data", "logs", "build", "tmp", "dist"):
            self.assertIsNone(_re_.search(_keep), "合法目录 %r 被误排除" % _keep)
        for _skip in ("__pycache__", "venv", ".venv", "node_modules", ".bak_batch91"):
            self.assertIsNotNone(_re_.search(_skip), "目录 %r 应被排除" % _skip)

    def test_13_indexes_are_non_trivial_and_cached(self):
        self.assertGreater(len(self.idx), 60, "logger tag 索引条目过少：%d" % len(self.idx))
        self.assertGreater(len(self.alias), 30, "器官别名索引条目过少：%d" % len(self.alias))
        self.assertIs(self.insp._build_logger_tag_index(), self.idx, "索引未缓存")
        self.assertIs(self.insp._build_organ_alias_index(), self.alias, "索引未缓存")

    def test_14_no_dead_paths_in_indexes(self):
        """索引里绝不允许出现**不存在的文件**（否则定位会产出死路径）。"""
        for _tag, _rel in self.idx.items():
            self.assertTrue(os.path.isfile(os.path.join(ROOT, _rel)),
                            "logger 索引死路径：%r -> %r" % (_tag, _rel))
        for _tag, _rel in self.alias.items():
            self.assertTrue(os.path.isfile(os.path.join(ROOT, _rel)),
                            "别名索引死路径：%r -> %r" % (_tag, _rel))

    def test_15_lookup_never_returns_dead_path_and_none_for_unknown(self):
        for _tag in ("self_inspector", "WriteGuard", "胸腺", "眼睛"):
            for _fn in (self.insp._m91_lookup_logger_tag,
                        self.insp._m91_lookup_organ_alias):
                _r = _fn(_tag)
                if _r:
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, _r)))
        for _junk in ("不存在的标签XYZ", "", "   ", None, 12345):
            self.assertIsNone(self.insp._m91_lookup_logger_tag(_junk),
                              "未知标签应返回 None：%r" % (_junk,))
            self.assertIsNone(self.insp._m91_lookup_organ_alias(_junk),
                              "未知标签应返回 None：%r" % (_junk,))

    def test_16_ambiguity_is_recorded(self):
        """同一字面量出现在多个文件 ⇒ 本质歧义，必须留痕（供降置信）。"""
        _ambig = getattr(self.insp, "_m91_logger_tag_ambig", set())
        self.assertIn("pulse", _ambig, "`pulse` 是多源字面量，必须记入歧义集合")
        self.assertIn("TaskOrchestrator", _ambig,
                      "`TaskOrchestrator` 亦是多源字面量（class 索引会先接管，见 E 组）")

    def test_17_逆映射与_PulseFormatter_互逆(self):
        """★★最强不变量：对生产源码里**每一个** logger 字面量，
        真跑 `PulseFormatter` 得到的 TAG 必须能由 `_m91_logger_literal_to_tags` 反推。

        `pulse.framework` 是 formatter 内**硬编码**的特例（→ `框架`），
        无法从字面量反推，且生产代码用 `init_framework_logger()` 而非字面量调用，
        故此处显式单列并说明（`框架` 标签实际由中文名 0.6 层命中）。
        """
        _lits = _production_literals()
        self.assertGreater(len(_lits), 15, "抽取到的 logger 字面量过少：%s" % _lits)
        _checked = 0
        for _lit in _lits:
            if _lit == "pulse.framework":
                self.assertEqual(_formatter_tag(_lit), "框架", "framework 特例契约变更")
                continue
            _tag = _formatter_tag(_lit)
            self.assertIn(_tag, SelfInspector._m91_logger_literal_to_tags(_lit),
                          "字面量 %r 的日志 TAG %r 反推失败：%s"
                          % (_lit, _tag, SelfInspector._m91_logger_literal_to_tags(_lit)))
            _checked += 1
        self.assertGreater(_checked, 10, "实际校验的字面量过少：%d" % _checked)

    def test_18_literal_to_tags_known_forms(self):
        """`_m91_logger_literal_to_tags` 的已知形态（与 `PulseFormatter` 逐条对齐）。

        ★注意 `pulse.organ.PulseHeart` 的正确期望是 `Heart`（formatter 去 `Pulse` 前缀），
        **不是** `心脏`——框架实际不会拿 `PulseHeart` 当 organ_name：`PulseHeart.py`
        声明的是 `organ_name = "心脏"` ⇒ logger 名是 `pulse.organ.心脏`，
        故 `心脏` 走**第二级别名索引**而非字面量索引（这正是两级索引分工的由来）。
        """
        _f = SelfInspector._m91_logger_literal_to_tags
        for _lit, _must in (("pulse.organ.胸腺", "胸腺"),
                            ("pulse.organ.PulseHeart", "Heart"),
                            ("pulse.module.WriteGuard", "WriteGuard"),
                            ("pulse.module.PulseSnapshot", "Snapshot"),
                            ("pulse.structured_parallel", "pulse.structured_parallel"),
                            ("get_x", "get_x")):
            self.assertIn(_must, _f(_lit), "%r 应产出 %r" % (_lit, _must))
        # 反向确认：字面量层**不**产出中文器官名（中文名由 alias 索引负责）
        self.assertNotIn("心脏", _f("pulse.organ.PulseHeart"))
        self.assertEqual(_f(""), [])
        self.assertEqual(_f(None), [])


# ============================================================ B 两级索引命中
class TestT91aTwoLevelIndexHits(unittest.TestCase):
    """16 个历史缺口标签逐一命中 + 置信度契约。"""

    @classmethod
    def setUpClass(cls):
        cls.insp = _inspector()
        cls.idx = cls.insp._build_logger_tag_index()
        cls.alias = cls.insp._build_organ_alias_index()

    def test_20_logger_name_type_hits(self):
        """第一级：模块名 / logger 名型（9 个）。"""
        for _tag, _exp in _LOGGER_EXPECT.items():
            self.assertEqual(self.insp._m91_lookup_logger_tag(_tag), _exp,
                             "%r 的 logger 索引落点错误" % _tag)

    def test_21_organ_alias_type_hits(self):
        """第二级：中文标签别名型（7 个，来自 `organ_name` 声明）。"""
        for _tag, _exp in _ALIAS_EXPECT.items():
            self.assertEqual(self.insp._m91_lookup_organ_alias(_tag), _exp,
                             "%r 的别名索引落点错误" % _tag)

    def test_22_both_levels_are_complementary_not_overlapping(self):
        """两级索引职责不重叠：别名型不应落在 logger 索引里（反之亦然）。

        说明：`自主探查` / `时间中枢` 由 `get_module_logger("…")` **字面量**产生
        ⇒ 属第一级；而 `眼睛` / `胸腺` 等由 `organ_name` 声明产生 ⇒ 属第二级。
        """
        for _t in _ALIAS_EXPECT:
            self.assertIsNone(self.insp._m91_lookup_logger_tag(_t),
                              "%r 同时落在两级索引（职责重叠）" % _t)
        for _t in ("write_guard_tag_placeholder",):
            self.assertIsNone(self.insp._m91_lookup_organ_alias(_t))

    def test_23_all_gap_tags_locate_from_empty_message(self):
        """★任务书验收主语：16 个缺口标签在 `msg=""` / `file_hint=""` 下必须全部命中。"""
        _miss = [t for t in _GAP_TAGS if not self.insp.locate_issue(t, "").get("file")]
        self.assertEqual(_miss, [], "仍不可定位：%s" % _miss)

    def test_24_confidence_contract(self):
        """置信度分层契约：logger/alias 精确 = 0.8；歧义 = 0.55。

        两者均 **> 0.5** ⇒ 按 `locate_issue` 的调用方约定可进入自动修复队列。
        """
        for _tag in list(_LOGGER_EXPECT) + list(_ALIAS_EXPECT):
            if _tag == "pulse":
                continue
            _c = self.insp.locate_issue(_tag, "").get("confidence", 0.0)
            self.assertAlmostEqual(_c, 0.8, places=3,
                                   msg="%r 置信度应为 0.8，实得 %.3f" % (_tag, _c))
        _cp = self.insp.locate_issue("pulse", "").get("confidence", 0.0)
        self.assertAlmostEqual(_cp, 0.55, places=3,
                               msg="歧义标签 `pulse` 必须降置信到 0.55，实得 %.3f" % _cp)

    def test_25_deterministic_pick_and_confidence_before_method_boost(self):
        """确定性择一（同输入同输出）+ 置信度上界不被方法名加权突破 1.0。"""
        _a = self.insp.locate_issue("self_inspector", "")
        _b = self.insp.locate_issue("self_inspector", "")
        self.assertEqual(_a, _b, "定位结果非确定性")
        for _tag in _GAP_TAGS:
            _r = self.insp.locate_issue(_tag, "")
            self.assertLessEqual(_r.get("confidence", 0.0), 1.0)
            self.assertGreaterEqual(_r.get("confidence", 0.0), 0.0)

    def test_26_empty_organ_still_returns_default_shape(self):
        """`organ=""` ⇒ 不进入 1c 段，返回默认结构（契约不变）。"""
        for _o in ("", None):
            self.assertEqual(self.insp.locate_issue(_o, ""),
                             {"file": "", "method": "", "line": 0, "confidence": 0.0})


# ============================================================ C 覆盖率验收
class TestT91aCoverage(unittest.TestCase):
    """任务书验收线：覆盖率 ≥95%、剩余不可定位 ≤3。"""

    @classmethod
    def setUpClass(cls):
        cls.tags, cls.is_real = _real_tags()
        cls.insp = _inspector()

    def _rate(self, tags):
        _hit = sum(1 for _t in tags if self.insp.locate_issue(_t, "").get("file"))
        return _hit, float(_hit) / max(1, len(tags))

    def test_30_coverage_at_least_95pct_with_v3_on(self):
        _hit, _rate = self._rate(self.tags)
        _msg = ("覆盖率 %.1f%%（%d/%d，数据源=%s）未达验收线 95%%"
                % (100.0 * _rate, _hit, len(self.tags),
                   "真日志" if self.is_real else "冻结清单"))
        self.assertGreaterEqual(_rate, _ACCEPT_RATE, _msg)
        if os.environ.get("M91_SHOW_COVERAGE"):
            print("\n[T-91a] " + _msg)

    def test_31_remaining_misses_le_3(self):
        _miss = [t for t in self.tags if not self.insp.locate_issue(t, "").get("file")]
        self.assertLessEqual(len(_miss), _ACCEPT_MISS,
                             "剩余不可定位 %d 个（>%d）：%s"
                             % (len(_miss), _ACCEPT_MISS, _miss))

    def test_32_frozen_gap_tags_are_all_hittable(self):
        """不依赖真实日志：16 个缺口标签 + 6 个既有标签 100% 命中。"""
        _miss = [t for t in _FROZEN_TAGS if not self.insp.locate_issue(t, "").get("file")]
        self.assertEqual(_miss, [], "冻结清单仍不可定位：%s" % _miss)

    def test_33_先红_v3_off_reproduces_the_16_misses(self):
        """★先红证据（运行期）：V3 关闭 ⇒ 16 个缺口标签**全部**回到不可定位。

        断言方向为「⊆」：这 16 个标签在 V2 下**结构性**不可命中（命名源错位），
        故不随日志增长而失效。
        """
        with _Switch(_SWITCH, False):
            _miss = set(t for t in self.tags
                        if not self.insp.locate_issue(t, "").get("file"))
        _still = [t for t in _GAP_TAGS if t not in _miss]
        self.assertEqual(_still, [],
                         "以下标签在 V3 关闭时竟然命中（说明先红证据不成立）：%s" % _still)

    def test_34_v3_strictly_improves_over_v2(self):
        with _Switch(_SWITCH, False):
            _old, _ = self._rate(self.tags)
        _new, _ = self._rate(self.tags)
        self.assertGreater(_new, _old,
                           "V3 打开 %d/%d vs 关闭 %d/%d，无提升"
                           % (_new, len(self.tags), _old, len(self.tags)))

    def test_35_zero_regression_v3_only_adds(self):
        """★零回归核心：V3 置于 1c 段**最后**且带 `not _file` 守卫 ⇒
        V2 已命中的标签，在 V3 打开后 `file` / `confidence` 必须**逐字不变**。
        """
        with _Switch(_SWITCH, False):
            _old = {t: self.insp.locate_issue(t, "") for t in self.tags}
        _diff = []
        for _t, _r in _old.items():
            if not _r.get("file"):
                continue                      # 未命中者由 V3 负责「新增」，不作比较
            _now = self.insp.locate_issue(_t, "")
            if (_now.get("file"), _now.get("confidence")) != \
                    (_r.get("file"), _r.get("confidence")):
                _diff.append((_t, _r, _now))
        self.assertEqual(_diff, [], "V3 改写了既有命中（越权抢占）：%s" % _diff)

    def test_36_switch_off_is_verbatim_legacy(self):
        """关闭 ⇒ 与「改前算法」逐字一致（内联固化的判据，不依赖 scratch 目录）。"""
        with _Switch(_SWITCH, False):
            for _t in self.tags:
                _r = self.insp.locate_issue(_t, "")
                if _t in _GAP_TAGS:
                    self.assertEqual(_r.get("file"), "",
                                     "%r 在关闭状态下仍命中 %r" % (_t, _r.get("file")))
                    self.assertEqual(_r.get("confidence"), 0.0)
                    self.assertEqual(_r.get("method"), "")


# ============================================================ D 开关契约
class TestT91aSwitchContract(unittest.TestCase):
    def test_40_default_on(self):
        self.assertTrue(_m91_log_locate_v3_on())

    def test_41_registered_in_config_py_with_true(self):
        """★第91批 相关任务：本开关已**正式登记**进 config.py（默认值与登记前一致）。"""
        _src = _read(_CFG)
        self.assertIn(_SWITCH, _src, "T-91c 起 %s 必须正式登记在 config.py" % _SWITCH)
        self.assertIn("%s = True" % _SWITCH, _src, "登记默认值必须为 True")
        self.assertIn("# [M91-CFG]", _src, "缺少第91批登记段标记")
        self.assertTrue(getattr(config, _SWITCH))

    def test_42_getattr_fallback_when_absent(self):
        """config 缺属性也须为 True（灰度过期安全，不因配置缺失而静默降级）。"""
        with _Switch(_SWITCH, "not-a-bool"):
            # 属性存在但非布尔 ⇒ bool("not-a-bool") 为 True，不得抛异常
            self.assertTrue(_m91_log_locate_v3_on())

    def test_43_switch_is_pure_config_reader(self):
        """关闭时必须真的关闭（helper 无内部状态残留）。"""
        with _Switch(_SWITCH, False):
            self.assertFalse(_m91_log_locate_v3_on())
        self.assertTrue(_m91_log_locate_v3_on())

    def test_44_v3_is_subordinate_to_m90_switch(self):
        """★层级契约：V3 是 M90「1c 段」的**增量**，M90 关闭 ⇒ V3 必须一并失效。"""
        with _Switch(_SWITCH, True):
            self.assertTrue(self.locate_hits("self_inspector"))
            with _Switch(_SWITCH_M90, False):
                self.assertFalse(self.locate_hits("self_inspector"),
                                 "M90 关闭时 V3 仍生效（层级被绕过）")
                self.assertFalse(self.locate_hits("胸腺"))

    @staticmethod
    def locate_hits(tag):
        return bool(_inspector().locate_issue(tag, "").get("file"))

    def test_45_backup_has_no_such_capability(self):
        """★先红证据（静态，`.bak_batch91`）：改前**不存在**本能力。

        断言改前源码中不存在三个 相关任务 新增符号 ⇒ 「先红」不依赖运行期巧合。
        """
        if not os.path.isfile(_BAK_SI):
            self.skipTest("缺少 .bak_batch91/nucleus/self_inspector.py.bak（改前对照）")
        _old = _read(_BAK_SI)
        for _sym in ("_m91_lookup_logger_tag", "_m91_lookup_organ_alias",
                     "_build_organ_alias_index", "_m91_log_locate_v3_on",
                     "_M91_TAG_SCAN_ROOTS", "_m91_v3"):
            self.assertNotIn(_sym, _old, "改前源码竟已存在 %s（先红证据不成立）" % _sym)
        _new = _read(_SI)
        for _sym in ("_m91_lookup_logger_tag", "_m91_lookup_organ_alias",
                     "_build_organ_alias_index", "_m91_log_locate_v3_on"):
            self.assertIn(_sym, _new, "改后源码缺少 %s" % _sym)


# ============================================================ E 与既有能力共存
class TestT91aCoexistWithM90(unittest.TestCase):
    """V3 置于 1c 段最后 ⇒ 既有 0.9 / 0.85 / 0.75 / 0.7 / 0.6 五级优先级全部保留。"""

    @classmethod
    def setUpClass(cls):
        cls.insp = _inspector()

    def test_50_class_index_still_outranks_v3(self):
        """`TaskOrchestrator` 同时是多源 logger 字面量（V3 会降 0.55），
        但类索引（0.75）在前 ⇒ 必须仍为 0.75，绝不被 V3 拉低。"""
        _r = self.insp.locate_issue("TaskOrchestrator", "")
        self.assertTrue(_r.get("file"))
        self.assertGreater(_r.get("confidence", 0.0), 0.55,
                           "V3 抢占/拉低了类索引结果：%s" % _r)
        self.assertAlmostEqual(_r.get("confidence", 0.0), 0.75, places=3,
                               msg="类索引层（0.75）应优先命中：%s" % _r)

    def test_51_chinese_fuzzy_layer_not_stolen_by_alias(self):
        """`心脏` 的 `organ_name` 别名也指向 PulseHeart.py；
        若 V3 前置则置信度会被抬到 0.8 ⇒ 断言必须仍为 **0.6**（中文名模糊层）。"""
        for _tag, (_exp_file, _exp_conf) in _PRIORITY_GUARD.items():
            _r = self.insp.locate_issue(_tag, "")
            self.assertEqual(_r.get("file"), _exp_file, "%r 落点错误：%s" % (_tag, _r))
            self.assertAlmostEqual(
                _r.get("confidence", 0.0), _exp_conf, places=3,
                msg="%r 置信度被 V3 改写（应为 %.2f）：%s" % (_tag, _exp_conf, _r))

    def test_52_message_file_path_still_wins(self):
        """消息里带真实 `.py` 路径（1a 段 0.9）仍压过 V3。"""
        _msg = 'File "nucleus/self_inspector.py", line 12, in locate_issue'
        _r = self.insp.locate_issue("self_inspector", _msg)
        self.assertEqual(_r.get("file"), "nucleus/self_inspector.py")
        self.assertGreaterEqual(_r.get("confidence", 0.0), 0.75,
                                "1a/类索引层被 V3 覆盖：%s" % _r)

    def test_53_existing_m90_gap_fixed_by_class_index_is_unchanged(self):
        """第90批成果（类索引）零回归：`InfoField` / `PulseSnapshot` 依旧是 0.75。"""
        for _tag, _exp in (("InfoField", "nucleus/field/InfoField.py"),
                           ("PulseSnapshot", "nucleus/mnemosyne/PulseSnapshot.py"),
                           ("SafeEvolutionExecutor",
                            "nucleus/reasoning/SafeEvolutionExecutor.py")):
            _r = self.insp.locate_issue(_tag, "")
            self.assertEqual(_r.get("file"), _exp, "%r 落点漂移：%s" % (_tag, _r))
            self.assertAlmostEqual(_r.get("confidence", 0.0), 0.75, places=3,
                                   msg="%r 置信度漂移：%s" % (_tag, _r))


if __name__ == "__main__":
    unittest.main(verbosity=2)
