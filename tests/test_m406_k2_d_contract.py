# -*- coding: utf-8 -*-
"""406 刀2 判据：D 档 10 条契约断裂的判定表 + 已修 3 条的接线与行为。

四组断言：
  A 判定表：附表落盘、10 条齐全、判定列三选一、该有→修恰 3 条（#9 #10 #15）
  B 接线：AST 钉住「新增发射点真的在预期函数里」，而不只是"文件里出现过这个词"
  C 行为（本体感知）：首帧补报 / 无变化静默 / 变化成对上报 / 事件回灌不 flapping
  D 行为（崩溃钩子）：真 exec 源码块 —— payload 字段、publish、节流、上限、框架缺失不抛

★为什么 C/D 两组都要"真跑"而不是文本断言：
  #9/#10 的风险是**自环**（事件回灌改状态 → 再触发），静态看不出来；
  #15 的风险是**节流失效**（线程反复抛错打爆免疫系统），也只有真调才暴露。
"""
import ast
import builtins
import io
import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_ROOT, "docs", "路灯与星轨对话", "分析报告",
                    "路灯_406批_D档10条判定_20261031.md")
_PP = os.path.join(_ROOT, "organs", "core", "PulseProprioception.py")
_MAIN = os.path.join(_ROOT, "main.py")


def _read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def _func_src(path, name):
    src = _read(path)
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n), n
    return None, None


class TestA_Table(unittest.TestCase):
    """判定表必须真落盘且 10 条齐全 —— 表没写等于没判定。"""

    ROWS = ["device.allocate", "hardware.device.attached", "hardware.device.detached",
            "persona.query", "persona.update", "qica.classify", "sensor.camera.frame",
            "system.error_report", "system.health_check", "system.start_service"]

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_DOC):
            raise unittest.SkipTest("判定附表不存在：%s" % _DOC)
        cls.doc = _read(_DOC)

    def test_01_all_ten_present(self):
        for r in self.ROWS:
            self.assertIn(r, self.doc, "判定表缺事件：%s" % r)

    def test_02_three_verdicts_only(self):
        """判定只能是三选一 —— 出现第四种说法说明口径漂了。"""
        sec = self.doc.split("## 二、10 条判定（三选一）")[1].split("## 三、")[0]
        rows = [ln for ln in sec.split("\n") if ln.startswith("| ") and "`" in ln]
        self.assertEqual(len(rows), 10, "判定行数不是 10（实际 %d）" % len(rows))
        for ln in rows:
            self.assertTrue(("观察" in ln) or ("该有 → 已修" in ln) or ("不该有 → 撤订阅" in ln),
                            "判定不在三选一内：%s" % ln[:80])

    def test_03_fixed_count_is_three(self):
        sec = self.doc.split("## 二、10 条判定（三选一）")[1].split("## 三、")[0]
        self.assertEqual(sec.count("该有 → 已修"), 3, "该有→修 应恰 3 条")

    def test_04_observed_count_is_seven(self):
        sec = self.doc.split("## 二、10 条判定（三选一）")[1].split("## 三、")[0]
        self.assertEqual(sec.count("**观察**"), 7, "观察 应恰 7 条")

    def test_05_no_subscription_removed(self):
        """本批一条都没撤订阅 —— 撤订阅是破坏性动作，必须有明确证据才做。"""
        sec = self.doc.split("## 二、10 条判定（三选一）")[1].split("## 三、")[0]
        self.assertEqual(sec.count("不该有 → 撤订阅"), 0)

    def test_06_constants_corrected(self):
        """附表必须记下 405 清单的一处归属修正（#9/#10/#15 实际属 SystemEvent）。"""
        self.assertIn("SystemEvent.DEVICE_ATTACHED", self.doc)
        self.assertIn("SystemEvent.ERROR_REPORT", self.doc)


