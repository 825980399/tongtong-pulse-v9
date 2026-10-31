# -*- coding: utf-8 -*-
"""408 刀3 判据：L4 停机判 SKIP（与 L5/L6 同一个 framework_down 判据）。

为什么改
--------
406 批实测的自相矛盾：**同一个停机事实**，L5/L6 判 SKIP（"停机时本层不判"），
L4 判 FAIL（"当天没有 causal_chains 台账"）。后果不是"多一条红"，
而是**整体永远 FAIL** ⇒ 每次提交都带一条注定红 ⇒「FAIL 有信息量」失效。

★三态纪律（本刀最容易做错的地方）：`_framework_down()` 必须能区分
  「确定停机」与「判不了」。判不了退回 FAIL，不许当停机用 ——
  406 批 L5 假阳性正是把"判不了"当"没在跑"造成的。

A 停机 ⇒ SKIP（两种缺数据形态都要覆盖）
B 在跑 ⇒ 仍然 FAIL（本刀不许把 FAIL 全改成 SKIP）
C 判不了 ⇒ FAIL（不许拿"没判"当挡箭牌）
D 不变量：day_too_young 优先 / 有台账仍 PASS / 输出含 skip_reason
E 源码结构：三处分支都在，且都没有走到 `_bad` 之前漏掉 `_down`
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import m404_layer_health_check as mh                 # noqa: E402

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "m404_layer_health_check.py")


def _skip_reasons(fn):
    """取一个函数里所有 ``_skip(...)`` 的第 3 个参数（skip_reason 字面量）。

    ★不用 ``ast.unparse(...).count('"framework_down"')``：unparse 把字符串
      渲染成**单引号**，数双引号恒为 0 —— 408 首跑就是这么红的。
    """
    _out = []
    for _n in ast.walk(fn):
        if not (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
                and _n.func.id == "_skip"):
            continue
        if len(_n.args) >= 3 and isinstance(_n.args[2], ast.Constant):
            _out.append(_n.args[2].value)
    return _out


def _ts(hour=15):
    """构造一个本地时间为今天 ``hour`` 点的时间戳（控制 tm_hour）。"""
    _n = time.localtime()
    return time.mktime((_n.tm_year, _n.tm_mon, _n.tm_mday, hour, 0, 0, 0, 0, -1))


class _Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = self._d.name
        os.makedirs(os.path.join(self.root, "data", "monitor"), exist_ok=True)
        self.ctx = {"root": self.root, "now": _ts(15)}
        self._orig = mh._probe_framework_running

    def tearDown(self):
        mh._probe_framework_running = self._orig
        self._d.cleanup()

    def _set_running(self, val):
        """val: True=在跑 / False=停机 / None=判不了。"""
        mh._probe_framework_running = lambda *a, **k: val

    def _l4_path(self):
        return os.path.join(self.root, "data", "monitor",
                            "causal_chains_%s.jsonl" % mh._today_str(self.ctx["now"]))

    def _write_rows(self, rows):
        with open(self._l4_path(), "w", encoding="utf-8") as f:
            for _r in rows:
                f.write(json.dumps(_r, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ A 停机
class TestA_DownSkips(_Base):
    def test_10_no_file_skips(self):
        self._set_running(False)
        _r = mh.check_l4(self.ctx)
        self.assertEqual(mh.SKIP, _r["status"], _r)
        self.assertEqual("framework_down", _r["reason"])
        self.assertIs(False, _r["evidence"]["framework_running"])
        self.assertIn("停机", _r["detail"])

    def test_11_file_without_summary_skips(self):
        self._set_running(False)
        self._write_rows([{"kind": "chain", "a": "x"}])       # 有文件无小时汇总
        _r = mh.check_l4(self.ctx)
        self.assertEqual(mh.SKIP, _r["status"], _r)
        self.assertEqual("framework_down", _r["reason"])

    def test_12_skip_reason_matches_l5_l6(self):
        """★三层必须是**同一个字面量** framework_down（否则读数又对不上）。

        ★为什么不在测试里真跑 L5/L6：它们要走到 framework_down 分支，
          得先造一份**超龄的健康快照/小时快照**，夹具成本远高于收益，
          而且会把本刀的判据绑到 L5/L6 的内部流程上。这里直接查源码：
          「三层用的 skip_reason 是同一个字符串」这件事本身就能结构性证明。
        """
        _tree = ast.parse(open(_SRC, "rb").read().decode("utf-8"))
        _cnt = {}
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.FunctionDef) and _n.name in (
                    "check_l4", "check_l5", "check_l6"):
                _cnt[_n.name] = _skip_reasons(_n).count("framework_down")
        self.assertGreaterEqual(_cnt.get("check_l4", 0), 2, _cnt)
        self.assertGreaterEqual(_cnt.get("check_l5", 0), 1, _cnt)
        self.assertGreaterEqual(_cnt.get("check_l6", 0), 1, _cnt)


# ------------------------------------------------------------------ B 在跑
class TestB_RunningStillFails(_Base):
    def test_20_no_file_fails(self):
        self._set_running(True)
        _r = mh.check_l4(self.ctx)
        self.assertEqual(mh.FAIL, _r["status"], _r)
        self.assertIn("没有 causal_chains", _r["detail"])
        self.assertIs(True, _r["evidence"]["framework_running"])

    def test_21_file_without_summary_fails(self):
        self._set_running(True)
        self._write_rows([{"kind": "chain"}])
        _r = mh.check_l4(self.ctx)
        self.assertEqual(mh.FAIL, _r["status"], _r)
        self.assertIn("hourly_summary", _r["detail"])


# ------------------------------------------------------------------ C 判不了
class TestC_UnknownStaysFail(_Base):
    def test_30_unknown_fails(self):
        """★判不了不许当停机 —— 这是 406 批 L5 假阳性的反面。"""
        self._set_running(None)
        _r = mh.check_l4(self.ctx)
        self.assertEqual(mh.FAIL, _r["status"], _r)
        self.assertIsNone(_r["evidence"]["framework_running"])


# ------------------------------------------------------------------ D 不变量
class TestD_Invariants(_Base):
    def test_40_day_too_young_wins(self):
        """首个小时还没过完 → day_too_young 优先（比 framework_down 更具体）。"""
        self._set_running(False)
        _r = mh.check_l4({"root": self.root, "now": _ts(0)})
        self.assertEqual(mh.SKIP, _r["status"])
        self.assertEqual("day_too_young", _r["reason"])

    def test_41_rows_still_pass_when_down(self):
        """停机但当天台账有小时汇总 → 仍按原逻辑 PASS（不许因为停机就一律 SKIP）。"""
        self._set_running(False)
        self._write_rows([
            {"kind": "hourly_summary", "hour": "2026-01-01T10", "complete": True},
            {"kind": "hourly_summary", "hour": "2026-01-01T11", "complete": True},
        ])
        _r = mh.check_l4(self.ctx)
        self.assertEqual(mh.PASS, _r["status"], _r)

    def test_42_detail_mentions_stop(self):
        self._set_running(False)
        _r = mh.check_l4(self.ctx)
        self.assertTrue(any(w in _r["detail"] for w in ("停机", "不判")), _r["detail"])


# ------------------------------------------------------------------ E 源码结构
class TestE_SourceShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(open(_SRC, "rb").read().decode("utf-8"))
        cls.fn = None
        for _n in ast.walk(cls.tree):
            if isinstance(_n, ast.FunctionDef) and _n.name == "check_l4":
                cls.fn = _n
        assert cls.fn is not None

    def test_50_helper_is_tristate(self):
        _fn = [n for n in ast.walk(self.tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_framework_down"]
        self.assertEqual(1, len(_fn), "_framework_down 不存在或重复")
        _src = ast.unparse(_fn[0])
        self.assertIn("None", _src, "三态丢了（必须能区分'判不了'）")

    def test_51_both_branches_have_down_skip(self):
        """两处缺数据分支都必须先问 ``_down`` 再 ``_bad``（顺序错了就白改）。"""
        _n = _skip_reasons(self.fn).count("framework_down")
        self.assertEqual(2, _n, "framework_down 的 skip 不是两处：%d" % _n)
        # `if _down:` 必须出现在对应 `_bad` 之前
        _src = ast.unparse(self.fn)
        _i_down = _src.index("if _down:")
        _i_bad = _src.index("_bad('L4'")
        self.assertLess(_i_down, _i_bad, "`if _down:` 排在 `_bad` 后面 = 永远走不到")

    def test_52_no_silent_except_introduced(self):
        for _h in [n for n in ast.walk(self.fn) if isinstance(n, ast.ExceptHandler)]:
            self.assertIn("silent_exc", ast.unparse(_h),
                          "check_l4 里有静默 except：%s" % ast.unparse(_h)[:80])


if __name__ == "__main__":
    unittest.main(verbosity=2)
