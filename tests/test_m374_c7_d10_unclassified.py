# -*- coding: utf-8 -*-
"""374 刀7 单测：D10 扫描器「判 None 的 handler 不许静默消失」。

票 `T-370-扫描器kind_of形态误判-1`（P2 · 374 批 C 组判据改造）。

【票面】「扫描器 `_kind_of` 按 `body[0]` 判形态，留痕后判 None 致条目静默消失」。

【实测（T0）】`_kind_of` 只认 pass/ret/assign/cont/break 五种**整体同形**；handler
体里多一句无法归类的语句（典型：先留痕/加点数，末尾 `pass`/`continue`/`break`/
`return`）就整条判 None ⇒ 既不进静默清单、也不进任何报表。
生产面（`config.py`/`main.py`/`organs/`/`nucleus/`）实测 **262 条**这样消失，
其中末句吞控制流 **78 条**。

【本刀做法：只做可见化，不动门禁尺子】
把这些 handler 单独成桶输出（带末句形态 / `tail_swallow`）。**不**把它们并进
静默清单——`ci_common.extract_handlers` 是 cw2 门禁的尺子，加宽等于抬高门禁基线，
属「改尺子」，须另报裁决单独立项（任务书：改检测器/门禁尺子前必须另报裁决）。
本刀交付的是**可裁数据**：条数、末句分布、样例。

验五件事：
① 留痕式吞异常（`x += 1; pass`）从「完全消失」变成**桶里可见**；
② 桶与静默清单**互斥**（同一 handler 不许两边都算，也不许两边都不算）；
③ 有上报/重抛的 handler 不入桶（不重复计）；
④ 已被正确识别的五形态不入桶；
⑤ 真语料形态断言（不拿绝对条数当判据）+ 接线不是死代码。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scan_silent_except_dual as dual

_SWALLOW_WITH_TRACE = '''def f(self):
    try:
        go()
    except Exception:
        self._n += 1
        pass
'''

_PLAIN_PASS = '''def g(self):
    try:
        go()
    except Exception:
        pass
'''

_REPORTED = '''def h(self):
    try:
        go()
    except Exception as e:
        silent_exc(e, where="x")
'''


def _keys(entries, with_last=False):
    if with_last:
        return {(e["path"], e["lineno"], e["last"]) for e in entries}
    return {(e["path"], e["lineno"]) for e in entries}


# ------------------------------------------------- ① 从"消失"到"可见"
def test_01_trace_then_pass_is_visible():
    _u = dual.extract_unclassified(_SWALLOW_WITH_TRACE, "x.py")
    assert len(_u) == 1, _u
    _e = _u[0]
    assert _e["tname"] == "Exception"
    assert _e["stmts"] == 2 and _e["last"] == "Pass"
    assert _e["tail_swallow"] is True
    assert _e["qualname"] == "f"


def test_02_it_used_to_vanish():
    """钉住旧行为：这类 handler 在 v2 静默清单里**一条都没有**。"""
    assert dual.extract_handlers_v2(_SWALLOW_WITH_TRACE, "x.py") == []
    assert len(dual.extract_unclassified(_SWALLOW_WITH_TRACE, "x.py")) == 1


# ------------------------------------------------------- ② 两桶互斥
def test_03_buckets_are_disjoint_and_total():
    """同一段源码里：静默清单 ∪ unclassified = 去掉"有上报"后的全部 handler。"""
    _src = (_PLAIN_PASS + "\n" + _SWALLOW_WITH_TRACE + "\n" + _REPORTED
            + "\ndef k(self):\n    try:\n        go()\n    except ValueError:\n"
              "        return None\n")
    _silent = _keys(dual.extract_handlers_v2(_src, "y.py"))
    _uncl = _keys(dual.extract_unclassified(_src, "y.py"))
    assert not (_silent & _uncl), f"两桶重叠：{_silent & _uncl}"
    # pass / 留痕+pass / return None 三类都在，且 REPORTER 不在任何一桶
    assert len(_silent) == 2, _silent
    assert len(_uncl) == 1, _uncl


def test_04_reported_handler_not_in_bucket():
    assert dual.extract_unclassified(_REPORTED, "z.py") == []


def test_05_recognized_kinds_not_in_bucket():
    """五形态（pass/ret/assign/cont/break）都已被 v2 认走，不许再进桶。"""
    _src = ('def a():\n    try:\n        go()\n    except Exception:\n        pass\n'
            'def b():\n    try:\n        go()\n    except Exception:\n        return 1\n'
            'def c():\n    try:\n        go()\n    except Exception:\n        x = 1\n'
            'def d():\n    for i in r:\n        try:\n            go()\n'
            '        except Exception:\n            continue\n'
            'def e():\n    while 1:\n        try:\n            go()\n'
            '        except Exception:\n            break\n')
    assert dual.extract_unclassified(_src, "w.py") == []
    assert len(dual.extract_handlers_v2(_src, "w.py")) == 5


# ------------------------------------- ③ 真语料形态 + 接线不是死代码
def test_06_real_prod_face_buckets_disjoint():
    """真文件上两桶仍互斥（不判绝对条数，只判形态）。"""
    _bad = []
    _n = 0
    for _rel in ("config.py", "main.py", "nucleus/audit_problem_pool.py",
                 "nucleus/ContextFreshener.py"):
        _fp = os.path.join(_ROOT, _rel)
        if not os.path.isfile(_fp):
            continue
        with open(_fp, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        try:
            _s = _keys(dual.extract_handlers_v2(_src, _rel))
            _u = _keys(dual.extract_unclassified(_src, _rel))
        except SyntaxError as _e:
            pytest.skip(f"{_rel} 解析失败（判据无从判定，不静默吞）：{_e}")
        _overlap = _s & _u
        if _overlap:
            _bad.append((_rel, sorted(_overlap)[:3]))
        _n += len(_u)
    assert not _bad, f"两桶重叠：{_bad}"
    assert _n >= 1, "真语料里一条都没抓到？判据可能失效"


def test_07_scan_wires_bucket():
    """`scan()` 必须真的把桶填进 `LAST_UNCLASSIFIED`（否则报表永远 0）。"""
    with open(dual.__file__, encoding="utf-8") as _f:
        _src = _f.read()
    assert "_uncl.extend(extract_unclassified" in _src
    assert "LAST_UNCLASSIFIED[:] = _uncl" in _src
    assert "unclassified_tail_swallow" in _src
    assert isinstance(dual.LAST_UNCLASSIFIED, list)


@pytest.fixture(autouse=True)
def _no_state_leak():
    _bak = list(dual.LAST_UNCLASSIFIED)
    yield
    dual.LAST_UNCLASSIFIED[:] = _bak
