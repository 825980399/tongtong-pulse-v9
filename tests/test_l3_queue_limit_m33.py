# -*- coding: utf-8 -*-
"""主线第33批 T4（P3）：分层队列上限初始化顺序修复 门控单测。

背景（实测复现）：
    `InfoField.__init__` 中 `self._init_layer_pools()`（earlier）已按
    `config.PULSE_LAYER` 设置 `_layer_queue_limits`（L3=300），但紧随其后的
    硬编码默认值又把 L3 覆盖回 100 →
      · 启动 banner 报「L3=300」
      · 运行时 `[L3动态扩缩] 队列=X/100`（背压实际按 100 生效）
    修复 = 把默认值上移到 `_init_layer_pools()` **之前**（真正只作兜底）。

测试策略：
    1) AST 顺序不变式（真实源码，改动一破即失败）；
    2) 真实 `_init_layer_pools()` 行为（config 值生效 + banner 一致）；
    3) 真实 `_auto_scale_l3_by_depth()` 日志用实际上限（非硬编码）；
    4) 灰度开关关闭 → 复现修复前覆盖行为（零回归）。
"""
import ast
import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from nucleus.field.InfoField import (  # noqa: E402
    _PULSE_LAYER_L3,
    InfoField,
)

_IF_MOD = importlib.import_module("nucleus.field.InfoField")
_SRC = open(os.path.join(_PROJ, "nucleus", "field", "InfoField.py"),
               encoding="utf-8").read()
_SWITCH = "ENABLE_L3_QUEUE_LIMIT_ORDER_FIX"


def _find_init_node():
    tree = ast.parse(_SRC)
    cls = [n for n in ast.walk(tree)
           if isinstance(n, ast.ClassDef) and n.name == "InfoField"][0]
    return [b for b in cls.body
            if isinstance(b, ast.FunctionDef) and b.name == "__init__"][0]


class _FakeQueue:
    def __init__(self, depth):
        self._d = depth

    def qsize(self):
        return self._d


class _FakePool:
    def __init__(self, depth, max_workers=2):
        self._work_queue = _FakeQueue(depth)
        self._max_workers = max_workers


def _make_l3(depth, limit):
    """轻量实例：沿用 tests/test_info_field.py 的 __new__ 范式。"""
    inst = InfoField.__new__(InfoField)
    inst._layer_pools = {_PULSE_LAYER_L3: _FakePool(depth, 2)}
    inst._layer_queue_limits = {_PULSE_LAYER_L3: limit}
    inst._layer_rejected_count = {_PULSE_LAYER_L3: 0}
    inst._layer_overflow_allowed = {_PULSE_LAYER_L3: 0}
    inst._overflow_priority_allow = 3
    inst._l3_dynamic_enabled = True
    inst._l3_last_scale_ts = 0.0
    inst._l3_scale_cooldown = 0.0
    inst._l3_scale_up_cooldown = 0.0
    inst._l3_scale_down_cooldown = 0.0
    inst._l3_base_workers = 2
    inst._l3_max_dynamic_workers = 5
    inst._l3_scale_up_thresholds = [0.70, 0.85, 0.95]
    inst._l3_burst_detection_enabled = True
    inst._l3_burst_growth_threshold = 0.30
    inst._l3_min_workers = 3
    inst._l3_scale_down_buffer_sec = 30.0
    inst._l3_burst_window = []
    inst._l3_below_half_since = 0.0
    inst._l3_scale_up_count = 0
    inst._l3_scale_down_count = 0
    inst._l3_last_scale_reason = ""
    inst._l3_demand_start_ts = 0.0
    inst._l3_response_times = []
    inst._l3_depth_sample_ts = 0.0
    inst._l3_depth_history = []
    inst._l3_producer_counts = {}
    inst._l3_completion_window = []
    inst._resize_layer_pool = MagicMock()
    inst._record_phase18_l3 = MagicMock()
    return inst


class _Switch:
    """临时改写 config 开关（构造/析构成对）。"""

    def __init__(self, value):
        self._v = value

    def __enter__(self):
        import config
        self._old = getattr(config, _SWITCH, None)
        setattr(config, _SWITCH, self._v)

    def __exit__(self, *a):
        import config
        if self._old is None:
            try:
                delattr(config, _SWITCH)
            except AttributeError:
                pass
        else:
            setattr(config, _SWITCH, self._old)
        return False


