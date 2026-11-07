# -*- coding: utf-8 -*-
"""474 批第三期：自主性观测节拍接线的门控单测。

判据纪律（本仓铁律）：
  · 基准必须来自**另一个入口**（铁律 30）：这里用手工构造的 entries 与手算比率，
    不拿被测函数的输出反推期望值。
  · 每个判据都要**有判别力**（铁律 35）：test_07 是反证用例 —— 换掉单例后
    「去重」必须失效，否则说明断言在旧实现上也成立、等于自欺。
  · 每个 except 必须带 silent_exc（铁律 4）：本文件尽量不写 except，
    必需的几处一律显式调用。
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import autonomy as _pkg  # noqa: F401
from nucleus._silent_except import silent_exc
from nucleus.autonomy import pulse
from nucleus.autonomy.kernel import CapabilityKernel
from nucleus.autonomy.reasoning import ReasoningObserver

_MOD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "nucleus", "autonomy")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rows(n: int) -> list:
    """构造与 `verification_learning_hub.record()` 同构的记录（独立基准数据）。"""
    _out = []
    for _i in range(n):
        _out.append({
            "id": f"vl_test_{_i}",
            "organ": "inner_world",
            "task_type": "symbolic" if _i % 2 == 0 else "rule",
            "local_result": {"solved": _i % 3 != 2},
            "api_better": _i % 3 == 2,
            "needs_verification": False,
        })
    return _out


class _HubPublic:
    """提供公开快照口的 hub 替身。"""

    def __init__(self, rows):
        self._entries = list(rows)
        self._lock = threading.Lock()
        self.reads = 0

    def entries_snapshot(self):
        self.reads += 1
        with self._lock:
            return list(self._entries)


class _HubPrivate:
    """只有既有内部字段的 hub 替身（模拟当前生产 hub，无公开快照口）。"""

    def __init__(self, rows):
        self._entries = list(rows)
        self._lock = threading.Lock()


class _HubBroken:
    """读快照时抛异常 —— 验证异常绝不外溢。"""

    def entries_snapshot(self):
        raise RuntimeError("hub 模拟故障")


class TestAutonomyPulse(unittest.TestCase):
    """接线节拍：开关 / 节流 / 去重 / 只读 / 异常 / 铁律。"""

    def setUp(self):
        self._old_state = dict(pulse._STATE)
        self._old_observer = pulse._OBSERVER
        pulse._STATE.clear()
        pulse._STATE.update({"last_ts": 0.0, "runs": 0})
        pulse._OBSERVER = None
        self._tmp = os.path.join(_ROOT, "tmp", "pt_m474p3_ledger")
        os.makedirs(self._tmp, exist_ok=True)
        self._ledger = os.path.join(self._tmp, "experience.jsonl")
        if os.path.exists(self._ledger):
            os.remove(self._ledger)

    def tearDown(self):
        pulse._STATE.clear()
        pulse._STATE.update(self._old_state)
        pulse._OBSERVER = self._old_observer
        for _n in os.listdir(self._tmp):
            try:
                os.remove(os.path.join(self._tmp, _n))
            except OSError as _e:
                silent_exc(_e, where="test_m474_autonomy_pulse::tearDown", level="debug")
        try:
            os.rmdir(self._tmp)
        except OSError as _e:
            silent_exc(_e, where="test_m474_autonomy_pulse::tearDown rmdir", level="debug")

    def _tick(self, **kw):
        kw.setdefault("now", 1000.0)
        kw.setdefault("force", True)
        return pulse.autonomy_observe_tick(**kw)

    # ── 灰度开关（含 getattr 0 陷阱）──
    def test_01_default_is_on_when_unset(self):
        with mock.patch.object(config, "ENABLE_M474_AUTONOMY_PULSE", None, create=True):
            _r = self._tick(hub=_HubPublic(_rows(2)))
        self.assertEqual("ok", _r["status"])

    def test_02_explicit_false_disables_and_never_reads_hub(self):
        _hub = _HubPublic(_rows(2))
        with mock.patch.object(config, "ENABLE_M474_AUTONOMY_PULSE", False, create=True):
            _r = self._tick(hub=_hub)
        self.assertEqual("disabled", _r["status"])
        self.assertEqual(0, _hub.reads, "关闸后不得读 hub")

    def test_03_zero_is_explicit_off_not_default(self):
        """★铁律：``getattr(cfg, X, True) or True`` 会把 0 吞成 True。"""
        with mock.patch.object(config, "ENABLE_M474_AUTONOMY_PULSE", 0, create=True):
            _r = self._tick(hub=_HubPublic(_rows(1)))
        self.assertEqual("disabled", _r["status"])

    # ── 节流 ──
    def test_04_second_call_within_interval_is_skipped(self):
        _hub = _HubPublic(_rows(2))
        _a = pulse.autonomy_observe_tick(now=1000.0, hub=_hub)
        _b = pulse.autonomy_observe_tick(now=1100.0, hub=_hub)
        self.assertEqual("ok", _a["status"])
        self.assertEqual("skipped_throttle", _b["status"])
        self.assertEqual(1, _hub.reads, "节流的这一拍不该读 hub")

    def test_05_interval_config_is_honoured(self):
        _hub = _HubPublic(_rows(1))
        with mock.patch.object(config, "M474_AUTONOMY_PULSE_INTERVAL_SEC", 30, create=True):
            pulse.autonomy_observe_tick(now=1000.0, hub=_hub)
            _b = pulse.autonomy_observe_tick(now=1020.0, hub=_hub)
            _c = pulse.autonomy_observe_tick(now=1031.0, hub=_hub)
        self.assertEqual("skipped_throttle", _b["status"])
        self.assertEqual("ok", _c["status"])

    def test_06_force_bypasses_throttle(self):
        _hub = _HubPublic(_rows(1))
        pulse.autonomy_observe_tick(now=1000.0, hub=_hub)
        _r = pulse.autonomy_observe_tick(now=1001.0, hub=_hub, force=True)
        self.assertEqual("ok", _r["status"])

    # ── 去重与反证（判别力）──
    def test_07_singleton_dedup_and_counterproof(self):
        """同批记录第二次 new=0；**换掉单例后必须重新记 N 条**（反证有判别力）。"""
        _hub = _HubPublic(_rows(3))
        _a = self._tick(hub=_hub)
        _b = self._tick(hub=_hub, now=2000.0)
        self.assertEqual(3, _a["new"])
        self.assertEqual(0, _b["new"], "单例去重应拦住同批记录")
        pulse._OBSERVER = ReasoningObserver(CapabilityKernel(ledger_enabled=False))
        _c = self._tick(hub=_hub, now=3000.0)
        self.assertEqual(3, _c["new"], "换 observer 后仍为 0 ⇒ 判据无判别力")

    # ── 只读快照 ──
    def test_08_public_snapshot_path(self):
        _hub = _HubPublic(_rows(2))
        self.assertEqual(2, len(pulse.read_hub_entries(_hub)))
        self.assertEqual(1, _hub.reads)

    def test_09_private_locked_path(self):
        _hub = _HubPrivate(_rows(2))
        self.assertEqual(2, len(pulse.read_hub_entries(_hub)))

    def test_10_snapshot_never_mutates_hub(self):
        _hub = _HubPrivate(_rows(2))
        _before = json.dumps(_hub._entries, ensure_ascii=False, sort_keys=True)
        _got = pulse.read_hub_entries(_hub)
        _got[0]["local_result"]["solved"] = "TAMPERED"
        _got.append({"id": "injected"})
        self.assertEqual(_before,
                         json.dumps(_hub._entries, ensure_ascii=False, sort_keys=True),
                         "快照必须是深拷贝，不能改到 hub")

    def test_11_max_batch_takes_tail(self):
        _hub = _HubPrivate(_rows(300))
        _got = pulse.read_hub_entries(_hub, max_batch=200)
        self.assertEqual(200, len(_got))
        self.assertEqual("vl_test_100", _got[0]["id"])
        self.assertEqual("vl_test_299", _got[-1]["id"])

    def test_12_non_dict_rows_are_dropped(self):
        _hub = _HubPrivate(_rows(2) + [None, "bad", 42])
        self.assertEqual(2, len(pulse.read_hub_entries(_hub)))

    # ── 异常边界 ──
    def test_13_hub_failure_returns_error_without_raising(self):
        _r = self._tick(hub=_HubBroken())
        self.assertEqual("error", _r["status"])
        self.assertEqual("RuntimeError", _r["error_type"])

    def test_14_log_failure_does_not_change_result(self):
        """★铁律 40：日志绝不决定返回值。"""
        _hub = _HubPublic(_rows(2))
        with mock.patch.object(pulse._LOGGER, "info",
                               side_effect=RuntimeError("日志通道故障")):
            _r = self._tick(hub=_hub)
        self.assertEqual("ok", _r["status"])
        self.assertEqual(2, _r["new"])

    # ── 指标：基准来自手工构造 + 手算，不用被测函数输出 ──
    def test_19_metrics_match_hand_computed_baseline(self):
        _entries = [
            {"id": "a", "local_result": {"solved": True}, "api_better": False,
             "needs_verification": False, "task_type": "symbolic"},
            {"id": "b", "local_result": {"solved": True}, "api_better": False,
             "needs_verification": False, "task_type": "rule"},
            {"id": "c", "local_result": {"solved": False}, "api_better": True,
             "needs_verification": False, "task_type": "symbolic"},
        ]
        _r = self._tick(hub=_HubPrivate(_entries))
        self.assertEqual(3, _r["new"])
        self.assertAlmostEqual(0.6667, _r["reasoning_self_sufficiency_rate"], places=4)
        self.assertAlmostEqual(0.3333, _r["external_dependency_rate"], places=4)
        self.assertAlmostEqual(0.3333, _r["domain_external_rate"], places=4)

    # ── 台账：显式路径要真写，默认生产路径在 pytest 下不写 ──
    def test_20_ledger_written_with_explicit_path(self):
        pulse._OBSERVER = ReasoningObserver(
            CapabilityKernel(ledger_path=self._ledger, ledger_enabled=True))
        _r = self._tick(hub=_HubPrivate(_rows(2)))
        self.assertEqual("ok", _r["status"])
        with open(self._ledger, encoding="utf-8") as _fh:
            _recs = [json.loads(_x) for _x in _fh if _x.strip()]
        self.assertEqual(2, len(_recs))
        self.assertTrue(all(_x["kind"] == "reasoning_observation" for _x in _recs))
        self.assertTrue(all(_x["domain"] == "reasoning" for _x in _recs))

    def test_21_default_path_is_not_written_under_pytest(self):
        """★铁律：pytest 会经生产代码路径灌假经验，默认路径必须不写。"""
        _prod = os.path.join(_ROOT, "data", "autonomy", "experience.jsonl")
        _before = os.path.getsize(_prod) if os.path.exists(_prod) else -1
        self._tick(hub=_HubPrivate(_rows(2)))
        _after = os.path.getsize(_prod) if os.path.exists(_prod) else -1
        self.assertEqual(_before, _after)

    def test_22_pulse_snapshot_schema(self):
        self._tick(hub=_HubPrivate(_rows(1)))
        _s = pulse.pulse_snapshot()
        self.assertEqual("autonomy_pulse/474.v3", _s["schema"])
        self.assertEqual("reasoning", _s["domain"])
        self.assertEqual(1, _s["total"])


class TestAutonomyPulseIronLaw(unittest.TestCase):
    """铁律 304 + 接线契约：把纪律做成可执行门禁。"""

    @staticmethod
    def _src(name: str) -> str:
        with open(os.path.join(_MOD_DIR, name), encoding="utf-8") as _fh:
            return _fh.read()

    def test_15_no_external_imports(self):
        _forbidden = {"requests", "urllib", "urllib3", "httpx", "aiohttp", "openai",
                      "zhipuai", "dashscope", "socket", "http", "boto3"}
        for _n in ast.walk(ast.parse(self._src("pulse.py"))):
            if isinstance(_n, ast.Import):
                for _a in _n.names:
                    self.assertNotIn(_a.name.split(".")[0], _forbidden)
            elif isinstance(_n, ast.ImportFrom):
                self.assertNotIn(str(_n.module or "").split(".")[0], _forbidden)

    def test_16_no_external_calls(self):
        _forbidden = {"urlopen", "urlretrieve", "chatcompletions", "completions",
                      "chat_completion", "invoke_model", "generate_content"}
        for _n in ast.walk(ast.parse(self._src("pulse.py"))):
            if not isinstance(_n, ast.Call):
                continue
            _fn = _n.func
            _name = _fn.id if isinstance(_fn, ast.Name) else getattr(_fn, "attr", "")
            self.assertNotIn(str(_name).lower(), _forbidden)

    def test_17_main_wiring_is_guarded(self):
        with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as _fh:
            _tree = ast.parse(_fh.read())
        _hits = []
        for _try in (n for n in ast.walk(_tree) if isinstance(n, ast.Try)):
            for _stmt in _try.body:
                for _n in ast.walk(_stmt):
                    if (isinstance(_n, ast.Call)
                            and getattr(_n.func, "id", getattr(_n.func, "attr", ""))
                            == "autonomy_observe_tick"):
                        _silent = any(
                            isinstance(_x, ast.Call)
                            and getattr(_x.func, "id", getattr(_x.func, "attr", ""))
                            == "silent_exc"
                            for _h in _try.handlers for _x in ast.walk(_h))
                        _hits.append((bool(_try.handlers), _silent))
        self.assertTrue(_hits, "main.py 未找到 autonomy_observe_tick 接线")
        self.assertTrue(all(_h and _s for _h, _s in _hits),
                        "接线必须包在 try/except 内且 handler 带 silent_exc")

    def test_18_config_documents_rollback(self):
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _fh:
            _s = _fh.read()
        self.assertIn("ENABLE_M474_AUTONOMY_PULSE = True", _s)
        self.assertIn("False ⇒ 逐字回滚", _s)

    def test_23_shadow_mode_is_still_default(self):
        """接线不得顺手打开 enforce：真实改写仍需显式授权。"""
        self.assertFalse(config.ENABLE_M474_AUTONOMY_KERNEL,
                         "enforce 开关必须保持影子模式缺省关")


if __name__ == "__main__":
    unittest.main()
