# -*- coding: utf-8 -*-
"""374 刀6 单测：嵌套函数内外层重复计数（D2）。

票 `T-370-嵌套函数重复计数-1`（P2 · 374 批 C 组判据改造）。

【票面】「Cortex 2 行变 4 条」——嵌套函数被当成独立方法再扫一遍，而外层方法的
方法体本来就包含整段嵌套定义 ⇒ 同一物理行被算两次。

【实测（本刀开工 T0）】当代语料里 `organs/` 全部 25 条报出条目中，同一
`(file, 行号, 类型)` **0 组重复**——票面的具体数字复现不出（与 374 刀4/刀5 同型：
前提已随时点漂移）。但机制本身**可复现**：合成「外层方法 + 内层 print」样本
（2 个物理 print 行），改前报 **3 条**。故本刀按「机制修」而非「凑票面数字」。

【改法两层】
① **根因**：跳过嵌套函数（外层 body 已覆盖它）；
② **兜底键**：`_run_detectors` 出口按 `(file, 行号, 类型, 签名)` 去重，签名取
   描述文本、**不含 method**——外层名与内层名正是重复计数唯一的区别。

【★一处必须说清的前提】`_parse_methods` 的行号系统性偏 1~2（`^\\s*` 会吞掉 def
前的空行，`_read_method_body` 切片基准又比 1-based 偏一位）⇒ **单靠行号键根本
去不掉内外层重复**（同一物理行在内层视角与外层视角得到不同 `line`）。所以根因
修（跳过嵌套）是主手段，键只是兜底；行号修正属另一张票。
"""
import os

import pytest

import nucleus.self_inspector as SI

_NESTED = '''class PulseX:
    def outer(self):
        print("debug-outer")

        def inner():
            print("debug-inner")

        return inner
'''


def _inst():
    return SI.SelfInspector()


def _mk(tmp_path, name, src):
    _p = tmp_path / name
    _p.write_text(src, encoding="utf-8")
    return str(_p)


def _scan(path):
    _o = _inst()
    _ms = _o._parse_methods(path)
    return _o._run_detectors({os.path.basename(path)[:-3]: {
        "file_path": path, "methods": _ms}})


def _prints(issues):
    return [i for i in issues if i.get("type") == "print_debug"]


# ------------------------------------------------------- ① 根因：跳过嵌套
def test_01_nested_not_scanned_twice(tmp_path):
    """2 个物理 print 行 ⇒ 必须恰好 2 条（改前 3 条）。"""
    _p = _mk(tmp_path, "PulseX.py", _NESTED)
    _iss = _prints(_scan(_p))
    assert len(_iss) == 2, [(_i["line"], _i["method"]) for _i in _iss]


def test_02_inner_only_issue_not_lost(tmp_path):
    """★不漏的正控：内层独有的问题仍要被报（只是归到外层名下）。"""
    _p = _mk(tmp_path, "PulseX.py", _NESTED)
    _iss = _prints(_scan(_p))
    assert len({_i["line"] for _i in _iss}) == 2, "两个物理行必须各报一次，不能少"
    # 内层那条现在挂外层名下（外层 body 覆盖它）——这正是「不重复但仍可见」
    assert {_i["method"] for _i in _iss} == {"outer"}


def test_03_three_levels_counted_once(tmp_path):
    _src = ('class PulseY:\n'
            '    def a(self):\n'
            '        print("p1")\n'
            '\n'
            '        def b():\n'
            '            print("p2")\n'
            '\n'
            '            def c():\n'
            '                print("p3")\n'
            '\n'
            '            return c\n'
            '\n'
            '        return b\n')
    _p = _mk(tmp_path, "PulseY.py", _src)
    _iss = _prints(_scan(_p))
    assert len(_iss) == 3, [(_i["line"], _i["method"]) for _i in _iss]


def test_04_class_methods_never_skipped(tmp_path):
    """类里的普通方法**不是嵌套**，不许被跳过（防"跳过"波及正常方法）。"""
    _src = ('class PulseZ:\n'
            '    def m1(self):\n'
            '        print("a")\n'
            '\n'
            '    def m2(self):\n'
            '        print("b")\n')
    _p = _mk(tmp_path, "PulseZ.py", _src)
    _iss = _prints(_scan(_p))
    assert len(_iss) == 2
    assert {_i["method"] for _i in _iss} == {"m1", "m2"}


# ------------------------------------------------- ② 兜底键（不含 method）
def test_05_duplicate_method_entries_collapse(tmp_path):
    """同一方法被登记两次 ⇒ 出口去重后只剩一份（且键**不含 method**）。"""
    _p = _mk(tmp_path, "PulseZ.py",
             'class PulseZ:\n    def m1(self):\n        print("a")\n')
    _o = _inst()
    _ms = _o._parse_methods(_p)
    _once = _o._run_detectors({"PulseZ": {"file_path": _p, "methods": _ms}})
    _twice = _o._run_detectors({"PulseZ": {"file_path": _p,
                                           "methods": _ms + _ms}})
    assert len(_twice) == len(_once), (len(_once), len(_twice))
    assert len(_once) >= 1


def test_06_scan_all_organs_entry_unchanged(tmp_path):
    """去重只发生在检测出口，不改 `_parse_methods` 的枚举语义（被测对象不动）。"""
    _p = _mk(tmp_path, "PulseX.py", _NESTED)
    _names = [m["name"] for m in _inst()._parse_methods(_p)]
    assert _names == ["outer", "inner"], _names


# ---------------------------------------------------------- ③ 健壮与形态
def test_07_broken_file_no_crash(tmp_path):
    _p = _mk(tmp_path, "Bad.py", "def (:\n")
    assert SI._m374_nested_func_lines(_p) == frozenset()
    assert SI._m374_is_nested_start(frozenset(), 10) is False


def test_08_real_organs_no_duplicate_line():
    """真语料**形态**断言：同一 `(file, 行号, 类型)` 不许出现多组。

    不断言「必须几条」——绝对条数会随语料漂移（G5 同型教训）。
    """
    _o = _inst()
    _seen = {}
    _dup = []
    for _d, _sub, _fs in os.walk("organs"):
        _sub[:] = [x for x in _sub if x != "__pycache__"]
        for _fn in sorted(_fs):
            if not _fn.endswith(".py"):
                continue
            _p = os.path.join(_d, _fn).replace("\\", "/")
            _ms = _o._parse_methods(_p)
            for _i in _o._run_detectors({_fn[:-3]: {"file_path": _p,
                                                   "methods": _ms}}):
                _k = (_i.get("file"), int(_i.get("line", 0) or 0), _i.get("type"))
                if _k in _seen:
                    _dup.append(_k)
                _seen[_k] = True
    assert not _dup, f"同一物理行被计多次：{_dup[:5]}"


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    SI._m374_nested_func_lines.cache_clear()
