# -*- coding: utf-8 -*-
"""第436批刀1 单测：(mtime,size) 结果缓存 + 文件行缓存。

守四件事：
  ① `_m436_memo`：同文件 mtime/size 未变 ⇒ compute **只调一次**（第二轮零解析）；
  ② 文件改动（mtime 变化）⇒ 缓存失效、重新 compute；
  ③ `_m436_src_lines`：同文件只整读一次，改动后失效；
  ④ stat 失败 ⇒ 不缓存（直接算，不猜）。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.self_inspector as SI


def _new_si():
    """不经 __init__ 的裸实例（只测缓存逻辑，不起器官总线）。"""
    _si = object.__new__(SI.SelfInspector)
    _si._m436_result_cache = {}
    _si._m436_tree_cache = {}
    _si._m436_tree_cache_max = 0
    return _si


def test_memo_hits_on_unchanged_file(tmp_path):
    _si = _new_si()
    _p = tmp_path / "a.py"
    _p.write_text("x = 1\n", encoding="utf-8")
    _calls = []

    def _compute():
        _calls.append(1)
        return "result-A"

    _r1 = _si._m436_memo("det", str(_p), _compute)
    _r2 = _si._m436_memo("det", str(_p), _compute)
    assert _r1 == _r2 == "result-A"
    assert len(_calls) == 1, f"compute 被调 {len(_calls)} 次（应 1 次，第二轮应命中缓存）"


def test_memo_invalidated_on_mtime_change(tmp_path):
    _si = _new_si()
    _p = tmp_path / "b.py"
    _p.write_text("x = 1\n", encoding="utf-8")
    _calls = []
    _vals = iter(["v1", "v2"])

    def _compute():
        _calls.append(1)
        return next(_vals)

    _r1 = _si._m436_memo("det", str(_p), _compute)
    # 改文件（保证 mtime 变化）
    time.sleep(0.01)
    _p.write_text("x = 22\n", encoding="utf-8")
    _r2 = _si._m436_memo("det", str(_p), _compute)
    assert _r1 == "v1" and _r2 == "v2", "文件改了缓存必须失效"
    assert len(_calls) == 2


def test_memo_stat_fail_no_cache(tmp_path):
    _si = _new_si()
    _calls = []

    def _compute():
        _calls.append(1)
        return "v"

    _missing = str(tmp_path / "not_exist.py")
    _si._m436_memo("det", _missing, _compute)
    _si._m436_memo("det", _missing, _compute)
    assert len(_calls) == 2, "stat 失败不应缓存（每次都要算）"
    assert len(_si._m436_result_cache) == 0, "stat 失败不得写入缓存"


def test_src_lines_cached_and_invalidated(tmp_path):
    SI._M436_SRC_LINES_CACHE.clear()
    _p = tmp_path / "c.py"
    _p.write_text("l1\nl2\n", encoding="utf-8")
    _a = SI._m436_src_lines(str(_p))
    assert _a == ["l1", "l2"]
    _b = SI._m436_src_lines(str(_p))
    assert _b is _a, "同文件应返回同一个缓存对象（未重复整读）"
    time.sleep(0.01)
    _p.write_text("l1\nl2\nl3\n", encoding="utf-8")
    _c = SI._m436_src_lines(str(_p))
    assert _c == ["l1", "l2", "l3"], "文件改了行缓存必须失效"
    SI._M436_SRC_LINES_CACHE.clear()


def test_src_lines_missing_returns_none(tmp_path):
    assert SI._m436_src_lines(str(tmp_path / "nope.py")) is None
