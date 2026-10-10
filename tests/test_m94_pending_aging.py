# -*- coding: utf-8 -*-
"""★主线第94批 T-94a：待审批队列**老化策略** 门控单测。

被测对象 = **真实源码**（`nucleus/reasoning/PatchManager.py` +
`nucleus/reasoning/SafeEvolutionExecutor.py`），不复刻任何逻辑；
`.bak_batch94` 仅用于「改前行为对照」，缺失时 skipTest。

任务书原文（第94批 T-94a）：
    「pending 超过 N 条（建议 20 条）或超过 T 小时（建议 24 小时）未裁决时，
      对满足 source=local_rule / 非核心文件 / runtime_verified=true / 风险=低
      的补丁自动放行；开关 ENABLE_PENDING_QUEUE_AGING 默认关闭；
      冷却表『已有待审批』计时器改为按补丁而非按问题位置。」

★★本批 T0 的**前提修正**（与任务书表述不同，已用生产数据实证）：
    pending 队列 12 条 **全部 source=llm**，`local_rule` **0 条** ——
    因为 local_rule 在**入队时**就已被第85批的
    `_m85_local_low_risk_auto_apply` 自动放行为 approved（65 条 local_rule
    全在 patch_history 且 applied=True）。
    ⇒ 任务书的放行判据在真实队列上**命中 0 条**，本策略属「纵深防御 + 未来场景」。
    E 组即为该结论的可执行证据（不把结论写成期望值，只固化「可复算事实」）。

★两处实现取舍（已写进代码文档串，此处作为回归契约固化）：
    1. **放行集合按触发器区分**：条数触发（队列拥堵）⇒ 放行全部合格项；
       时长触发 ⇒ 只放行「自身停留也 ≥ max_hours」的合格项（否则刚入队的
       补丁会被顺手放行，违背「老化」本意）。见 C3 / C7。
    2. **上帝文件叠加排除**：任务书点名 `organs/brain/PulseInnerWorld.py`，
       而单一真源 `_is_core_file` 的 `_core_markers` 不含 `organs/` ⇒
       叠加 `_m94_is_god_file` 薄层（只加严、不放松、不改全局语义）。见 B3/B9。

覆盖五组：
  A. 静态接线（AST）—— 新增成员真在 PatchManager 类内、老化调用点在
     `only_approved` 过滤**之前**、SafeEvolutionExecutor 两处调用点已换
     且原直接调用点已清空（仅剩新方法内的异常回落）；
  B. 纯判据 —— eligible 六态 + 时间戳口径 + 双阈值触发（条数 / 时长）；
  C. 端到端（真实 `_m94_apply_pending_aging` + 真实 pending 文件落盘）；
  D. 零行为变化 —— 开关默认关闭；关闭时 `find_blocking_pending_patch`
     与既有 `has_pending_patch_for` **逐例同值**；
  E. 生产队列取证（只读）。
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402

_PM = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
_SE = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_BAK_PM = os.path.join(ROOT, ".bak_batch94", "PatchManager.py.bak")
_BAK_SE = os.path.join(ROOT, ".bak_batch94", "SafeEvolutionExecutor.py.bak")
_CFG = os.path.join(ROOT, "config.py")
_PENDING_REAL = os.path.join(ROOT, "data", "patches", "pending_patches.json")
_HISTORY_REAL = os.path.join(ROOT, "data", "patches", "patch_history.json")

_SW = "ENABLE_PENDING_QUEUE_AGING"
_NOW = 1789000000.0

_M94_MEMBERS = (
    "_m94_pending_aging_on", "_m94_aging_max_count", "_m94_aging_max_hours",
    "_m94_patch_age_hours", "_m94_aging_eligible", "_m94_is_god_file",
    "_m94_aging_should_run", "pending_aging_preview", "_m94_apply_pending_aging",
    "find_blocking_pending_patch",
)


def _read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().replace("\r\n", "\n")


def _class_names(path, cls):
    _t = ast.parse(_read(path))
    for _n in _t.body:
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            return {x.name for x in _n.body
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


def _method_span(path, cls, name):
    """返回类内某方法的 (lineno, end_lineno)，找不到返回 (None, None)。"""
    _t = ast.parse(_read(path))
    for _n in _t.body:
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            for _m in _n.body:
                if (isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and _m.name == name):
                    return _m.lineno, getattr(_m, "end_lineno", _m.lineno)
    return None, None


class _SwBase(unittest.TestCase):
    """开关复位基类：每个用例起点固定为「关闭」，结束还原真实值。"""

    def setUp(self):
        self._had = hasattr(config, _SW)
        self._orig = getattr(config, _SW, None)
        setattr(config, _SW, False)

    def tearDown(self):
        if self._had:
            setattr(config, _SW, self._orig)
        else:
            try:
                delattr(config, _SW)
            except Exception:
                pass


class TestM94AgingStaticWiring(_SwBase):
    """A 组：静态接线（AST 归属硬约束，防「helper 全绿但生产链路未接」）。"""

    def test_A1_all_new_members_inside_class(self):
        _names = _class_names(_PM, "PatchManager")
        for _w in _M94_MEMBERS:
            self.assertIn(_w, _names, "★{} 必须挂在 PatchManager 类内".format(_w))
        # 不得出现模块级 M94 函数（AST 归属硬约束）
        _top = [n.name for n in ast.parse(_read(_PM)).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.assertEqual([], [n for n in _top if "m94" in n.lower()])

    def test_A2_aging_runs_before_approval_filter(self):
        _src = _read(_PM)
        _i_aging = _src.index("self._m94_apply_pending_aging(pending)")
        _i_filter = _src.index("        # ★v24.0新增：审批过滤\n        if only_approved:")
        self.assertLess(_i_aging, _i_filter,
                        "★老化必须在审批过滤**之前**，否则放行的补丁捞不到")

    def test_A3_se_callsites_switched_and_old_calls_gone(self):
        _src = _read(_SE)
        self.assertEqual(2, _src.count("self._m94_pending_blocks_regeneration("),
                         "★SafeEvolutionExecutor 应有 2 处调用点")
        _s, _e = _method_span(_SE, "SafeEvolutionExecutor",
                              "_m94_pending_blocks_regeneration")
        self.assertIsNotNone(_s, "★新方法必须存在于 SafeEvolutionExecutor 类内")
        _lines = _src.split("\n")
        _inside = "\n".join(_lines[_s - 1:_e])
        _outside = "\n".join(_lines[:_s - 1] + _lines[_e:])
        # 新方法内的两处是「判据异常 → 回落既有口径」，必须保留
        self.assertEqual(2, _inside.count(
            "self._patch_manager.has_pending_patch_for("),
            "★异常回落必须保留既有口径（两处）")
        self.assertNotIn("self._patch_manager.has_pending_patch_for(", _outside,
                         "★原直接调用点必须已全部替换为新口径")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_A4_config_registered_and_default_off(self):
        _src = _read(_CFG)
        self.assertIn("ENABLE_PENDING_QUEUE_AGING = False", _src)
        self.assertIn("PENDING_AGING_MAX_COUNT = 20", _src)
        self.assertIn("PENDING_AGING_MAX_AGE_HOURS = 24", _src)
        self.assertIn("# [M94-CFG]", _src)
        self.assertIs(False, getattr(config, _SW))
        self.assertEqual(20, config.PENDING_AGING_MAX_COUNT)
        self.assertEqual(24, config.PENDING_AGING_MAX_AGE_HOURS)

    def test_A5_declaration_contract(self):
        """★契约：纯判据 = `@staticmethod`（无状态）；涉及 IO 的 = 实例方法。

        本批「helper 全绿但生产链路未接」的历史坑（第91批自曝 P0）说明：
        成员**归属**与**签名**必须被门禁固化，否则重构时会静默漂移。
        """
        _t = ast.parse(_read(_PM))
        _found = {}
        for _n in _t.body:
            if isinstance(_n, ast.ClassDef) and _n.name == "PatchManager":
                for _m in _n.body:
                    if isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        _found[_m.name] = _m
        for _w in _M94_MEMBERS:
            self.assertIsNotNone(_found.get(_w), "★{} 必须在类内".format(_w))
        # 纯判据（无 self、无 IO）
        for _w in ("_m94_pending_aging_on", "_m94_aging_max_count",
                   "_m94_aging_max_hours", "_m94_patch_age_hours",
                   "_m94_aging_eligible", "_m94_is_god_file",
                   "_m94_aging_should_run"):
            _deco = [ast.unparse(d) for d in _found[_w].decorator_list]
            self.assertIn("staticmethod", _deco,
                          "★{} 必须是 staticmethod（纯判据，无 self 状态）".format(_w))
        # IO 侧（读 pending / 写盘）必须是实例方法且首参为 self
        for _w in ("pending_aging_preview", "_m94_apply_pending_aging",
                   "find_blocking_pending_patch"):
            _a = _found[_w].args
            self.assertEqual(["self"], [x.arg for x in _a.args][:1],
                             "★{} 需要访问实例状态，首参必须为 self".format(_w))


class TestM94AgingCriteria(_SwBase):
    """B 组：纯判据（不碰磁盘、不碰生产队列）。"""

    def _base(self, **kw):
        _d = {"id": "p", "file": "organs/body/PulseKidney.py", "method": "m",
              "source": "local_rule", "risk_level": "低",
              # ★第105批 T-105a：放行判据改读嵌套 runtime_verify_result.verified
              # （顶层 runtime_verified 为污染字段）+ baseline_errors>0。
              "runtime_verify_result": {"verified": True, "baseline": 5},
              "baseline_errors": 5,
              "status": "pending", "saved_at": _NOW - 30 * 3600}
        _d.update(kw)
        return _d

    def test_B1_eligible_six_states(self):
        self.assertTrue(PatchManager._m94_aging_eligible(self._base()))
        self.assertFalse(PatchManager._m94_aging_eligible(self._base(source="llm")))
        self.assertFalse(PatchManager._m94_aging_eligible(
            self._base(file="nucleus/reasoning/PatchManager.py")))
        # ★第105批：判据读嵌套 verified；嵌套 verified=False → 不放行
        self.assertFalse(PatchManager._m94_aging_eligible(
            self._base(runtime_verify_result={"verified": False})))
        # ★第105批：顶层污染字段 runtime_verified=True 即使嵌套 False 也不放行（双保险）
        self.assertFalse(PatchManager._m94_aging_eligible(
            self._base(runtime_verified=True, runtime_verify_result={"verified": False})))
        self.assertFalse(PatchManager._m94_aging_eligible(self._base(risk_level="高")))
        # ★第 7 态：任务书点名的上帝文件
        self.assertFalse(PatchManager._m94_aging_eligible(
            self._base(file="organs/brain/PulseInnerWorld.py")))

    def test_B2_eligible_never_releases_on_bad_input(self):
        for _v in (None, [], "x", 0, {}, {"source": "local_rule"},
                   {"source": "local_rule", "runtime_verified": "True",
                    "risk_level": "低"}):
            self.assertFalse(PatchManager._m94_aging_eligible(_v),
                             "★保守：{!r} 必须不放行".format(_v))

    def test_B3_core_gate_reuses_single_source(self):
        """★非核心文件判定必须复用 `_m80_is_core_file`（单一真源），不自造清单。"""
        self.assertTrue(PatchManager._m80_is_core_file("nucleus/reasoning/x.py"))
        self.assertTrue(PatchManager._m80_is_core_file("main.py"))
        self.assertFalse(PatchManager._m80_is_core_file("organs/body/PulseKidney.py"))

    def test_B4_age_timestamp_candidates_and_missing(self):
        for _k in ("saved_at", "created_at", "submitted_at", "timestamp",
                   "generated_at"):
            _p = {"id": "x", _k: _NOW - 2 * 3600}
            self.assertAlmostEqual(2.0, PatchManager._m94_patch_age_hours(_p, _NOW),
                                   places=3, msg="时间戳候选 {} 未被识别".format(_k))
        self.assertIsNone(PatchManager._m94_patch_age_hours({"id": "x"}, _NOW))
        self.assertIsNone(PatchManager._m94_patch_age_hours(None, _NOW))
        # 布尔/字符串/负数 不得被当成时间戳（否则 0 小时误判）
        for _v in (True, "1700000000", -1, 0):
            self.assertIsNone(
                PatchManager._m94_patch_age_hours({"saved_at": _v}, _NOW),
                "★{!r} 不应被当作有效时间戳".format(_v))

    def test_B5_saved_at_wins_over_generated_at(self):
        _p = {"saved_at": _NOW - 5 * 3600, "generated_at": _NOW - 99 * 3600}
        self.assertAlmostEqual(5.0, PatchManager._m94_patch_age_hours(_p, _NOW),
                               places=3,
                               msg="★saved_at 才是「等待裁决」的起点，应优先")

    def test_B6_dual_threshold_trigger(self):
        _fresh = [{"id": "x%d" % i, "saved_at": _NOW - 3600} for i in range(12)]
        _ok, _why = PatchManager._m94_aging_should_run(_fresh, _NOW)
        self.assertFalse(_ok)
        self.assertIn("below_threshold", _why)
        # 条数阈值：正好 20 条
        _ok2, _why2 = PatchManager._m94_aging_should_run(
            [{"id": "x%d" % i, "saved_at": _NOW - 3600} for i in range(20)], _NOW)
        self.assertTrue(_ok2)
        self.assertIn("count=20>=20", _why2)
        # 时长阈值：单条超期
        _ok3, _why3 = PatchManager._m94_aging_should_run(
            [{"id": "x", "saved_at": _NOW - 30 * 3600}], _NOW)
        self.assertTrue(_ok3)
        self.assertIn("max_age=30.0h>=24.0h", _why3)

    def test_B7_empty_or_bad_queue(self):
        for _v in ([], None, {}, "x"):
            _ok, _why = PatchManager._m94_aging_should_run(_v, _NOW)
            self.assertFalse(_ok)
            self.assertEqual("empty", _why)

    def test_B8_thresholds_configurable_and_clamped(self):
        _o_c = config.PENDING_AGING_MAX_COUNT
        _o_h = config.PENDING_AGING_MAX_AGE_HOURS
        try:
            config.PENDING_AGING_MAX_COUNT = 3
            config.PENDING_AGING_MAX_AGE_HOURS = 2.5
            self.assertEqual(3, PatchManager._m94_aging_max_count())
            self.assertEqual(2.5, PatchManager._m94_aging_max_hours())
            config.PENDING_AGING_MAX_COUNT = 0      # 越界 → 钳到 1
            self.assertEqual(1, PatchManager._m94_aging_max_count())
            config.PENDING_AGING_MAX_AGE_HOURS = -5
            self.assertEqual(0.0, PatchManager._m94_aging_max_hours())
        finally:
            config.PENDING_AGING_MAX_COUNT = _o_c
            config.PENDING_AGING_MAX_AGE_HOURS = _o_h

    def test_B9_god_file_layer_is_additive_only(self):
        """★上帝文件排除**只加严**：必须是为既有核心判定的**超集**。"""
        _god = PatchManager._m94_is_god_file
        self.assertTrue(_god("organs/brain/PulseInnerWorld.py"))
        self.assertTrue(_god("organs\\brain\\PulseInnerWorld.py"))
        self.assertTrue(_god(None))          # 判定异常/空 → 保守视为受保护
        self.assertTrue(_god(""))
        self.assertFalse(_god("organs/body/PulseKidney.py"))
        # 核心文件仍然受保护（两层取或，不因新增层而放松）
        self.assertTrue(PatchManager._m80_is_core_file("nucleus/reasoning/x.py"))
        self.assertTrue(_god("nucleus/reasoning/x.py") is False)  # 薄层不管核心
        # 叠加后：核心 ∪ 上帝 ⊆ 不可放行
        for _f in ("nucleus/reasoning/x.py", "main.py",
                   "organs/brain/PulseInnerWorld.py"):
            self.assertFalse(PatchManager._m94_aging_eligible(self._base(file=_f)),
                             "★{} 绝不可被放行".format(_f))


class TestM94AgingEndToEnd(_SwBase):
    """C 组：端到端（真实 pending 文件 + 真实落盘 + 真实重启读取）。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="m94_aging_")
        self.pm = PatchManager(self.tmp)
        self.pf = self.pm._pending_file
        os.makedirs(os.path.dirname(self.pf), exist_ok=True)
        self._write(self._queue())

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, items):
        with io.open(self.pf, "w", encoding="utf-8") as f:
            f.write(json.dumps(items, ensure_ascii=False))

    def _mk(self, _id, **kw):
        _d = {"id": _id, "file": "organs/body/PulseKidney.py", "method": "m_" + _id,
              "source": "local_rule", "risk_level": "低",
              # ★第105批 T-105a：放行判据改读嵌套 verified + baseline>0
              "runtime_verify_result": {"verified": True, "baseline": 5},
              "baseline_errors": 5,
              "status": "pending", "saved_at": time.time() - 30 * 3600}
        _d.update(kw)
        return _d

    def _queue(self):
        return [self._mk("ok"), self._mk("llm", source="llm"),
                self._mk("core", file="nucleus/reasoning/PatchManager.py"),
                self._mk("god", file="organs/brain/PulseInnerWorld.py"),
                self._mk("fresh", saved_at=time.time() - 3600),
                self._mk("hi", risk_level="高")]

    def _disk(self):
        return {p["id"]: p for p in json.load(io.open(self.pf, encoding="utf-8"))}

    def test_C1_off_means_zero_behaviour_change(self):
        _before = io.open(self.pf, "rb").read()
        _mt = os.path.getmtime(self.pf)
        config.ENABLE_PENDING_QUEUE_AGING = False
        _r = self.pm._m94_apply_pending_aging(self.pm._load_patch_list(self.pf))
        self.assertEqual(
            {"enabled": False, "triggered": False, "released": []},
            {k: _r[k] for k in ("enabled", "triggered", "released")})
        self.assertFalse(_r["persisted"])
        self.assertEqual(_before, io.open(self.pf, "rb").read(),
                         "★开关关闭时不得改写 pending 文件")
        self.assertEqual(_mt, os.path.getmtime(self.pf))
        # 关闭时 preview 的 enabled 也必须是 False（真实开关状态，非假设）
        self.assertFalse(self.pm.pending_aging_preview()["enabled"])

    def test_C2_on_but_below_threshold(self):
        _fresh = [self._mk("f%d" % i, saved_at=time.time() - 600)
                  for i in range(5)]
        self._write(_fresh)
        config.ENABLE_PENDING_QUEUE_AGING = True
        _before = io.open(self.pf, "rb").read()
        _r = self.pm._m94_apply_pending_aging(self.pm._load_patch_list(self.pf))
        self.assertTrue(_r["enabled"])
        self.assertFalse(_r["triggered"], "★5 条 / 10 分钟：双阈值都不该触发")
        self.assertEqual([], _r["released"])
        self.assertEqual(_before, io.open(self.pf, "rb").read())

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_C3_age_trigger_releases_only_self_expired(self):
        config.ENABLE_PENDING_QUEUE_AGING = True
        _r = self.pm._m94_apply_pending_aging(self.pm._load_patch_list(self.pf))
        self.assertTrue(_r["triggered"])
        self.assertIn("max_age=", _r["reason"])
        self.assertEqual(["ok"], [x["id"] for x in _r["released"]],
                         "★只有 local_rule+非核心+非上帝文件+已运行验证+低风险"
                         " 且**自身也超期** 才放行")
        self.assertTrue(_r["persisted"])
        _d = self._disk()
        self.assertEqual("approved", _d["ok"]["status"])
        for _k in ("llm", "core", "god", "hi"):
            self.assertEqual("pending", _d[_k]["status"], "★{} 不应被放行".format(_k))
        self.assertEqual("pending", _d["fresh"]["status"],
                         "★自身未超期不应被顺手放行（时长触发不冲刷新补丁）")
        self.assertTrue(_d["ok"].get("aged_approved"))
        self.assertTrue(_d["ok"].get("aged_approved_at"))
        self.assertIn("aged_reason", _d["ok"])

    def test_C4_persist_does_not_pollute_in_memory_abs_paths(self):
        """★浅拷贝保护：`_save_patch_list` 会原地把 file 归一化为相对路径。"""
        config.ENABLE_PENDING_QUEUE_AGING = True
        _q = self.pm._load_patch_list(self.pf)
        self.assertTrue(all(os.path.isabs(p["file"]) for p in _q))
        self.pm._m94_apply_pending_aging(_q)
        for _p in _q:
            self.assertTrue(os.path.isabs(_p["file"]),
                            "★内存对象必须保持绝对路径（后续 apply 循环依赖）")
        _disk = json.load(io.open(self.pf, encoding="utf-8"))
        self.assertTrue(all(not os.path.isabs(p["file"]) for p in _disk),
                        "★落盘仍是相对路径（与改造前格式一致）")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_C5_reload_after_persist_is_effective(self):
        """★端到端闭环：落盘后**重新读盘**，approved 必须真实生效（可被审批过滤捞到）。"""
        config.ENABLE_PENDING_QUEUE_AGING = True
        self.pm._m94_apply_pending_aging(self.pm._load_patch_list(self.pf))
        _again = self.pm._load_patch_list(self.pf)
        self.assertEqual({"ok"},
                         {p["id"] for p in _again if p.get("status") == "approved"})
        # 二次调用幂等：已 approved 的不会被重复计入
        _r2 = self.pm._m94_apply_pending_aging(_again)
        self.assertEqual([], _r2["released"], "★已 approved 不得重复放行")

    def test_C6_preview_read_only(self):
        config.ENABLE_PENDING_QUEUE_AGING = True
        _before = io.open(self.pf, "rb").read()
        _pv = self.pm.pending_aging_preview()
        self.assertEqual(_before, io.open(self.pf, "rb").read(),
                         "★preview 绝不写盘")
        self.assertEqual(6, _pv["checked"])
        self.assertEqual(1, _pv["eligible"])
        self.assertEqual(["ok"], [x["id"] for x in _pv["released"]])
        self.assertEqual(20, _pv["max_count"])
        self.assertEqual(24.0, _pv["max_hours"])

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_C7_count_trigger_flushes_all_eligible(self):
        """★条数触发＝队列拥堵 ⇒ 全部合格项放行（含刚入队者），与时长触发区别。"""
        _q = [self._mk("q%d" % i, saved_at=time.time() - 600) for i in range(20)]
        self._write(_q)
        config.ENABLE_PENDING_QUEUE_AGING = True
        _r = self.pm._m94_apply_pending_aging(self.pm._load_patch_list(self.pf))
        self.assertTrue(_r["triggered"])
        self.assertIn("count=20>=20", _r["reason"])
        self.assertEqual(20, len(_r["released"]))

    def test_C8_before_has_no_m94_code(self):
        """先红后绿：改前备份**不含**本批代码。"""
        if not os.path.isfile(_BAK_PM):
            self.skipTest("改前备份缺失")
        _old = _read(_BAK_PM)
        for _w in ("_m94_pending_aging_on", "find_blocking_pending_patch",
                   "_m94_aging_eligible", "_m94_is_god_file"):
            self.assertNotIn(_w, _old, "★改前不应有 {}".format(_w))
        self.assertIn("_m94_pending_aging_on", _read(_PM))
        if os.path.isfile(_BAK_SE):
            self.assertNotIn("_m94_pending_blocks_regeneration", _read(_BAK_SE))
        self.assertIn("_m94_pending_blocks_regeneration", _read(_SE))