class TestB_Wiring(unittest.TestCase):
    """AST 接线：新增发射点必须真在所声称的函数体内。"""

    @classmethod
    def setUpClass(cls):
        cls.pp_src = _read(_PP)
        cls.main_src = _read(_MAIN)

    def test_10_transition_method_exists(self):
        _seg, node = _func_src(_PP, "_m406_report_hw_transitions")
        self.assertIsNotNone(node, "PulseProprioception 缺 _m406_report_hw_transitions")

    def test_11_called_from_evaluate(self):
        seg, _ = _func_src(_PP, "_evaluate_hardware_capabilities")
        self.assertIsNotNone(seg)
        self.assertIn("self._m406_report_hw_transitions(dc)", seg,
                      "变化上报未接在 _evaluate_hardware_capabilities 里")
        # 必须紧随能力矩阵赋值之后（在 caps 已经写回 self 之后才上报）
        self.assertGreater(seg.index("self._m406_report_hw_transitions(dc)"),
                           seg.index("self._hardware_capabilities = caps"))

    def test_12_emits_attached_and_detached(self):
        seg, _ = _func_src(_PP, "_m406_report_hw_transitions")
        self.assertIn("SystemEvent.DEVICE_ATTACHED", seg)
        self.assertIn("SystemEvent.DEVICE_DETACHED", seg)

    def test_13_signature_from_dc_not_capabilities(self):
        """★防自环的结构保证：函数体**代码**不得读 self._hardware_capabilities。

        ★必须用 AST 而不是字符串包含 —— 本函数的 docstring 里正是在**解释**
        「为什么不用 `self._hardware_capabilities`」，文本匹配会把这句解释判成违规。
        """
        tree = ast.parse(_read(_PP))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "_m406_report_hw_transitions":
                bad = [x for x in ast.walk(n)
                       if isinstance(x, ast.Attribute)
                       and x.attr == "_hardware_capabilities"
                       and isinstance(x.value, ast.Name)
                       and x.value.id == "self"]
                self.assertEqual(
                    [], bad,
                    "函数体代码读了 self._hardware_capabilities —— 会与 "
                    "_on_device_attached 形成 flapping 自环")
                return
        self.fail("PulseProprioception 缺 _m406_report_hw_transitions")

    def test_14_crash_hook_emits_error_report(self):
        seg, node = _func_src(_MAIN, "_crash_hook")
        self.assertIsNotNone(node, "main.py 缺 _crash_hook")
        self.assertIn("SystemEvent", seg)
        self.assertIn("ERROR_REPORT", seg)

    def test_15_crash_hook_emit_is_guarded(self):
        """上报块必须在 try/except 里，且 except 有留痕 —— 否则崩溃钩子会因上报失败而丢取证。"""
        seg, _ = _func_src(_MAIN, "_crash_hook")
        i = seg.index("ERROR_REPORT")
        self.assertIn("try:", seg[:i])
        tail = seg[i:]
        self.assertIn("except Exception", tail)
        self.assertIn("silent_exc", tail)

    def test_16_excepthook_still_installed(self):
        self.assertIn("sys.excepthook = _crash_hook", self.main_src,
                      "sys.excepthook 挂载被破坏")
        self.assertIn("threading.excepthook", self.main_src,
                      "threading.excepthook 挂载被破坏")

    def test_17_throttle_constants_present(self):
        self.assertIn("_M406_ER_MAX_TOTAL", self.main_src)
        self.assertIn("_M406_ER_MIN_INTERVAL", self.main_src)


class _PPBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from organs.core.PulseProprioception import PulseProprioception
        cls.Cls = PulseProprioception

    def _mk(self):
        p = self.Cls.__new__(self.Cls)
        p._device_capabilities = {}
        p._hardware_capabilities = {}
        p._sent = []
        p._emit = lambda et, pl=None, **k: p._sent.append((et, dict(pl or {})))
        p._log = lambda *a, **k: None
        p._has_gpu_compute = lambda: False
        p._get_gpu_devices = lambda: []
        return p


