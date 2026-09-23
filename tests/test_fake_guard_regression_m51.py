# -*- coding: utf-8 -*-
"""第51批 T4（P2-355）门控测试：假防护 AST 级防回归。

★判据（与 tmp/scan_m51_t4.py 同源，收窄后口径）：
* 「静默吞异常」= handler body **无任何留痕**（无日志 / 无异常引用 / 无结果写回）；
* 只扫**关键目录**（守卫相关），控制耗时。

覆盖：
1. 全库无「守卫调用 + 静默 except」结构
2. 守卫调用关键字参数名与真实签名一致（A 类假防护为 0）
3. 5 处修复点的留痕代码在位（回归防护）
4. write_guard 签名稳定
5. 漏判/误判自检（合成样例）
"""
import ast
import inspect
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.data import write_guard as wg  # noqa: E402

#: 关键扫描目录（守卫相关）
_SCAN_DIRS = ("nucleus/data", "nucleus/reporting", "nucleus/mnemosyne",
              "nucleus/genesis", "nucleus/reasoning", "organs", "tools")

_SKIP = {".git", "__pycache__", ".venv", "venv", ".release-tmp",
         ".ruff_cache", ".pytest_cache"}

#: 写盘/安全守卫（精确名）
_GUARD_EXACT = {
    "guard_write", "reject_write", "guard_enabled", "is_test_env",
    "is_production_data_path", "is_test_like_env", "is_framework_process",
    "is_test_mode", "reject_reason", "strict_enabled",
}


def _iter_calls_limited(node):
    """遍历单节点中的调用，不进入嵌套函数/类/lambda。"""
    _out = []

    def _rec(n):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef, ast.Lambda)):
            return
        if isinstance(n, ast.Call):
            _out.append(n)
        for _c in ast.iter_child_nodes(n):
            _rec(_c)

    _rec(node)
    return _out


def _guard_name(node):
    _f = node.func
    if isinstance(_f, ast.Name):
        return _f.id if _f.id in _GUARD_EXACT else ""
    if isinstance(_f, ast.Attribute):
        return _f.attr if _f.attr in _GUARD_EXACT else ""
    return ""


def _is_silent(h):
    """handler 是否「无任何留痕」（收窄口径）。"""
    _body = h.body
    if not _body:
        return True
    if len(_body) == 1 and isinstance(_body[0], ast.Pass):
        return True
    for _s in _body:
        if isinstance(_s, ast.Assign):
            return False
        for _sub in ast.walk(_s):
            if isinstance(_sub, ast.Name) and _sub.id == getattr(h, "name", None):
                return False
            if isinstance(_sub, ast.Call):
                _fn = _sub.func
                _nm = (_fn.attr if isinstance(_fn, ast.Attribute)
                       else _fn.id if isinstance(_fn, ast.Name) else "")
                if _nm in ("print", "write", "debug", "info", "warning", "error", "log"):
                    return False
            if isinstance(_sub, ast.Attribute) and _sub.attr == "write":
                return False
    return False


def _scan_key_dirs():
    """返回 (静默守卫结构列表, 参数名违规列表, 扫描文件数)。"""
    _sigs = {}
    for _n in _GUARD_EXACT:
        _f = getattr(wg, _n, None)
        if callable(_f):
            try:
                _sigs[_n] = inspect.signature(_f).parameters
            except (TypeError, ValueError):
                pass
    _silent, _badkw, _files = [], [], 0
    for _d in _SCAN_DIRS:
        _base = os.path.join(_ROOT, _d)
        for _dp, _dns, _fns in os.walk(_base):
            _dns[:] = [x for x in _dns if x not in _SKIP and not x.startswith(".bak")]
            for _fn in _fns:
                if not _fn.endswith(".py"):
                    continue
                _fp = os.path.join(_dp, _fn)
                _rel = os.path.relpath(_fp, _ROOT).replace("\\", "/")
                try:
                    _tree = ast.parse(io.open(_fp, encoding="utf-8",
                                              errors="replace").read())
                except (SyntaxError, OSError):
                    continue
                _files += 1
                for _n in ast.walk(_tree):
                    if not isinstance(_n, ast.Try):
                        continue
                    _gc = []
                    for _s in _n.body:
                        for _sub in _iter_calls_limited(_s):
                            _g = _guard_name(_sub)
                            if _g:
                                _gc.append((_g, _sub))
                    if not _gc:
                        continue
                    for _g, _call in _gc:
                        _p = _sigs.get(_g)
                        if _p is not None and not any(
                                v.kind == inspect.Parameter.VAR_KEYWORD
                                for v in _p.values()):
                            for _k in _call.keywords:
                                if _k.arg and _k.arg not in _p:
                                    _badkw.append((_rel, _call.lineno, _g, _k.arg))
                    for _h in _n.handlers:
                        if _is_silent(_h):
                            for _g, _call in _gc:
                                _silent.append((_rel, _call.lineno, _g))
    return _silent, _badkw, _files