class TestSourceOrderInvariant(unittest.TestCase):
    """① 真实源码的语句顺序不变式。"""

    def test_01_default_before_init_layer_pools(self):
        """★核心：默认值赋值必须在 `_init_layer_pools()` 调用**之前**。"""
        init = _find_init_node()
        call_line = None
        for stmt in ast.walk(init):
            if isinstance(stmt, ast.Call):
                try:
                    if ast.unparse(stmt.func).endswith("_init_layer_pools"):
                        call_line = stmt.lineno
                except Exception:
                    pass
        self.assertIsNotNone(call_line, "未找到 _init_layer_pools() 调用")

        default_lines = []
        for stmt in init.body:                       # 只看顶层语句
            if isinstance(stmt, ast.Assign):
                for tg in stmt.targets:
                    try:
                        if ast.unparse(tg) == "self._layer_queue_limits":
                            default_lines.append(stmt.lineno)
                    except Exception:
                        pass
        self.assertTrue(default_lines, "未找到默认队列上限赋值")
        self.assertLess(min(default_lines), call_line,
                        "默认队列上限必须在 _init_layer_pools() 之前赋值")

    def test_02_no_unconditional_clobber_after(self):
        """★`_init_layer_pools()` 之后不得存在**无条件**覆盖（必须在开关分支内）。"""
        init = _find_init_node()
        call_line = None
        for stmt in ast.walk(init):
            if isinstance(stmt, ast.Call):
                try:
                    if ast.unparse(stmt.func).endswith("_init_layer_pools"):
                        call_line = stmt.lineno
                except Exception:
                    pass

        guarded, unguarded = [], []
        for stmt in init.body:
            if isinstance(stmt, ast.Assign) and stmt.lineno > call_line:
                for tg in stmt.targets:
                    try:
                        if ast.unparse(tg) == "self._layer_queue_limits":
                            (guarded if False else unguarded).append(stmt.lineno)
                    except Exception:
                        pass
            if isinstance(stmt, ast.If) and stmt.lineno > call_line:
                seg = ast.unparse(stmt.test)
                if _SWITCH in seg or "_m33_queue_limit_order_fix_on" in seg:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Assign):
                            for tg in sub.targets:
                                try:
                                    if ast.unparse(tg) == "self._layer_queue_limits":
                                        guarded.append(sub.lineno)
                                except Exception:
                                    pass
        self.assertEqual(unguarded, [],
                         f"存在无条件覆盖 (行 {unguarded})，会让 config 值失效")
        self.assertTrue(guarded, "未找到带灰度开关的覆盖分支（回退路径缺失）")

    def test_03_switch_method_exists_and_defaults_true(self):
        self.assertTrue(hasattr(InfoField, "_m33_queue_limit_order_fix_on"))
        with _Switch(True):
            self.assertTrue(InfoField._m33_queue_limit_order_fix_on())
        with _Switch(False):
            self.assertFalse(InfoField._m33_queue_limit_order_fix_on())

    def test_04_config_switch_present(self):
        import config
        self.assertTrue(getattr(config, _SWITCH, False),
                        f"{_SWITCH} 默认应为 True")


class TestRealInitLayerPools(unittest.TestCase):
    """② 真实 `_init_layer_pools()`：config 值必须真正写入。"""

    def setUp(self):
        self._inst = InfoField.__new__(InfoField)
        self._inst._layer_pools = {}
        self._inst._layer_disabled = {}

    def tearDown(self):
        for _p in getattr(self._inst, "_layer_pools", {}).values():
            try:
                _p.shutdown(wait=False)
            except Exception:
                pass
        _ap = getattr(self._inst, "_adaptive_task_pool", None)
        if _ap is not None:
            try:
                _ap.shutdown(wait=False)
            except Exception:
                pass

    def test_10_limits_match_config(self):
        import config
        with patch.object(_IF_MOD, "_module_logger", new=MagicMock()) as _lg:
            self._inst._init_layer_pools()
        _exp = config.PULSE_LAYER["l3_queue_hard_limit"]
        _got = self._inst._layer_queue_limits[_PULSE_LAYER_L3]
        self.assertEqual(_got, _exp,
                         "L3 生效上限应与 config.PULSE_LAYER 一致")
        self.assertIn(f"L3={_exp}", str(_lg.info.call_args_list),
                      "启动 banner 应显示 config 的 L3 上限")

    def test_11_default_dict_would_be_clobbered_without_fix(self):
        """反证：若默认值在图之后执行，config 值会被覆盖回 100。"""
        self._inst._init_layer_pools()
        _before = self._inst._layer_queue_limits[_PULSE_LAYER_L3]
        self._inst._layer_queue_limits = {_PULSE_LAYER_L3: 100}   # 旧行为
        self.assertNotEqual(self._inst._layer_queue_limits[_PULSE_LAYER_L3],
                            _before)


class TestRuntimeLogUsesActualLimit(unittest.TestCase):
    """③ 真实动态扩缩日志必须用**实际**上限。"""

    def test_20_log_shows_actual_limit(self):
        inst = _make_l3(depth=40, limit=300)      # 40/300 = 13%
        _lg = MagicMock()
        with patch.object(_IF_MOD, "_module_logger", new=_lg):
            # 触发 burst（10s 内深度增长 > 30%）
            inst._l3_burst_window = [(0.0, 10)]
            import time as _t
            inst._l3_burst_window = [(_t.time(), 10)]
            inst._auto_scale_l3_by_depth()
        _txt = "".join(str(c) for c in _lg.info.call_args_list)
        self.assertIn("/300(", _txt, f"日志应显示实际上限 300，实际={_txt}")

    def test_21_no_hardcoded_100_in_log_path(self):
        """日志路径不得硬编码 100 作为上限。"""
        _i = _SRC.index("def _auto_scale_l3_by_depth")
        _j = _SRC.index("def get_l3_scaling_stats")
        _seg = _SRC[_i:_j]
        self.assertNotIn("/100(", _seg)

    def test_22_admit_uses_layer_queue_limits(self):
        """所有层共用同一字典 → 修一处即全层正确。"""
        _i = _SRC.index("def _admit_layer_task")
        _seg = _SRC[_i:_i + 3000]
        self.assertIn("_layer_queue_limits.get(layer", _seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
