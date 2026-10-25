# -*- coding: utf-8 -*-
"""367 刀3：逐例归族器（`tools/ci/m367_n5_percase.py`）。

守的契约：
  * **五类互斥完备**：任一 (out, rc) 只落一类，且 `healed` 必须在最前 ——
    实测 135 条里有 30 条同文件跑红、**单跑却绿**，若 rc==0 被落进 `unknown`，
    归族表会凭空多出 30 条"待人工"（本次施工真实踩过，见 test_02）；
  * `unknown` 只留给「rc≠0 且无 E 行」；
  * 基线 `--compare` 只报告增量，**不阻断** CI。
"""
import importlib.util
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    p = os.path.join(_ROOT, "tools", "ci", "m367_n5_percase.py")
    spec = importlib.util.spec_from_file_location("m367_n5_percase", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()

_E_LINE = "E   AssertionError: 1 != 2\n"
_ENV_LINE = "E   ConnectionRefusedError: [Errno 111] Connection refused\n"
_SELF_LINE = "E   AttributeError: 'NoneType' object has no attribute 'x'\n"


class TestClassify:
    def test_01_five_families_exclusive(self):
        """五类各给自己的样本，且**任一输入只有一个答案**（互斥完备）。"""
        cases = [
            ("", 0, "healed"),
            (_ENV_LINE, 1, "env_dependency"),
            (_SELF_LINE, 1, "test_self"),
            (_E_LINE, 1, "pre_existing"),
            ("no e line here\n", 1, "unknown"),
        ]
        for _out, _rc, _want in cases:
            got = M.classify(_out, _rc)
            assert got == _want, "out=%r rc=%s got=%s want=%s" % (_out[:30], _rc, got, _want)
            assert got in M.FAMILIES, got
        assert len(set(M.FAMILIES)) == 5

    def test_02_rc_zero_is_healed_not_unknown(self):
        """★锁住施工中踩的 bug：rc==0 必须判 healed，哪怕输出里没有任何 E 行。"""
        for _out in ("", "Running 1 items in this shard | .", "1 passed in 0.12s"):
            assert M.classify(_out, 0) == "healed", _out

    def test_03_unknown_only_when_red_without_e(self):
        """`unknown` 只在 rc≠0 且无 E 行/无 FAILED 行时出现。"""
        assert M.classify("garbage\n", 1) == "unknown"
        assert M.classify("FAILED tests/x.py::T::t - boom\n", 1) == "pre_existing"
        assert M.classify("", -9) == "unknown", "超时（rc=-9）无输出也应落 unknown"

    def test_04_env_wins_over_assert(self):
        """同一输出里既有环境错误又有断言 ⇒ 归 env（外部原因优先，避免误当代码错）。"""
        _out = _ENV_LINE + _E_LINE
        assert M.classify(_out, 1) == "env_dependency"


class TestRealData:
    def test_05_recorded_unknown_are_all_rc_zero(self):
        """可复现性：本刀实测的 30 条 `unknown` **全部 rc==0**（故已归正为 healed）。"""
        p = os.path.join(_ROOT, "logs", "full_regression", "n5_percase", "percase.json")
        if not os.path.isfile(p):
            pytest.skip("实测产物不在（logs/ 不入库）")
        d = json.load(io.open(p, encoding="utf-8"))
        _un = [r for r in d["rows"] if r.get("family") == "unknown"]
        for r in _un:
            assert r["rc"] == 0, "出现非 0 的 unknown：%s rc=%s" % (r["case"], r["rc"])
        _healed = [r for r in d["rows"] if r["rc"] == 0]
        assert len(_healed) >= 1


class TestCompareNotBlocking:
    def test_06_compare_reports_only(self):
        """基线 `--compare` 只报告 new_red/fixed/same_n，**不阻断**（rc 恒 0）。"""
        _p = os.path.join(_ROOT, "tools", "ci", "m365_full_regression_runner.py")
        _src = io.open(_p, encoding="utf-8").read()
        assert "--compare" in _src
        assert "baseline_diff.json" in _src
        # 不得因比对结果异常而 sys.exit(1)
        _seg = _src[_src.index("def main"):]
        for _bad in ("sys.exit(1)", "raise SystemExit(1)"):
            assert _bad not in _seg, "main 里出现阻断式退出：%s" % _bad


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
