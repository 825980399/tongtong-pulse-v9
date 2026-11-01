# -*- coding: utf-8 -*-
"""418 刀3 单测：七项软伤各自的判据。

①锁等待假高  ②线程上限  ③_m70_blanked 合并  ④融合占位符来自路径名
⑤tp1~tp4 出生产  ⑥PermissionError 重试  ⑦额度预警去重
"""
import ast
import json
import logging
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


# ============================================================ ① 锁等待：时间窗 + 含 0
def test_10_lock_window_drops_stale_peak():
    """10 分钟前的尖峰不许再算进均值（旧实现报 126.0ms）。"""
    from nucleus.runtime_metrics import _m418_lock_window_avg
    _now = 1_000_000.0
    _w = [200.0, 250.0, 180.0, 0.0, 0.0]
    _t = [_now - 600, _now - 500, _now - 400, _now - 10, _now - 5]
    _avg, _n = _m418_lock_window_avg(_w, _t, _now)
    assert _avg == 0.0 and _n == 2, (_avg, _n)
    assert round(sum(_w) / len(_w), 3) == 126.0, "旧公式的读数（对照）"


def test_11_lock_window_includes_zeros():
    """0 值必须在分母里：60ms / 9 个样本 = 6.667，不是 60.0。"""
    from nucleus.runtime_metrics import _m418_lock_window_avg
    _now = 1_000_000.0
    _avg, _n = _m418_lock_window_avg([0.0] * 8 + [60.0], [_now - 5] * 8 + [_now - 1], _now)
    assert _n == 9
    assert abs(_avg - 6.667) < 0.01, _avg


def test_12_lock_window_empty_is_zero_with_zero_samples():
    from nucleus.runtime_metrics import _m418_lock_window_avg
    assert _m418_lock_window_avg([], [], 1.0) == (0.0, 0)
    assert _m418_lock_window_avg(None, None, 1.0) == (0.0, 0)


def test_13_runtime_metrics_wired():
    from nucleus.runtime_metrics import RuntimeMetrics, _M418_LOCK_WAIT_WINDOW_SEC
    _m = RuntimeMetrics()
    assert hasattr(_m, "_lock_wait_ts")
    assert _m._lock_waits.maxlen == _m._lock_wait_ts.maxlen
    assert _M418_LOCK_WAIT_WINDOW_SEC > 0
    _snap = _m.get_snapshot()
    assert "lock_wait_avg_ms" in _snap and "lock_wait_samples" in _snap


def test_14_point_carries_denominator():
    """告警要能自己说明「几个样本」——没有分母的均值在监控上是不可判读的。"""
    _s = _src("nucleus/runtime_metrics.py")
    assert '"lock_wait_samples": _lock_wait_n' in _s
    assert '"lock_wait_window_sec": _M418_LOCK_WAIT_WINDOW_SEC' in _s


# ============================================================ ② 线程上限
def test_20_budget_total_below_60():
    from utils.thread_budget import budget_total, caps
    assert budget_total() < 60, (caps(), budget_total())


def test_21_cap_only_squeezes():
    from utils.thread_budget import cap
    assert cap("hybrid_io", 32) == 8
    assert cap("l1", 8) == 6
    assert cap("l3", 4) == 4
    assert cap("l3", 2) == 2, "预算比原值大时不许调高"


def test_22_cap_unknown_key_passthrough():
    from utils.thread_budget import cap
    assert cap("no_such_pool", 17) == 17


def test_23_cap_switch_off(monkeypatch):
    import config
    from utils.thread_budget import cap
    monkeypatch.setattr(config, "ENABLE_M418_THREAD_SLIMMING", False, raising=False)
    assert cap("hybrid_io", 32) == 32


def test_24_both_call_sites_wired():
    _h = _src("nucleus/HybridParallelScheduler.py")
    _i = _src("nucleus/field/InfoField.py")
    assert 'cap("hybrid_io"' in _h
    for _k in ("l0", "l1", "l2", "l3", "adaptive"):
        assert 'cap("%s"' % _k in _i, _k


def test_25_config_switch_exists():
    import config
    assert getattr(config, "ENABLE_M418_THREAD_SLIMMING", None) is True
    assert isinstance(getattr(config, "M418_THREAD_CAPS", None), dict)


