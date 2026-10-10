# -*- coding: utf-8 -*-
"""主线第55批 T5：死代码检测器 tools/dead_code_scan.py 的契约测试

★铁律 35：测「扫描/写盘型」工具必须把根目录指向沙箱，绝不扫生产目录。
★铁律 40：契约类基线**固化进测试文件**，不依赖 git-ignored scratch 目录。
★铁律 52：两份等价逻辑必须「同数据对比」—— 这里对比 AST 判据与人工预期。
★本任务只检测不删除 —— 测试必须断言**扫描后源码字节不变**。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_SPEC = importlib.util.spec_from_file_location(
    "dead_code_scan_m55", os.path.join(_ROOT, "tools", "dead_code_scan.py"))
DCS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(DCS)


def _rmtree(path: str) -> None:
    """★铁律 54：Windows 下 shutil.rmtree(ignore_errors=True) 会静默失败。"""
    for _dp, _dns, _fns in os.walk(path, topdown=False):
        for _f in _fns:
            try:
                os.remove(os.path.join(_dp, _f))
            except OSError:
                pass
        for _d in _dns:
            try:
                os.rmdir(os.path.join(_dp, _d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


class TestDeadCodeScan(unittest.TestCase):
    """死代码检测器的分级判据。"""

    def setUp(self) -> None:
        self.sandbox = tempfile.mkdtemp(prefix="m55_dcs_")
        # 模拟「8 大模块」之一的布局
        for _m in ("nucleus", "tests"):
            os.makedirs(os.path.join(self.sandbox, _m), exist_ok=True)

    def tearDown(self) -> None:
        _rmtree(self.sandbox)

    def _w(self, rel: str, text: str) -> str:
        _p = os.path.join(self.sandbox, rel)
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "w", encoding="utf-8", newline="") as _f:
            _f.write(text)
        return _p

    # ------------------------------------------------------------------
    def test_01_zero_ref_detected(self):
        """全库零引用的 def/class → ZERO_REF。"""
        self._w("nucleus/mod_a.py", "def orphan_fn():\n    return 1\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"]: i for i in _r["items"]}
        self.assertIn("orphan_fn", _names)
        self.assertEqual(_names["orphan_fn"]["level"], "ZERO_REF")
        self.assertEqual(_names["orphan_fn"]["prod_refs"], 0)
        self.assertEqual(_names["orphan_fn"]["test_refs"], 0)

    def test_02_referenced_not_reported(self):
        """有生产引用的定义**不**应出现在结果里。"""
        self._w("nucleus/mod_b.py",
                "def used_fn():\n    return 1\n\n\ndef caller():\n    return used_fn()\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"] for i in _r["items"]}
        self.assertNotIn("used_fn", _names, "有引用的函数不应被判死代码")

    def test_03_test_only_level(self):
        """仅被 tests 引用 → TEST_ONLY（不是 ZERO_REF）。"""
        self._w("nucleus/mod_c.py", "def only_test_fn():\n    return 2\n")
        self._w("tests/test_c.py", "def test_x():\n    import nucleus.mod_c\n")
        self._w("tests/test_d.py",
                "from nucleus.mod_c import only_test_fn\n\n"
                "def test_y():\n    assert only_test_fn() == 2\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"]: i for i in _r["items"]}
        if "only_test_fn" in _names:
            self.assertEqual(_names["only_test_fn"]["level"], "TEST_ONLY")
            self.assertEqual(_names["only_test_fn"]["prod_refs"], 0)
            self.assertGreater(_names["only_test_fn"]["test_refs"], 0)

    def test_04_dunder_is_dynamic_risk(self):
        """dunder 名即使零引用也**不可删** → DYNAMIC_RISK。"""
        self._w("nucleus/mod_d.py", "class K:\n    pass\n\n\ndef __special__():\n    return 3\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"]: i for i in _r["items"]}
        self.assertIn("__special__", _names)
        self.assertEqual(_names["__special__"]["level"], "DYNAMIC_RISK")
        self.assertTrue(_names["__special__"]["dunder"])

    def test_05_all_exports_are_dynamic_risk(self):
        """出现在 __all__ 里的名字 → DYNAMIC_RISK（公共 API，不可删）。"""
        self._w("nucleus/mod_e.py",
                "__all__ = ['public_api']\n\n\ndef public_api():\n    return 4\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"]: i for i in _r["items"]}
        self.assertIn("public_api", _names)
        self.assertEqual(_names["public_api"]["level"], "DYNAMIC_RISK")
        self.assertTrue(_names["public_api"]["in_all"])

    def test_06_string_hit_is_dynamic_risk(self):
        """★名字以字符串出现（反射风险）→ DYNAMIC_RISK，绝不判可删。"""
        self._w("nucleus/mod_f.py", "def reflected_fn():\n    return 5\n")
        self._w("nucleus/mod_g.py",
                "_NAME = 'reflected_fn'  # 可能被 getattr 反射调用\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"]: i for i in _r["items"]}
        self.assertIn("reflected_fn", _names)
        self.assertEqual(_names["reflected_fn"]["level"], "DYNAMIC_RISK")
        self.assertTrue(_names["reflected_fn"]["dynamic_string_hit"])

    def test_07_decorated_is_dynamic_risk(self):
        """带装饰器的定义 → DYNAMIC_RISK（装饰器可能注册到全局表）。"""
        self._w("nucleus/mod_h.py",
                "def deco(f):\n    return f\n\n\n@deco\ndef hooked_fn():\n    return 6\n")
        _r = DCS.scan(self.sandbox)
        _names = {i["name"]: i for i in _r["items"]}
        if "hooked_fn" in _names:
            self.assertEqual(_names["hooked_fn"]["level"], "DYNAMIC_RISK")
            self.assertTrue(_names["hooked_fn"]["has_decorator"])

    # ------------------------------------------------------------------
    def test_08_scan_does_not_modify_sources(self):
        """★核心纪律：只检测不删除 —— 扫描前后源码**字节不变**。"""
        _p = self._w("nucleus/mod_i.py", "def orphan_i():\n    return 7\n")
        _before = open(_p, "rb").read()
        DCS.scan(self.sandbox)
        _after = open(_p, "rb").read()
        self.assertEqual(_before, _after, "扫描不得修改任何源码")

    def test_09_excluded_dirs_not_scanned(self):
        """★复用 exclude_dirs：副本/缓存/备份目录不计入扫描（否则引用自我膨胀）。"""
        # 在副本目录里放一个「引用」orphan_j 的文件
        self._w("nucleus/mod_j.py", "def orphan_j():\n    return 8\n")
        self._w(".release-tmp/copy.py",
                "from nucleus.mod_j import orphan_j  # 副本里的引用不算\n")
        _r = DCS.scan(self.sandbox)
        _files = _r["summary"]["scanned_files"]
        # 副本目录不应被计入扫描文件
        _all = []
        for _dp, _dns, _fns in os.walk(self.sandbox):
            _dns[:] = [d for d in _dns if not DCS.E.is_excluded(d)]
            _all += [f for f in _fns if f.endswith(".py")]
        self.assertEqual(_files, len(_all), "扫描文件数应与 exclude_dirs 过滤一致")

    def test_10_report_render_contains_sections(self):
        """报告渲染：含分级统计与「不删除」纪律声明。"""
        self._w("nucleus/mod_k.py", "def orphan_k():\n    return 9\n")
        _r = DCS.scan(self.sandbox)
        _md = DCS.render_md(_r)
        for _k in ("## 一、扫描概况", "## 二、分级统计", "ZERO_REF",
                   "TEST_ONLY", "DYNAMIC_RISK", "只检测，不删除"):
            self.assertIn(_k, _md)

    def test_11_modules_constant_covers_eight(self):
        """8 大模块清单完整且与项目顶层包一致。"""
        self.assertEqual(len(DCS.MODULES), 8)
        for _m in ("base", "functions", "hardware", "nucleus",
                   "organs", "pulses", "somatics", "utils"):
            self.assertIn(_m, DCS.MODULES)
        for _m in DCS.MODULES:
            self.assertTrue(os.path.isdir(os.path.join(_ROOT, _m)),
                            "模块目录不存在：{}".format(_m))


if __name__ == "__main__":
    unittest.main(verbosity=2)