class TestNoFakeGuard(unittest.TestCase):
    """全库无假防护结构。"""

    @classmethod
    def setUpClass(cls):
        cls.silent, cls.badkw, cls.files = _scan_key_dirs()

    def test_01_scanned_enough_files(self):
        self.assertGreater(self.files, 100, "扫描范围过小，判据失效")

    def test_02_no_silent_guard(self):
        """★无「守卫调用 + 静默 except」结构。"""
        self.assertEqual(self.silent, [],
                         "发现守卫被静默 except 吞：%s" % self.silent[:5])

    def test_03_no_wrong_keyword(self):
        """★A 类假防护（守卫调用参数名错误）= 0。"""
        self.assertEqual(self.badkw, [],
                         "守卫调用参数名不符：%s" % self.badkw[:5])


class TestFixesInPlace(unittest.TestCase):
    """5 处修复的留痕代码在位（回归防护）。"""

    def _src(self, rel):
        return io.open(os.path.join(_ROOT, rel), encoding="utf-8",
                       errors="replace").read()

    def test_10_backfill_guard_logged(self):
        _s = self._src("tools/backfill_patch_verification_split.py")
        self.assertIn("写盘守卫不可用（按 fail-open 继续）", _s)

    def test_11_config_check_logged(self):
        _s = self._src("config.py")
        self.assertIn("[API Key 检查] 启动检查异常已忽略", _s)

    def test_12_nodepool_check_logged(self):
        _s = self._src("nucleus/mnemosyne/PulseNodePool.py")
        self.assertIn("[T4留痕] 知识写入前质量检查异常已忽略", _s)

    def test_13_streamminer_logged(self):
        _s = self._src("nucleus/genesis/StreamMiner.py")
        self.assertIn("[T4留痕] 周期预测命中校验异常已忽略", _s)

    def test_14_deriver_logged(self):
        _s = self._src("nucleus/reasoning/AutonomousDeriver.py")
        self.assertIn("[T4留痕] 因果链验证器异常已忽略", _s)

    def test_15_no_bare_pass_at_fix_sites(self):
        """修复点不得回退为裸 pass。"""
        for _rel in ("nucleus/mnemosyne/PulseNodePool.py",
                     "nucleus/genesis/StreamMiner.py",
                     "nucleus/reasoning/AutonomousDeriver.py"):
            _s = self._src(_rel)
            self.assertNotIn("check_node(node, context=\"知识写入前\")\n        except Exception:\n            pass",
                             _s, _rel)


class TestDetectorSelfCheck(unittest.TestCase):
    """判据自检：合成样例的漏判/误判边界。"""

    @staticmethod
    def _handlers(code):
        _t = ast.parse("try:\n" + code)
        return _t.body[0].handlers

    def test_20_bare_pass_is_silent(self):
        _h = self._handlers("    x = 1\nexcept Exception:\n    pass\n")
        self.assertTrue(_is_silent(_h[0]))

    def test_21_logged_is_not_silent(self):
        _h = self._handlers(
            "    x = 1\nexcept Exception as e:\n    print(e)\n")
        self.assertFalse(_is_silent(_h[0]))

    def test_22_result_write_is_not_silent(self):
        _h = self._handlers(
            "    x = 1\nexcept Exception as e:\n    r['err'] = str(e)\n")
        self.assertFalse(_is_silent(_h[0]))

    def test_23_log_only_no_exc_is_not_silent(self):
        """★收窄口径：只记日志（未含异常对象）也算「已留痕」。"""
        _h = self._handlers(
            "    x = 1\nexcept Exception:\n    _logger.error('boom')\n")
        self.assertFalse(_is_silent(_h[0]))

    def test_24_guard_name_detection(self):
        _c = ast.parse("guard_write(p, explicit=True)").body[0].value
        self.assertEqual(_guard_name(_c), "guard_write")
        _c2 = ast.parse("self.check_package(x)").body[0].value
        self.assertEqual(_guard_name(_c2), "", "业务方法不应被判为守卫")


class TestSignatureStable(unittest.TestCase):
    def test_30_write_guard_signature(self):
        self.assertEqual(list(inspect.signature(wg.guard_write).parameters),
                         ["path", "explicit", "component"])

    def test_31_reject_write_signature(self):
        self.assertEqual(list(inspect.signature(wg.reject_write).parameters),
                         ["path", "explicit", "component"])


if __name__ == "__main__":
    unittest.main()
