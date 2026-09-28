# -*- coding: utf-8 -*-
"""主线第33批 T2（P2-195）策略护栏：禁止「裸方法名」源码计数型断言回潮。

背景：
    `assertEqual(_SRC.count("foo("), N)` 这类断言把**定义处 + docstring 文本**
    一并计入（实测 `_m31_extract_key_terms(` 裸计 15 / 调用形态 `self.` 仅 12），
    导致补 docstring、加注释等**无关改动**即假失败（第32批 P2/P5 各撞 1~2 例）。

本护栏**只拦截真实脆弱形态**：
    · 断言必须是 `assertEqual`（`assertGreaterEqual` 已属稳健，放行）；
    · 被计数的字面量必须**以裸标识符紧跟 `(`** 开头（如 `"foo("`）——
      `self.foo(` / `liver.memory.write` / `[WARNING]` / `silent_except`
      等形态天然不匹配，因此**运行期数据计数不受影响**。
"""
import os
import re
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.join(_PROJ, "tests")

# assertEqual(<expr>.count("<裸标识符>（" ...), N)
_FRAGILE = re.compile(
    r'assertEqual\s*\(\s*[A-Za-z_][\w\.\[\]"\']*\.count\s*\(\s*'
    r'(["\'])([A-Za-z_]\w*\()')

#: 允许清单：文件相对名 → 允许的理由（必须写明，禁止无理由豁免）
_ALLOW = {
    # 护栏自身：正文与自检用例**必须**包含脆弱样例，否则无法证明判别力
    "test_assertion_robustness_m33.py": "本护栏文件：含脆弱/稳健样例用于自检",
}


class TestNoFragileSourceCount(unittest.TestCase):
    def test_01_no_bare_name_count_in_tests(self):
        """★tests/ 内不得再出现 `assertEqual(...count("裸名("...), N)`。"""
        _bad = []
        for _f in sorted(os.listdir(_TESTS)):
            if not _f.endswith(".py"):
                continue
            if _f in _ALLOW:
                continue
            _p = os.path.join(_TESTS, _f)
            _t = open(_p, encoding="utf-8").read()
            for _i, _l in enumerate(_t.split("\n"), 1):
                if _FRAGILE.search(_l):
                    _bad.append(f"{_f}:{_i}: {_l.strip()[:110]}")
        self.assertEqual(
            _bad, [],
            "发现脆弱的裸方法名计数断言（应改为 assertGreaterEqual 或计数调用形态"
            " `self.xxx(`）：\n  " + "\n  ".join(_bad))

    def test_02_guard_self_check(self):
        """护栏自身的判别力自检：脆弱样例应命中、稳健样例应放过。"""
        _fragile_samples = [
            'self.assertEqual(_SRC.count("foo("), 3)',
            'assertEqual(src.count(\'bar(\'), 2)',
        ]
        _ok_samples = [
            'self.assertEqual(_IW_SRC.count("self._m31_extract_key_terms("), 12)',
            'self.assertGreaterEqual(_SRC.count("foo("), 3)',
            'self.assertEqual(_states.count(CLAIMED), 1)',
            'self.assertEqual(out2.count("[WARNING]"), 1)',
            'self.assertEqual(_names.count("liver.memory.write"), 2)',
            'self.assertEqual(_types.count("silent_except"), 2)',
        ]
        for _s in _fragile_samples:
            self.assertIsNotNone(_FRAGILE.search(_s), f"应命中: {_s}")
        for _s in _ok_samples:
            self.assertIsNone(_FRAGILE.search(_s), f"不应命中: {_s}")


class TestGovernanceMarkers(unittest.TestCase):
    """治理痕迹：被治理处应留下批次标记，便于审计。"""

    def test_10_slice_governance_uses_call_form(self):
        _t = open(os.path.join(_TESTS, "test_slice_governance_m32.py"),
                     encoding="utf-8").read()
        self.assertIn('self._m31_extract_key_terms(', _t,
                      "应计数「调用形态」而非裸方法名")

    def test_11_guard_sites_keep_exact_count_with_reason(self):
        """刻意保留精确计数的地方必须写明原因（禁止无注释的精确计数）。"""
        _t = open(os.path.join(_TESTS, "test_dialog_guard_m15.py"),
                     encoding="utf-8").read()
        _i = _t.index("def test_three_output_sites_guarded")
        _seg = _t[_i:_i + 500]
        self.assertIn("刻意保留精确计数", _seg,
                      "保留 == 必须说明「为何精确」")


if __name__ == "__main__":
    unittest.main(verbosity=2)