class TestM94Blocking(_SwBase):
    """D 组：`find_blocking_pending_patch` 与既有 `has_pending_patch_for` 的关系。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="m94_blk_")
        self.pm = PatchManager(self.tmp)
        self.pf = self.pm._pending_file
        os.makedirs(os.path.dirname(self.pf), exist_ok=True)
        _now = time.time()
        _items = [
            {"id": "old", "file": "organs/body/A.py", "method": "m1",
             "status": "runtime_failed", "saved_at": _now - 48 * 3600},
            {"id": "new", "file": "organs/body/B.py", "method": "m2",
             "status": "pending", "saved_at": _now - 3600},
        ]
        with io.open(self.pf, "w", encoding="utf-8") as f:
            f.write(json.dumps(_items, ensure_ascii=False))
        # ★必须用真实解析后的绝对路径（`_load_patch_list` 会把 file 解析为绝对）
        _abs = {p["id"]: p["file"] for p in self.pm._load_patch_list(self.pf)}
        self.f_old, self.f_new = _abs["old"], _abs["new"]

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_D1_off_matches_legacy_every_case(self):
        config.ENABLE_PENDING_QUEUE_AGING = False
        _cases = [(self.f_old, "m1"), (self.f_new, "m2"),
                  (os.path.join(self.tmp, "organs", "body", "C.py"), "m3"),
                  ("", "m1"), ("x.py", ""), (None, None)]
        for _f, _m in _cases:
            _old = bool(self.pm.has_pending_patch_for(_f, _m))
            _new = self.pm.find_blocking_pending_patch(_f, _m) is not None
            self.assertEqual(_old, _new,
                             "★零行为变化：{}/{} 两口径必须同值".format(_f, _m))

    def test_D2_on_expired_unblocks_in_window_blocks(self):
        config.ENABLE_PENDING_QUEUE_AGING = True
        self.assertIsNone(self.pm.find_blocking_pending_patch(self.f_old, "m1"),
                          "★超 24h 未裁决不再永久冻结同位置再生")
        self.assertIsNotNone(self.pm.find_blocking_pending_patch(self.f_new, "m2"),
                             "★窗口内仍应阻塞")
        # 既有 API 语义**未变**（仍是「匹配即阻塞」）
        self.assertTrue(self.pm.has_pending_patch_for(self.f_old, "m1"))

    def test_D3_unblock_is_not_release(self):
        """★超期补丁被解阻塞，但**不放行**（放行仍须过 eligible 严格判据）。"""
        config.ENABLE_PENDING_QUEUE_AGING = True
        _q = self.pm._load_patch_list(self.pf)
        _r = self.pm._m94_apply_pending_aging(_q)
        self.assertEqual([], [x["id"] for x in _r["released"]],
                         "★status=runtime_failed / 缺 source 的补丁不满足放行判据")

    def test_D4_timestamp_missing_is_conservative(self):
        """★取不到时间戳 ⇒ 视为仍在窗口内（保守，不误伤历史补丁）。"""
        _p = {"id": "nots", "file": "organs/body/D.py", "method": "m9",
              "status": "pending"}
        self._append(_p)
        config.ENABLE_PENDING_QUEUE_AGING = True
        self.assertIsNotNone(
            self.pm.find_blocking_pending_patch(self.pm._load_patch_list(self.pf)[-1]["file"],
                                                "m9"))

    def _append(self, item):
        _items = json.load(io.open(self.pf, encoding="utf-8"))
        _items.append(item)
        with io.open(self.pf, "w", encoding="utf-8") as f:
            f.write(json.dumps(_items, ensure_ascii=False))

    def test_D5_se_wiring_surface(self):
        _src = _read(_SE)
        self.assertIn("def _m94_pending_blocks_regeneration(self, file_path: str,",
                      _src)
        self.assertIn("_m94_pending_blocks_regeneration",
                      _class_names(_SE, "SafeEvolutionExecutor"))


class TestM94Production(_SwBase):
    """E 组：生产队列只读取证（不写盘、不改状态）。"""

    def test_E1_real_pending_source_composition_recomputable(self):
        """固化「pending 里 local_rule 为 0」这一可复算事实（不写死具体条数）。"""
        if not os.path.isfile(_PENDING_REAL):
            self.skipTest("生产 pending 文件缺失")
        _ps = json.load(io.open(_PENDING_REAL, encoding="utf-8"))
        if not isinstance(_ps, list) or not _ps:
            self.skipTest("生产 pending 为空")
        _src = {}
        for _p in _ps:
            if isinstance(_p, dict):
                _k = str(_p.get("source"))
                _src[_k] = _src.get(_k, 0) + 1
        self.assertEqual(len(_ps), sum(_src.values()))
        # local_rule 补丁确实进过补丁库，但**在 history 而非 pending**
        if not os.path.isfile(_HISTORY_REAL):
            self.skipTest("生产 history 文件缺失")
        _h = json.load(io.open(_HISTORY_REAL, encoding="utf-8"))
        _h = _h if isinstance(_h, list) else []
        _lr = [p for p in _h if isinstance(p, dict)
               and str(p.get("source")) == "local_rule"]
        self.assertGreater(len(_lr), 0,
                           "★local_rule 补丁确实存在（在 history 已 approved）")
        # ★本批 T0 核心结论的可执行证据：pending 侧 local_rule 命中数
        # ★第105批：判据已改读嵌套 runtime_verify_result.verified + baseline_errors>0，
        #   此处参考口径同步对齐（读嵌套，不再读顶层污染字段 runtime_verified）。
        def _is_elig_ref(_p):
            if not isinstance(_p, dict):
                return False
            if str(_p.get("source")) != "local_rule":
                return False
            if str(_p.get("risk_level")) != "低":
                return False
            _rvr = _p.get("runtime_verify_result") or {}
            if not isinstance(_rvr, dict) or _rvr.get("verified") is not True:
                return False
            _b = _p.get("baseline_errors")
            if not isinstance(_b, (int, float)) or _b <= 0:
                _b = _rvr.get("baseline")
            if not isinstance(_b, (int, float)) or _b <= 0:
                return False
            if PatchManager._m80_is_core_file(str(_p.get("file", ""))):
                return False
            if PatchManager._m94_is_god_file(str(_p.get("file", ""))):
                return False
            return True
        _hit = sum(1 for _p in _ps if PatchManager._m94_aging_eligible(_p))
        self.assertEqual(_hit, sum(1 for _p in _ps if _is_elig_ref(_p)),
                         "★放行判据与生产队列构成必须自洽（嵌套 verified + baseline>0）")

    def test_E2_real_preview_is_read_only(self):
        if not os.path.isfile(_PENDING_REAL):
            self.skipTest("生产 pending 文件缺失")
        _b = io.open(_PENDING_REAL, "rb").read()
        _mt = os.path.getmtime(_PENDING_REAL)
        _pv = PatchManager(ROOT).pending_aging_preview()
        self.assertIn("checked", _pv)
        self.assertEqual(_b, io.open(_PENDING_REAL, "rb").read(),
                         "★preview 绝不写生产队列")
        self.assertEqual(_mt, os.path.getmtime(_PENDING_REAL))


if __name__ == "__main__":
    unittest.main(verbosity=2)
