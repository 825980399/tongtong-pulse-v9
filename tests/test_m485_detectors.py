# -*- coding: utf-8 -*-
"""485刀6（P1）：补 3 个检测器 —— bare_return_none_in_except / Traceback / sql_injection。

★守任务书（§485-6）：此前这三类 ``issue_type`` 无检测器 ⇒ 复现恒 ``not_applicable``
⇒ 可判定率静默下降（``detector_coverage`` 暴露但不修）。本刀补齐：
  * bare_return_none_in_except：except 块里裸 ``return`` / ``return None``（AST 口径）；
  * Traceback：日志型 traceback（print_exc / format_exc / 字面 Traceback 头）——
    文本口径（任务书：误报率高，只作复现统计不作拒审依据）；
  * sql_injection：execute/executemany/executescript 的字符串拼接参数（AST 口径）。
★端到端：三类补丁走 ``active_reprobe`` 必须给出 true_pass（不再是 not_applicable）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution.patch_active_reprobe import (
    V_TRUE_PASS,
    active_reprobe,
    detect_bare_return_none_in_except,
    detect_sql_injection,
    detect_traceback_log,
    detector_for,
)

# ---------------- 1) bare_return_none_in_except：AST 检出 ----------------
def test_bare_return_none_hits():
    code = ("def f():\n"
            "    try:\n        do()\n"
            "    except Exception:\n        return None\n")
    hits = detect_bare_return_none_in_except(code)
    assert len(hits) == 1, hits
    assert hits[0].get("line"), hits


def test_bare_return_clean_no_hit():
    code = ("def f():\n"
            "    try:\n        do()\n"
            "    except Exception as e:\n        raise\n")
    assert detect_bare_return_none_in_except(code) == []


# ---------------- 2) Traceback：文本检出 ----------------
def test_traceback_print_exc_hits():
    code = ("def f():\n"
            "    try:\n        do()\n"
            "    except Exception:\n        traceback.print_exc()\n")
    hits = detect_traceback_log(code)
    assert len(hits) >= 1, hits


def test_traceback_clean_no_hit():
    code = ("def f():\n"
            "    try:\n        do()\n"
            "    except Exception as e:\n        raise RuntimeError(str(e)) from e\n")
    assert detect_traceback_log(code) == []


# ---------------- 3) sql_injection：AST 检出 ----------------
def test_sql_concat_hits():
    code = ("def q(uid):\n"
            "    cur.execute('SELECT * FROM t WHERE id = ' + uid)\n")
    hits = detect_sql_injection(code)
    assert len(hits) == 1, hits


def test_sql_fstring_hits():
    code = ("def q(uid):\n"
            "    cur.execute(f'SELECT * FROM t WHERE id = {uid}')\n")
    hits = detect_sql_injection(code)
    assert len(hits) == 1, hits


def test_sql_parameterized_clean_no_hit():
    code = ("def q(uid):\n"
            "    cur.execute('SELECT * FROM t WHERE id = ?', (uid,))\n")
    assert detect_sql_injection(code) == []


# ---------------- 4) 端到端：复现从 not_applicable 变 true_pass ----------------
def _patch(issue, orig, new):
    return {"issue_type": issue, "original_code": orig, "modified_code": new}


def test_reprobe_bare_return_true_pass():
    r = active_reprobe(_patch(
        "bare_return_none_in_except",
        "def f():\n    try:\n        do()\n    except Exception:\n        return None\n",
        "def f():\n    try:\n        do()\n    except Exception as e:\n        raise\n"))
    assert r.get("reprobe_verdict") == V_TRUE_PASS, r


def test_reprobe_traceback_true_pass():
    r = active_reprobe(_patch(
        "Traceback",
        "def f():\n    try:\n        do()\n    except Exception:\n        traceback.print_exc()\n",
        "def f():\n    try:\n        do()\n    except Exception as e:\n        raise RuntimeError(str(e)) from e\n"))
    assert r.get("reprobe_verdict") == V_TRUE_PASS, r


def test_reprobe_sql_true_pass():
    r = active_reprobe(_patch(
        "sql_injection",
        "def q(uid):\n    cur.execute('SELECT * FROM t WHERE id = ' + uid)\n",
        "def q(uid):\n    cur.execute('SELECT * FROM t WHERE id = ?', (uid,))\n"))
    assert r.get("reprobe_verdict") == V_TRUE_PASS, r


# ---------------- 5) 登记完整性：detector_for 不再返回 None ----------------
def test_detector_registered():
    for _k in ("bare_return_none_in_except", "Traceback", "sql_injection"):
        assert detector_for(_k) is not None, _k