# ============================================================ ③ _m70_blanked 合并
def test_30_blanked_logs_once_for_many_nodes(caplog):
    """100 次命中只许 1 条 WARNING（改前 5 条/批）。"""
    from nucleus.mnemosyne import PulseNode as PN
    PN._m80_blanked.update({"n": 0, "logged": 0})
    with caplog.at_level(logging.WARNING, logger="nucleus.mnemosyne.PulseNode"):
        for _ in range(100):
            PN._m80_blanked_warn()
    _rows = [r for r in caplog.records if "_m70_blanked" in r.getMessage()]
    assert len(_rows) == 1, "重复告警没合并：%d 条" % len(_rows)
    assert PN.m418_blanked_stats()["n"] == 100, "计数不能丢"


def test_31_blanked_stats_shape():
    from nucleus.mnemosyne import PulseNode as PN
    _s = PN.m418_blanked_stats()
    assert set(_s) == {"n", "logged"}


def test_32_blanked_to_dict_still_restores():
    """合并告警不许影响「还原原值写盘」这条主逻辑。

    ★418 自审：首版用 `PulseNode.__new__` + 手铺 16 个字段，`to_dict()` 还要读
    `frequency_signature` 等 ⇒ 用例红在「我少铺了一个字段」，不是在验被测逻辑。
    改用**真构造**（`PulseNode(...)`），字段由类自己负责 —— 以后加字段也不会假红。
    """
    from nucleus.mnemosyne.PulseNode import PulseNode
    _n = PulseNode("被清空的值", keywords=["k1"], space_path="/x")
    _n.linked_nodes = ["a"]
    _n._m70_blanked = True
    _n._m70_keep = {"value": "原值", "linked_nodes": ["a"]}
    _d = _n.to_dict()
    assert _d["value"] == "原值", "还原逻辑被改坏了"
    assert _d["linked_nodes"] == ["a"], "linked_nodes 还原被改坏了"


# ============================================================ ④ 融合占位符
def _liver():
    from organs.body.PulseLiver import PulseLiver
    return PulseLiver.__new__(PulseLiver)


def test_40_path_name_strips_placeholder_segment():
    """`/自我理解/器官别名` ⇒ 展示名必须**不含**占位符（否则模板自己造占位符）。"""
    _o = _liver()
    _o._m377_is_placeholder = lambda t: "[器官别名]" in str(t)
    _pn = _o._m418_safe_path_name("/自我理解/器官别名")
    assert _pn == "自我理解", _pn
    assert not _o._m377_is_placeholder("[%s]" % _pn)


def test_41_path_name_all_placeholder_falls_back():
    _o = _liver()
    _o._m377_is_placeholder = lambda t: "[器官别名]" in str(t)
    assert _o._m418_safe_path_name("/器官别名") == "核心智慧"
    assert _o._m418_safe_path_name("") == "核心智慧"
    assert _o._m418_safe_path_name("/") == "核心智慧"


def test_42_path_name_normal_case_unchanged():
    """正常路径名必须原样保留（这是绝大多数情况，别把好路径改坏）。"""
    _o = _liver()
    _o._m377_is_placeholder = lambda t: "[器官别名]" in str(t)
    assert _o._m418_safe_path_name("/技术/代码质量") == "代码质量"
    assert _o._m418_safe_path_name("/自我理解") == "自我理解"


def test_43_seg_placeholder_checks_bracketed_form():
    """裸 `器官别名` 单独测**不命中**，加方括号才命中 —— 这正是本 bug 隐蔽的原因。"""
    _o = _liver()
    _o._m377_is_placeholder = lambda t: "[器官别名]" in str(t)
    assert _o._m418_seg_is_placeholder("器官别名") is True
    assert _o._m418_seg_is_placeholder("[器官别名]") is True
    assert _o._m418_seg_is_placeholder("代码质量") is False
    assert _o._m418_seg_is_placeholder("") is True


def test_44_generation_site_uses_safe_name():
    _s = _src("organs/body/PulseLiver.py")
    assert "_m418_safe_path_name(prefix)" in _s
    assert 'core_keywords = [prefix.split("/")[-1] if prefix else "综合"]' not in _s, \
        "关键词兜底还在直接取路径名（会把占位符带进 keywords）"


def test_45_fusion_switch_exists():
    import config
    assert getattr(config, "ENABLE_M418_FUSION_SAFE_PATHNAME", None) is True