class TestC_ProprioceptionBehaviour(_PPBase):
    def test_20_first_frame_reports_available(self):
        p = self._mk()
        p._device_capabilities = {"sensor.camera.available": True}
        p._evaluate_hardware_capabilities()
        self.assertEqual(len(p._sent), 1, p._sent)
        self.assertEqual(p._sent[0][0], "hardware.device.attached")
        self.assertTrue(p._sent[0][1]["initial"])

    def test_21_first_frame_silent_for_absent(self):
        """首帧不噪报"未接入"的设备 —— 否则每次启动都刷三条 detached。"""
        p = self._mk()
        p._device_capabilities = {}
        p._evaluate_hardware_capabilities()
        self.assertEqual(p._sent, [])

    def test_22_no_change_is_silent(self):
        p = self._mk()
        p._device_capabilities = {"sensor.camera.available": True}
        p._evaluate_hardware_capabilities()
        p._sent = []
        p._evaluate_hardware_capabilities()
        self.assertEqual(p._sent, [])

    def test_23_attach_reported(self):
        p = self._mk()
        p._device_capabilities = {"sensor.camera.available": True}
        p._evaluate_hardware_capabilities()
        p._sent = []
        p._device_capabilities["sensor.microphone.available"] = True
        p._evaluate_hardware_capabilities()
        self.assertEqual(len(p._sent), 1, p._sent)
        self.assertEqual(p._sent[0][0], "hardware.device.attached")
        self.assertEqual(p._sent[0][1]["device_type"], "microphone")
        self.assertFalse(p._sent[0][1]["initial"])

    def test_24_detach_reported(self):
        p = self._mk()
        p._device_capabilities = {"sensor.camera.available": True}
        p._evaluate_hardware_capabilities()
        p._sent = []
        p._device_capabilities["sensor.camera.available"] = False
        p._evaluate_hardware_capabilities()
        self.assertEqual(len(p._sent), 1, p._sent)
        self.assertEqual(p._sent[0][0], "hardware.device.detached")
        self.assertEqual(p._sent[0][1]["device_type"], "camera")

    def test_25_event_feedback_no_flapping(self):
        """★核心：事件回灌（_on_device_attached 改 _hardware_capabilities）后不得反复上报。"""
        p = self._mk()
        p._device_capabilities = {"sensor.camera.available": True}
        p._evaluate_hardware_capabilities()
        p._sent = []
        for _ in range(3):
            p._on_device_attached({"device_type": "camera"})
            p._evaluate_hardware_capabilities()
        self.assertEqual(p._sent, [], "出现 flapping 自环：%r" % (p._sent,))

    def test_26_returns_count(self):
        p = self._mk()
        self.assertEqual(p._m406_report_hw_transitions({"sensor.camera.available": True}), 1)
        self.assertEqual(p._m406_report_hw_transitions({"sensor.camera.available": True}), 0)

    def test_27_emit_failure_does_not_break_caps(self):
        p = self._mk()
        p._device_capabilities = {"sensor.camera.available": True}

        def _boom(*a, **k):
            raise RuntimeError("emit-boom")
        p._emit = _boom
        p._evaluate_hardware_capabilities()          # 不得抛出
        self.assertIn("visual_sensing", p._hardware_capabilities)

    def test_28_consumer_understands_payload(self):
        """产出的 device_type 必须能被消费方 _on_device_attached 识别（否则等于白发）。"""
        p = self._mk()
        for dt, cap in (("camera", "visual_sensing"), ("microphone", "auditory_input")):
            p._hardware_capabilities = {cap: {"available": False}}
            p._on_device_attached({"device_type": dt})
            self.assertTrue(p._hardware_capabilities[cap]["available"], dt)


