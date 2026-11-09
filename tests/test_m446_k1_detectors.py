# -*- coding: utf-8 -*-
"""446 刀1：三类 issue_type 的静态检测器（AST 口径 + 注册表）。

判据来自任务书 §刀1；与 `nucleus/self_inspector.py` 同名诊断器同源，
但**只在标识符/AST 上判**，注释与字符串不参与（诊断侧是正则扫原文）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution.patch_active_reprobe import (
    active_reprobe,
    detect_cross_module_singleton_call,
    detect_non_atomic_write,
    detect_periodic_task_no_reentry,
    detector_for,
    detector_hits,
)

# ---------------- periodic_task_no_reentry ----------------

_TIMER_NO_GUARD = '''def _schedule_next_beat(self):
    self._timer = threading.Timer(5.0, self._tick)
    self._timer.start()
'''

_TIMER_WITH_GUARD = '''def _schedule_next_beat(self):
    if self._beat_busy:
        return
    self._timer = threading.Timer(5.0, self._tick)
'''

_SCHEDULE_CALL = '''def on_heartbeat(self):
    self._schedule_next_beat()
'''

_DOCSTRING_ONLY = '''def _m365_watchdog_loop(self):
    """配合 `_schedule_next_beat()` 的无条件 cancel，见 396 批。"""
    self._spin()
'''

_INIT_WITH_TIMER = '''def __init__(self):
    self._timer = threading.Timer(5.0, self._tick)
'''


def test_periodic_timer_without_guard_hits():
    _h = detect_periodic_task_no_reentry(_TIMER_NO_GUARD)
    assert len(_h) == 1, _h
    assert "Timer" in _h[0]["why"], _h


def test_periodic_guard_exempts():
    assert detect_periodic_task_no_reentry(_TIMER_WITH_GUARD) == []


def test_periodic_schedule_call_hits():
    """调用 `self._schedule_x()` 也算周期信号（与诊断侧 `_schedule_` 子串同义）。"""
    _h = detect_periodic_task_no_reentry(_SCHEDULE_CALL)
    assert len(_h) == 1 and "_schedule_next_beat" in _h[0]["why"], _h


def test_periodic_docstring_is_not_a_signal():
    """★注释/文档串里的 `_schedule_next_beat()` 不是周期信号（诊断侧会误报，复现侧不）。"""
    assert detect_periodic_task_no_reentry(_DOCSTRING_ONLY) == []


def test_periodic_constructor_skipped():
    """`__init__` 不是周期任务（同源 `_M356_NO_REENTRY_SKIP`）。"""
    assert detect_periodic_task_no_reentry(_INIT_WITH_TIMER) == []


# ---------------- non_atomic_write ----------------

_OPEN_W = '''def _dump(self):
    with open(self._path, 'w', encoding='utf-8') as f:
        f.write('x')
'''

_OPEN_W_NESTED = '''def _dump(self):
    with open(os.path.join(self._dir, 'a.json'), 'w') as f:
        f.write('x')
'''

_OPEN_W_ATOMIC = '''def _dump(self):
    with open(self._path + '.tmp', 'w') as f:
        f.write('x')
    os.replace(self._path + '.tmp', self._path)
'''

_OPEN_R = '''def _load(self):
    with open(self._path, 'r', encoding='utf-8') as f:
        return f.read()
'''

_OPEN_W_IN_COMMENT = '''def _call_names(self):
    """`open(x, 'w')` 与 `open(x, 'r')` 用第三项区分。"""
    return 1
'''


def test_non_atomic_write_hits():
    _h = detect_non_atomic_write(_OPEN_W)
    assert len(_h) == 1 and "mode='w'" in _h[0]["why"], _h


def test_non_atomic_write_nested_parens_hits():
    """★真实 `open(join(a, b), 'w')`：诊断侧 `[^)]*` 正则漏报，AST 侧命中。"""
    assert len(detect_non_atomic_write(_OPEN_W_NESTED)) == 1


def test_non_atomic_write_atomic_exempt():
    assert detect_non_atomic_write(_OPEN_W_ATOMIC) == []


def test_non_atomic_write_read_mode_ignored():
    assert detect_non_atomic_write(_OPEN_R) == []


def test_non_atomic_write_comment_ignored():
    """★注释里的 `open(x, 'w')` 不算（诊断侧正则不认注释 ⇒ 误报）。"""
    assert detect_non_atomic_write(_OPEN_W_IN_COMMENT) == []


# ---------------- cross_module_singleton_call ----------------

_CALL_NO_IMPORT = '''def _get_node(self, node_id):
    return get_kal().get_node(node_id)
'''

_CALL_ORGAN_ORIGIN = '''def _get_node(self, node_id):
    from organs.body.PulseKidney import get_node_store
    return get_node_store().get_node(node_id)
'''

_CALL_INFRA_ORIGIN = '''def _get_node(self, node_id):
    from nucleus.knowledge_access_layer import get_kal
    return get_kal().get_node(node_id)
'''

_CALL_SELF = '''def _get_node(self):
    return self.get_node_store().get_node(1)
'''

_CALL_CONTRACT = '''def _post(self):
    return get_event_bus().post('x')
'''


def test_cross_module_hits_without_import():
    _h = detect_cross_module_singleton_call(_CALL_NO_IMPORT)
    assert len(_h) == 1 and "get_kal" in _h[0]["why"], _h


def test_cross_module_organ_origin_hits():
    assert len(detect_cross_module_singleton_call(_CALL_ORGAN_ORIGIN)) == 1


def test_cross_module_infra_origin_exempt():
    """片段内能解析出 `get_kal` 来自 `nucleus.*` ⇒ 基础设施工厂，豁免（374刀5）。"""
    assert detect_cross_module_singleton_call(_CALL_INFRA_ORIGIN) == []


def test_cross_module_self_call_exempt():
    assert detect_cross_module_singleton_call(_CALL_SELF) == []


def test_cross_module_contract_exempt():
    """契约三件套（`get_event_bus` 等）直连是正门。"""
    assert detect_cross_module_singleton_call(_CALL_CONTRACT) == []


# ---------------- 注册表 & 组合 ----------------

def test_registry_registers_three():
    for _t in ("periodic_task_no_reentry", "non_atomic_write",
               "cross_module_singleton_call"):
        assert detector_for(_t) is not None, _t


def test_registry_code_optimization_still_none():
    """既有语义不许被本批改掉：`code_optimization` 仍视为无判据。"""
    assert detector_for("code_optimization") is None


def test_registry_unknown_type_none():
    # ★485刀6 契约变更：sql_injection / Traceback / bare_return_none_in_except
    #   已按任务书要求补齐检测器（485 前确实为 None，由
    #   tests/test_m485_detectors.py 守护新契约）。此处保留 None 型断言：
    #   非字符串 / 未登记类型仍必须返回 None。
    assert detector_for(None) is None
    assert detector_for("no_such_issue_type_485x") is None


def test_active_reprobe_true_pass_for_periodic():
    _r = active_reprobe({
        "issue_type": "periodic_task_no_reentry",
        "original_code": _TIMER_NO_GUARD,
        "modified_code": _TIMER_WITH_GUARD,
    })
    assert _r["reprobe_verdict"] == "true_pass", _r
    assert _r["reprobe_baseline_hits"] == 1 and _r["reprobe_after_hits"] == 0, _r


def test_active_reprobe_false_pass_when_not_reproducible():
    """复现不出 ≠ 修好了（第51批纪律）。"""
    _r = active_reprobe({
        "issue_type": "non_atomic_write",
        "original_code": _OPEN_R,
        "modified_code": _OPEN_R,
    })
    assert _r["reprobe_verdict"] == "false_pass", _r


def test_detector_hits_returns_rules():
    _h = detector_hits("cross_module_singleton_call", _CALL_NO_IMPORT)
    assert _h and "get_kal" in _h[0]["why"], _h
    assert detector_hits("sql_injection", _CALL_NO_IMPORT) == []
    assert detector_hits("non_atomic_write", "") == []