# ============================================================ ⑤ tp1~tp4 出生产
def test_50_production_has_no_test_stubs():
    _p = os.path.join(ROOT, "data", "evolution", "effect_verify.json")
    _d = json.load(open(_p, encoding="utf-8"))
    _bad_v = [k for k in (_d.get("verdicts") or {}) if str(k).startswith("tp")]
    _bad_i = [k for k in (_d.get("issues") or {}) if str(k).startswith("a.py::")]
    assert not _bad_v, "生产 verdicts 里还有测试桩：%s" % _bad_v
    assert not _bad_i, "生产 issues 里还有测试桩：%s" % _bad_i


def test_51_fixture_keeps_them_for_tests():
    _p = os.path.join(ROOT, "tests", "fixtures", "effect_verify_test_stubs.json")
    _d = json.load(open(_p, encoding="utf-8"))
    assert set(_d["verdicts"]) >= {"tp1", "tp2", "tp3", "tp4"}
    assert "不得回灌生产" in _d["_note"]


def test_52_code_gate_present():
    """代码级闸门：生产路径上 load/save 双向剔桩，非生产路径原样保留。"""
    from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier as V
    assert V._m418_is_stub_patch("tp1") is True
    assert V._m418_is_stub_patch("tp99") is True
    assert V._m418_is_stub_patch("patch_1788887251_0c6f") is False
    assert V._m418_is_stub_issue("a.py::log::f") is True
    assert V._m418_is_stub_issue(r"D:\x\PulseCortex.py::silent_exception::__init__") is False


def test_53_strip_only_on_production_path(tmp_path):
    """沙箱路径**不许**剔桩（单测里要能造桩验证，剔了就没法测）。"""
    from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier as V
    _v = V.__new__(V)
    _v._save_path = str(tmp_path / "effect_verify.json")
    _v._verdicts = {"tp1": {"patch_id": "tp1"}, "p_real": {"patch_id": "p_real"}}
    _v._issues = {"a.py::log::f": {}, "real.py::x::y": {}}
    assert _v._m418_strip_stubs() == (0, 0)
    assert len(_v._verdicts) == 2 and len(_v._issues) == 2


# ============================================================ ⑥ PermissionError 重试
def test_60_replace_with_retry_success(tmp_path):
    from utils.atomic_io import replace_with_retry
    _a = tmp_path / "a.tmp"
    _b = tmp_path / "a.json"
    _a.write_text("x", encoding="utf-8")
    assert replace_with_retry(str(_a), str(_b)) is True
    assert _b.read_text(encoding="utf-8") == "x"


def test_61_replace_with_retry_retries_transient(monkeypatch, tmp_path):
    """前两次抛 PermissionError，第三次成功 —— 必须拿到 True。

    ★418 自审：首版用 `/tmp/nope` 这种不存在的路径 + `_real = os.replace`（在打桩
    **之前**就早绑定到真函数）⇒ 第三次调用拿到 FileNotFoundError，用例必然红，且红
    的理由跟「重试」毫无关系。真跑一遍落盘才算验证（用 tmp_path 真文件）。
    """
    import utils.atomic_io as A
    _a = tmp_path / "flaky.tmp"
    _b = tmp_path / "flaky.json"
    _a.write_text("payload", encoding="utf-8")
    _calls = {"n": 0}
    _real = A.os.replace          # 打桩前先取**当前**引用（monkeypatch 会改 A.os.replace）

    def _flaky(s, d):
        _calls["n"] += 1
        if _calls["n"] <= 2:
            raise PermissionError(13, "拒绝访问")
        return _real(s, d)

    monkeypatch.setattr(A.os, "replace", _flaky)
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)
    assert A.replace_with_retry(str(_a), str(_b), attempts=4) is True
    assert _calls["n"] == 3, "应当恰好第 3 次成功，实际试了 %d 次" % _calls["n"]
    assert _b.read_text(encoding="utf-8") == "payload"


def test_62_replace_exhausted_returns_false(monkeypatch):
    import utils.atomic_io as A

    def _always(a, b):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(A.os, "replace", _always)
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)
    assert A.replace_with_retry("a", "b", attempts=3) is False


def test_63_non_retryable_fails_fast(monkeypatch):
    import utils.atomic_io as A
    _calls = {"n": 0}

    def _bad(a, b):
        _calls["n"] += 1
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(A.os, "replace", _bad)
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)
    assert A.replace_with_retry("a", "b", attempts=5) is False
    assert _calls["n"] == 1, "不可重试的错不该空转 5 次"


def test_64_call_recorder_uses_retry():
    _s = _src("nucleus/llm/call_recorder.py")
    assert "_m418_replace(_tmp, _path)" in _s
    assert "os.replace(_tmp, _path)" not in _s, "还有裸 os.replace 没走重试"