class TestD_CrashHookBehaviour(unittest.TestCase):
    """真 exec `_crash_hook` 源码块 —— 不 import main（会启动框架）。"""

    @classmethod
    def setUpClass(cls):
        seg, node = _func_src(_MAIN, "_crash_hook")
        if node is None:
            raise unittest.SkipTest("main.py 缺 _crash_hook")
        cls.seg = seg

    def _load(self):
        core, fld = _FakeCore(), _FakeField()
        fw = type("FW", (), {})()
        fw.pulse_core = core
        fw.info_field = fld
        writes = []

        class _W:
            def write(self, s):
                writes.append(s)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        w = _W()

        def _open(path, mode="r", *a, **k):
            return w if ("a" in mode or "w" in mode) else io.StringIO()

        bset = dict(vars(builtins))
        bset["open"] = _open
        env = {
            "os": os, "time": __import__("time"),
            "silent_exc": lambda *a, **k: None,
            "framework": fw,
            "_m406_er_state": {"last": {}, "total": 0},
            "_M406_ER_MAX_TOTAL": 50,
            "_M406_ER_MIN_INTERVAL": 60.0,
            "__builtins__": bset,
        }
        exec(textwrap.dedent(self.seg), env)     # noqa: S102
        return env, core, fld, writes

    def test_30_emits_once(self):
        env, core, fld, _writes = self._load()
        env["_crash_hook"](ValueError, ValueError("boom-406"), None)
        self.assertEqual(len(core.calls), 1, core.calls)
        self.assertEqual(len(fld.pubs), 1)

    def test_31_payload_contract(self):
        env, core, _, _ = self._load()
        env["_crash_hook"](ValueError, ValueError("boom-406"), None)
        c = core.calls[0]
        self.assertEqual(c["event_type"], "system.error_report")
        self.assertEqual(c["source_organ"], "框架")
        self.assertEqual(c["layer"], "L0")
        self.assertEqual(c["priority"], 8)
        pl = c["payload"]
        self.assertEqual(pl["error_type"], "ValueError")
        self.assertEqual(pl["error_message"], "boom-406")
        self.assertEqual(pl["origin"], "uncaught_exception")

    def test_32_crash_log_still_written(self):
        """上报不得顶掉崩溃日志 —— 那是最后的取证途径。"""
        env, _, _, writes = self._load()
        env["_crash_hook"](ValueError, ValueError("boom-406"), None)
        self.assertEqual(len(writes), 1, writes)
        self.assertIn("UNCAUGHT", writes[0])

    def test_33_throttle_same_fingerprint(self):
        env, core, _, _ = self._load()
        env["_crash_hook"](ValueError, ValueError("same"), None)
        env["_crash_hook"](ValueError, ValueError("same"), None)
        self.assertEqual(len(core.calls), 1, "同指纹 60s 内不得重发")

    def test_34_different_fingerprint_passes(self):
        env, core, _, _ = self._load()
        env["_crash_hook"](ValueError, ValueError("a"), None)
        env["_crash_hook"](KeyError, KeyError("b"), None)
        self.assertEqual(len(core.calls), 2)

    def test_35_total_cap(self):
        env, core, _, _ = self._load()
        env["_m406_er_state"]["total"] = 50
        env["_crash_hook"](TypeError, TypeError("t"), None)
        self.assertEqual(len(core.calls), 0, "总量上限未生效")

    def test_36_missing_framework_is_safe(self):
        env, core, _, writes = self._load()
        env["framework"] = None
        env["_crash_hook"](RuntimeError, RuntimeError("nofw"), None)   # 不得抛
        self.assertEqual(len(core.calls), 0)
        self.assertEqual(len(writes), 1)

    def test_37_emit_failure_is_safe(self):
        env, _core, _, writes = self._load()

        class _Bad:
            def emit(self, **k):
                raise RuntimeError("emit-boom")
        env["framework"].pulse_core = _Bad()
        env["_crash_hook"](RuntimeError, RuntimeError("x"), None)      # 不得抛
        self.assertEqual(len(writes), 1)


class _FakeField:
    def __init__(self):
        self.pubs = []

    def publish(self, pulse):
        self.pubs.append(pulse)


class _FakeCore:
    def __init__(self):
        self.calls = []

    def emit(self, **kw):
        self.calls.append(kw)
        return {"pulse": kw}


if __name__ == "__main__":
    unittest.main(verbosity=2)
