# -*- coding: utf-8 -*-
"""第37批 T4+T5 门控：额度监控关键事件日志 + 器官日志治理。

覆盖：预警阈值/三档告警/暂停与恢复事件/事件去重/stats 字段/
配对器与闭环检测器日志措辞/9 器官业务 print=0/自测块保留/E402 治理。
"""
import os
import sys

# ★注意：sys.path.insert(...) 是调用语句（不关闭 ruff import 区）；
#   `_ROOT = ...` 这类赋值会关闭 → 其后所有 import 报 E402。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ast
import io
import json
import logging
import subprocess
import tempfile
import unittest

import config
from nucleus.llm import ChannelQuotaMonitor as QM

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 测试内固定额度上限（真实实现按渠道名查 config 渠道池）
_LIMIT = 1000
# ★第64批 T6.2：原此处为模块级猴拳 `QM.ChannelQuotaMonitor._quota_limit_of = staticmethod(...)`，
#   在 import 时即永久改写类属性，导致同进程内 test_quota_monitor_m32 等被污染（7 例失败）。
#   现改为仅在 TestQuotaLogs.setUp 内打补丁、tearDown 内严格还原（见下方），杜绝跨模块泄漏。

ORGS = {
    "PulseBonding": "organs/genetic/PulseBonding.py",
    "PulseConsent": "organs/genetic/PulseConsent.py",
    "PulseThymus": "organs/immune/PulseThymus.py",
    "PulseNurture": "organs/genetic/PulseNurture.py",
    "PulseHands": "organs/motor/PulseHands.py",
    "PulseSkin": "organs/immune/PulseSkin.py",
    "PulseMouth": "organs/motor/PulseMouth.py",
    "PulseEars": "organs/senses/PulseEars.py",
    "PulseWhiteCell": "organs/immune/PulseWhiteCell.py",
}
ZERO_LOG_ORGS = ("PulseBonding", "PulseConsent", "PulseThymus", "PulseNurture")


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append((record.levelname, record.getMessage()))

    def lines(self):
        return [m for _, m in self.records]

    def by_level(self, lv):
        return [m for l, m in self.records if l == lv]


