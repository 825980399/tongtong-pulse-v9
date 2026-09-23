# -*- coding: utf-8 -*-
"""
test_snapshot_diagnostic_m16.py —— 主线第16批 任务4 门控单测（P2-103 快照 0 节点）

根因：`_verify_integrity` 对「恢复 0 节点」无条件报 ERROR，无法区分
「快照本身为空（首次启动，正常）」与「声明 N>0 却恢复出 0（加载失败）」。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


def _snap(path="data/knowledge/pulse_knowledge_snapshot.json"):
    """轻量实例化：只挂 _verify_integrity 需要的属性。"""
    s = PulseSnapshot.__new__(PulseSnapshot)
    s.snapshot_path = os.path.join(_PROJECT_ROOT, path)
    s._records = []
    s._log = lambda level, msg, *a, **k: s._records.append((str(level), msg))
    return s


class TestEmptyVsFailure(unittest.TestCase):
    def test_config_default(self):
        self.assertIs(getattr(config, "ENABLE_SNAPSHOT_DIAGNOSTIC", None), True)

    def test_declared_zero_is_info_and_passes(self):
        s = _snap()
        _ok = s._verify_integrity(
            {"node_count_at_save": 0, "nodes": [], "node_list_checksum": None}, [])
        self.assertTrue(_ok, "声明 0 节点应放行（首次启动属正常）")
        _msgs = [m for _, m in s._records]
        self.assertTrue(any("快照为空" in m for m in _msgs), _msgs)
        self.assertFalse(any(("ERROR" in lv) or ("错误" in lv) for lv, _ in s._records),
                         s._records)

    def test_declared_nonzero_but_zero_restored_is_error(self):
        s = _snap()
        _ok = s._verify_integrity(
            {"node_count_at_save": 5, "nodes": [1, 2, 3, 4, 5],
             "node_list_checksum": None, "version": "v9.5"}, [])
        self.assertFalse(_ok, "声明 N>0 却恢复 0 必须判失败")
        _msgs = [m for _, m in s._records]
        self.assertTrue(any("加载失败" in m for m in _msgs), _msgs)
        _joined = " ".join(_msgs)
        self.assertIn("node_count_at_save=5", _joined, "诊断串必须含声明节点数")
        self.assertIn("nodes长度=5", _joined, "诊断串必须含 nodes 长度")

    def test_switch_off_reverts_to_unconditional_error(self):
        with _Switch(ENABLE_SNAPSHOT_DIAGNOSTIC=False):
            s = _snap()
            _ok = s._verify_integrity(
                {"node_count_at_save": 0, "nodes": [], "node_list_checksum": None}, [])
            self.assertFalse(_ok, "开关关闭时应退回「无条件 ERROR + 拒绝」")
            self.assertTrue(any("恢复的节点数为0" in m for _, m in s._records))


class TestDiagnosticString(unittest.TestCase):
    def test_diagnostic_reports_file_and_shape(self):
        s = _snap()
        _d = s._snapshot_diagnostic({"version": "v9.5", "node_count_at_save": 3,
                                     "nodes": [1, 2, 3]})
        self.assertIn("大小=", _d)
        self.assertIn("版本=v9.5", _d)
        self.assertIn("nodes类型=list", _d)
        self.assertIn("nodes长度=3", _d)

    def test_diagnostic_handles_none_and_wrong_type(self):
        s = _snap()
        self.assertIn("data=None", s._snapshot_diagnostic(None))
        self.assertIn("顶层类型异常", s._snapshot_diagnostic(["not-a-dict"]))


class TestSourceWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_PROJECT_ROOT, "nucleus", "mnemosyne",
                                  "PulseSnapshot.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_save_time_warning_present(self):
        self.assertIn("保存快照时有效节点数为 0", self.src)

    def test_switch_wired(self):
        self.assertIn("_snapshot_diagnostic_enabled()", self.src)


if __name__ == "__main__":
    unittest.main()