def test_65_streamminer_uses_retry():
    _s = _src("nucleus/genesis/StreamMiner.py")
    assert "replace_with_retry" in _s


# ============================================================ ⑦ 额度预警去重
def _qm():
    from nucleus.llm.ChannelQuotaMonitor import ChannelQuotaMonitor
    _m = ChannelQuotaMonitor.__new__(ChannelQuotaMonitor)
    import threading
    _m._lock = threading.RLock()
    _m._warned = {}
    _m._events = {}
    return _m


def test_70_warn_once_returns_true_then_false():
    """去重键命中时必须返回 **False** —— 调用方靠它决定要不要补明细。"""
    _m = _qm()
    assert _m._warn_once("ch", "alert", 0.169) is True
    assert _m._warn_once("ch", "alert", 0.169) is False


def test_71_alert_branch_does_not_reset_dedup():
    """预警档不许每次调用都擦掉去重键（改前 `pop` 无条件执行）。"""
    _s = _src("nucleus/llm/ChannelQuotaMonitor.py")
    assert "if _r >= _alert:\n                        self._warned.pop(_n, None)" in _s, \
        "`_warned.pop` 必须收进 `_r >= _alert` 分支"


def test_72_second_line_gated_by_warn_once():
    _s = _src("nucleus/llm/ChannelQuotaMonitor.py")
    assert "if self._warn_once(_n, \"alert\", _r):" in _s, \
        "第二条明细必须由 `_warn_once` 的返回值把关"


def test_73_apply_to_channels_does_not_duplicate(tmp_path, caplog):
    """同一档位连续调用 5 次 apply_to_channels ⇒ 只许 1 条预警日志。"""
    _m = _qm()
    _m._forced = set()
    _m.usage_path = str(tmp_path / "u.json")
    _m._used = {}
    _m._data = {}
    _m._usage = {}
    _m._state = {}
    # 直接打桩内部读数，只测「告警是否去重」
    _m.pause_ratio = lambda: 0.05
    _m.degrade_ratio = lambda: 0.1
    _m.alert_ratio = lambda: 0.2
    _m._force_enabled = lambda: set()
    _m.enabled = lambda: True
    _m.get_remaining_ratio = lambda n: 0.169
    _m._ensure_daily_reset = lambda *a, **k: None
    _m.save = lambda *a, **k: True
    with caplog.at_level(logging.WARNING, logger="nucleus.llm.ChannelQuotaMonitor"):
        for _ in range(5):
            _m.apply_to_channels([{"name": "bailian-qwen-plus", "priority": 2}])
    _rows = [r for r in caplog.records if "剩余额度" in r.getMessage()
             or "剩余 16.9%" in r.getMessage()]
    assert len(_rows) == 1, "同一预警打了两遍/多遍：%d 条\n%s" % (
        len(_rows), [r.getMessage() for r in _rows])


# ============================================================ ⑧ 行尾 / 结构
@pytest.mark.parametrize("rel,crlf", [
    ("nucleus/runtime_metrics.py", True),
    ("nucleus/field/InfoField.py", True),
    ("nucleus/mnemosyne/PulseNode.py", True),
    ("organs/body/PulseLiver.py", True),
    ("nucleus/llm/ChannelQuotaMonitor.py", True),
    ("nucleus/llm/call_recorder.py", False),
    ("nucleus/genesis/StreamMiner.py", True),
    ("nucleus/HybridParallelScheduler.py", True),
    ("config.py", False),
    ("utils/atomic_io.py", False),
    ("utils/thread_budget.py", False),
])
def test_80_eol_preserved(rel, crlf):
    _b = open(os.path.join(ROOT, rel), "rb").read()
    if crlf:
        assert _b.count(b"\r\n") == _b.count(b"\n") == _b.count(b"\r"), rel
    else:
        assert _b.count(b"\r\n") == 0, rel


def test_81_no_module_level_import_after_code():
    """新模块不许在函数外 mid-file import（`python utils/xx.py` 直跑会崩）。"""
    for _rel in ("utils/atomic_io.py", "utils/thread_budget.py"):
        _t = ast.parse(_src(_rel))
        _seen_def = False
        for _n in _t.body:
            if isinstance(_n, (ast.FunctionDef, ast.ClassDef)):
                _seen_def = True
                continue
            if _seen_def and isinstance(_n, (ast.Import, ast.ImportFrom)):
                raise AssertionError("%s 第 %d 行在定义之后还有模块级 import"
                                     % (_rel, _n.lineno))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
