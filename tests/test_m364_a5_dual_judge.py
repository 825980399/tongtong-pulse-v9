# -*- coding: utf-8 -*-
"""364 刀 A5 前置：新旧判据对比工具定向单测（只守不变量，不锁易变总数）。

覆盖：
  1. v2 判据是旧判据的超集（removed 恒为 0，无回归漏报）
  2. continue / break 形态被 v2 识别、旧判据漏掉（★A5 要补的形态）
  3. v2 identity 为六元组且含 tname/kind；可比键四元组与旧 identity 同形
  4. kind 分类正确（pass/ret/assign/cont/break），混合 return+continue 不判静默
  5. nth 计数口径与旧判据一致（只对「无上报」handler 计数）
  6. 工具自身不含静默 handler（CI 门禁自举不变量）
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TOOL = os.path.join(ROOT, "tools", "scan_silent_except_dual.py")


def _load():
    spec = importlib.util.spec_from_file_location("m364_a5_dual", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()


def _v2(src, path="x.py"):
    return M.extract_handlers_v2(src, path)


def _old(src, path="x.py"):
    from ci_common import extract_handlers, handler_identity
    sys.path.insert(0, os.path.join(ROOT, "tools", "ci"))
    return set(handler_identity(x) for x in extract_handlers(src, path))


def test_v2_is_superset_of_legacy():
    """新判据不得丢掉旧判据的任何命中（removed 恒 0）。"""
    src = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except ImportError:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        return None\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        continue\n"
    )
    old = _old(src)
    new_keys = set(M._cmp_key(x) for x in _v2(src))
    assert old, "旧判据应有命中（样例自证）"
    assert old <= new_keys, f"新判据漏掉旧命中：{old - new_keys}"


def test_cont_and_break_newly_detected():
    src = (
        "def f():\n"
        "    for i in range(3):\n"
        "        try:\n"
        "            g(i)\n"
        "        except Exception:\n"
        "            continue\n"
        "        try:\n"
        "            g(i)\n"
        "        except Exception:\n"
        "            break\n"
    )
    old = _old(src)
    assert old == set(), "旧判据抓不到纯 continue/break（这正是 A5 要补的形态）"
    kinds = sorted(x["kind"] for x in _v2(src))
    assert kinds == ["break", "cont"]


def test_identity_shape_and_cmp_key():
    src = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        continue\n"
    )
    fp = _v2(src)[0]
    ident = M._v2_identity(fp)
    assert len(ident) == 6
    assert ident[3] == "ValueError" and ident[4] == "cont"
    assert len(M._cmp_key(fp)) == 4  # path/qualname/nth/sha16，与旧 identity 同形


def test_kind_classification():
    def kind_of(body, exc="Exception"):
        src = f"def f():\n    try:\n        pass\n    except {exc}:\n        {body}\n"
        hs = _v2(src)
        return hs[0]["kind"] if hs else None

    assert kind_of("pass") == "pass"
    assert kind_of("return None") == "ret"
    assert kind_of("x = 1") == "assign"
    assert kind_of("continue") == "cont"
    assert kind_of("break") == "break"
    # 混合形态（return + continue）不判静默 —— 新判据也不臆断
    assert kind_of("return None\n        continue") is None


def test_nth_counts_only_non_reporting_handlers():
    """nth 只统计「无上报」handler —— 必须与旧判据完全一致，否则 diff 全是噪声。"""
    src = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        logging.warning('x')\n"   # 有上报 ⇒ 不计数
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        continue\n"                # nth = 0
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        break\n"                   # nth = 1
    )
    hs = _v2(src)
    assert [(x["kind"], x["nth"]) for x in hs] == [("cont", 0), ("break", 1)]


def test_tool_file_has_no_silent_handler():
    """CI 门禁自举：本工具自身不得含静默 except（否则它自己会被门禁拦）。"""
    sys.path.insert(0, os.path.join(ROOT, "tools", "ci"))
    from ci_common import extract_handlers
    with open(TOOL, encoding="utf-8") as f:
        src = f.read()
    bad = extract_handlers(src, "tools/scan_silent_except_dual.py")
    assert bad == [], f"工具自身含静默 handler：{bad}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