class TestQuotaLogs(unittest.TestCase):
    """1) 额度监控三档告警 + 暂停/恢复事件。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m37ql_")
        self.path = os.path.join(self.tmp, "u.json")
        self.col = _Collector()
        QM._module_logger.addHandler(self.col)
        # ★第64批 T6.2：额度上限补丁严格作用域（add+还原），不污染其它测试模块。
        #   必须从 __dict__ 取原始描述符（staticmethod 对象），否则经类访问会被解包成裸函数，
        #   还原后 self._quota_limit_of(name) 会多绑 self 触发 TypeError。
        self._orig_quota_limit_of = QM.ChannelQuotaMonitor.__dict__.get("_quota_limit_of")
        QM.ChannelQuotaMonitor._quota_limit_of = staticmethod(lambda name: _LIMIT)

    def tearDown(self):
        QM._module_logger.removeHandler(self.col)
        # ★第64批 T6.2：还原原始 staticmethod 描述符（即便测试异常也必须还原）
        if getattr(self, "_orig_quota_limit_of", None) is not None:
            try:
                QM.ChannelQuotaMonitor._quota_limit_of = self._orig_quota_limit_of
            except Exception:
                pass

    def _mon(self, ratio):
        m = QM.ChannelQuotaMonitor(usage_path=self.path)
        used = int(_LIMIT * (1.0 - ratio))
        m._usage["ch"] = {"prompt_tokens": used, "completion_tokens": 0,
                          "total_tokens_used": used, "last_updated": 0.0}
        return m

    @staticmethod
    def _ch():
        return {"name": "ch", "priority": 1, "quota_limit": _LIMIT}

    def test_10_alert_ratio_config_and_method(self):
        self.assertTrue(hasattr(config, "QUOTA_ALERT_RATIO"))
        self.assertEqual(config.QUOTA_ALERT_RATIO, 0.20)
        self.assertEqual(self._mon(0.5).alert_ratio(), 0.20)

    def test_11_alert_level_warning(self):
        self.col.records.clear()
        self._mon(0.15).apply_to_channels([self._ch()])
        self.assertTrue(any("低于20%阈值" in m for m in self.col.lines()),
                        self.col.lines())
        self.assertGreater(len(self.col.by_level("WARNING")), 0)

    def test_12_degrade_level(self):
        self.col.records.clear()
        _out = self._mon(0.08).apply_to_channels([self._ch()])
        self.assertEqual(_out[0]["priority"], 4, "剩余 8% 应降级 priority +3")

    def test_13_pause_level_removes_channel(self):
        self.col.records.clear()
        _out = self._mon(0.03).apply_to_channels([self._ch()])
        self.assertEqual(len(_out), 0, "剩余 3% 应剔除渠道")

    def test_14_pause_event_info(self):
        self.col.records.clear()
        self._mon(0.03).apply_to_channels([self._ch()])
        self.assertTrue(
            any("额度耗尽，已暂停使用" in m for m in self.col.by_level("INFO")),
            self.col.by_level("INFO"))

    def test_15_recover_event_info(self):
        """★额度恢复必须有 INFO 事件（此前完全无日志）。"""
        m = self._mon(0.03)
        m.apply_to_channels([self._ch()])
        self.col.records.clear()
        m._usage["ch"]["total_tokens_used"] = int(_LIMIT * 0.10)
        _out = m.apply_to_channels([self._ch()])
        self.assertTrue(
            any("额度已恢复，重新启用" in x for x in self.col.by_level("INFO")),
            self.col.by_level("INFO"))
        self.assertEqual(len(_out), 1)

    def test_16_no_false_recover_on_first_normal(self):
        """★语义：从未暂停/降级时额度充足**不是**"恢复"，不得打恢复日志。"""
        self.col.records.clear()
        self._mon(0.90).apply_to_channels([self._ch()])
        self.assertFalse([m for m in self.col.lines() if "已恢复" in m],
                         self.col.lines())

    def test_17_event_dedup(self):
        m = self._mon(0.03)
        for _ in range(4):
            m.apply_to_channels([self._ch()])
        _n = len([m for m in self.col.by_level("INFO") if "已暂停使用" in m])
        self.assertEqual(_n, 1, f"暂停事件应只打 1 次，实际 {_n}")

    def test_18_stats_exposes_fields(self):
        _s = self._mon(0.03).get_stats()
        self.assertIn("alert_ratio", _s)
        self.assertIn("events", _s)
        self.assertIsInstance(_s["events"], dict)

    def test_19_switch_off_zero_side_effect(self):
        self.col.records.clear()
        with _Switch(ENABLE_QUOTA_MONITOR=False):
            _in = [self._ch()]
            _out = self._mon(0.001).apply_to_channels(_in)
        self.assertEqual(_out, _in)
        self.assertEqual(len(self.col.records), 0)


class TestWordingDisambiguation(unittest.TestCase):
    """2) 日志措辞消歧（P2-218）。"""

    def test_20_matcher_log_has_source_caliber(self):
        _t = io.open(os.path.join(
            _ROOT, "nucleus/self_awareness/ProductionConsumptionMatcher.py"),
            encoding="utf-8").read()
        self.assertIn("源码文件 / %d 源码数据路径", _t)
        self.assertIn("覆盖率", _t)

    def test_21_matcher_report_has_channel_section(self):
        _t = io.open(os.path.join(
            _ROOT, "nucleus/self_awareness/ProductionConsumptionMatcher.py"),
            encoding="utf-8").read()
        self.assertIn("【扫描渠道】", _t)

    def test_22_fakeloop_log_has_source_caliber(self):
        _t = io.open(os.path.join(
            _ROOT, "nucleus/self_awareness/FakeLoopDetector.py"),
            encoding="utf-8").read()
        self.assertIn("扫描 %d 个源码文件", _t)
        self.assertNotIn("扫描完成: %d 文件 / %d 候选", _t)


class TestOrganLogGovernance(unittest.TestCase):
    """3) 9 器官日志治理（P2-229）。"""

    @staticmethod
    def _prints(path):
        """返回 (自测块内 print 数, 业务代码 print 数)。"""
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        main_r = []
        for n in tree.body:
            if isinstance(n, ast.If):
                try:
                    if "__main__" in ast.unparse(n.test):
                        main_r.append((n.lineno, n.end_lineno))
                except Exception:
                    pass
        a = b = 0
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                try:
                    if ast.unparse(n.func) != "print":
                        continue
                except Exception:
                    continue
                if any(x <= n.lineno <= y for x, y in main_r):
                    a += 1
                else:
                    b += 1
        return a, b

    def test_30_no_print_in_business_code(self):
        """★业务代码不得有 print（框架运行时信息必须走日志）。"""
        for _k, _p in ORGS.items():
            _a, _b = self._prints(os.path.join(_ROOT, _p))
            self.assertEqual(_b, 0, f"{_p} 业务代码仍有 {_b} 处 print")

    def test_31_selftest_prints_preserved(self):
        """★自测块 print 必须保留（自测需要控制台输出，改 _log 会破坏可用性）。"""
        _tot = sum(self._prints(os.path.join(_ROOT, p))[0] for p in ORGS.values())
        self.assertEqual(_tot, 77, f"自测块 print 合计应 77，实际 {_tot}")

    def test_32_selftest_blocks_documented(self):
        for _k, _p in ORGS.items():
            _t = io.open(os.path.join(_ROOT, _p), encoding="utf-8").read()
            self.assertIn("主线第37批 T5（P2-229）：本块是**开发自测**", _t, _p)

    def test_33_zero_log_organs_now_have_business_logs(self):
        for _k in ZERO_LOG_ORGS:
            _p = os.path.join(_ROOT, ORGS[_k])
            _src = io.open(_p, encoding="utf-8").read()
            _tree = ast.parse(_src)
            main_r = []
            for n in _tree.body:
                if isinstance(n, ast.If):
                    try:
                        if "__main__" in ast.unparse(n.test):
                            main_r.append((n.lineno, n.end_lineno))
                    except Exception:
                        pass
            _n = 0
            for n in ast.walk(_tree):
                if isinstance(n, ast.Call):
                    try:
                        if ast.unparse(n.func) != "self._log":
                            continue
                    except Exception:
                        continue
                    if not any(x <= n.lineno <= y for x, y in main_r):
                        _n += 1
            self.assertGreaterEqual(_n, 2, f"{_k} 业务 _log 不足（{_n}）")

    def test_34_log_level_imported(self):
        for _k in ZERO_LOG_ORGS:
            _src = io.open(os.path.join(_ROOT, ORGS[_k]),
                           encoding="utf-8").read()
            self.assertIn("LogLevel", _src.split("class ")[0], _k)

    def test_35_existing_organs_logs_intact(self):
        for _k in ("PulseHands", "PulseSkin", "PulseMouth", "PulseEars",
                   "PulseWhiteCell"):
            _t = io.open(os.path.join(_ROOT, ORGS[_k]), encoding="utf-8").read()
            self.assertGreaterEqual(_t.count("self._log("), 1, _k)

    def test_36_thymus_e402_fixed(self):
        """★顺手治理：悬空第二 docstring → 注释块，E402 5 → 0。"""
        _t = io.open(os.path.join(_ROOT, ORGS["PulseThymus"]),
                     encoding="utf-8").read()
        self.assertIn("_m37_t5_e402", _t)


class TestNoE402Regression(unittest.TestCase):
    """4) 9 器官 E402 零新增（除 Thymus 已治理）。"""

    def test_40_e402_counts(self):
        import shutil
        _ruff = shutil.which("ruff") or r"D:\Program Files\Python312\Scripts\ruff.exe"
        if not os.path.exists(_ruff):
            self.skipTest("ruff 不可用")
        _expect = {"PulseThymus": 0, "PulseSkin": 3, "PulseEars": 9,
                   "PulseWhiteCell": 5}
        for _k, _p in ORGS.items():
            _r = subprocess.run([_ruff, "check", "--select", "E402",
                                 "--output-format", "json", _p],
                                capture_output=True, text=True, cwd=_ROOT)
            _n = len(json.loads(_r.stdout or "[]"))
            if _k in _expect:
                self.assertEqual(_n, _expect[_k], f"{_p} E402={_n}")
            else:
                self.assertEqual(_n, 0, f"{_p} E402={_n}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
