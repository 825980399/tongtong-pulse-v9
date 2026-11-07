# -*- coding: utf-8 -*-
"""473刀19 门控单测：O3「同题候选」判据按 status 分档。

现场（2026-11-07 12:26 ~ 13:01，曈曈 pid=29720）：
  [335刀1][O3消费] 已存在同题候选（id=o3_26fcf0a3c2e49964 status=wont_fix）⇒ 不重复入池
    · 池里那条是 `wont_fix`、`wont_fix_by="human:xinggui"`、
      `wont_fix_reason` 明写「不修，仅清队列」—— 人**已经裁决了**
    · 日志写「已存在同题候选」把「已否决」说成「有人在办」，排障会误读到
      「卡在审批」上，方向全错
    · 列表推导缺 `isinstance` 保护：池被写坏（混入 str/None）时 AttributeError
      会被外层 except 吞掉，整条 O3 通道当轮失效

本刀只改**可观测性**（+ 一行防御），**行为一字不变**：
两种情况都返回 `no_candidate` 且**不写盘** —— 尊重人工裁决，也不重复打扰。

隔离约束：
  · 待修池走 `pending_path=` 注入的临时文件，绝不读写生产 `data/pending_patches.json`；
  · `_m365_o3_review_items` / `_m343_o3_stable_id` / `_m333_intake_rate` /
    `_m335_o3_intake_candidates` / `_m339_intake_whitelisted` 一律打桩
    （不触发 25s 判死池刷新、不读真实裁决）；
  · `gate_report` 显式传入 ⇒ 不进 `refresh_dead_code_review_async` 分支；
  · 时间显式传 `now=`，不 sleep。
"""
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from typing import ClassVar
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.self_inspector as SI
from nucleus._silent_except import silent_exc
from nucleus.evolution import patch_execution_dispatch as PED
from nucleus.evolution.patch_dedup import TERMINAL_STATUSES as REAL_TERMINAL

_DAY0 = 1793600000.0
_TARGET = {"file": "nucleus/dead_target.py", "method": "ghost_method",
           "line": 42, "evidence_tier": "tier_a"}
_GATE = {"official_verdict": "OPEN", "official_reason": "单测注入"}


class _FakeLogger:
    """收集日志而不落盘：断言文案用，不改变任何控制流。"""

    def __init__(self):
        self.records = []

    def _add(self, level, msg, args):
        try:
            text = (str(msg) % args) if args else str(msg)
        except Exception as _e:      # 格式化失败不能影响被测函数
            silent_exc(_e, where="test_m473_knife19::_FakeLogger._add",
                       level="debug")
            text = f"{msg} | {args!r}"
        self.records.append((level, text))

    def info(self, msg, *a, **kw):
        self._add("info", msg, a)

    def warning(self, msg, *a, **kw):
        self._add("warning", msg, a)

    def debug(self, msg, *a, **kw):
        self._add("debug", msg, a)

    def error(self, msg, *a, **kw):
        self._add("error", msg, a)

    def text(self):
        return "\n".join(t for _, t in self.records)


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class _Base(unittest.TestCase):
    """每个用例自带一个临时待修池；所有外部依赖打桩。"""

    ROWS = None          # 子类覆盖：池里已有条目

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m473k19_")
        self._pool = os.path.join(self._tmp, "pending_patches.json")
        with open(self._pool, "w", encoding="utf-8") as fh:
            json.dump(self.ROWS if self.ROWS is not None else [], fh,
                      ensure_ascii=False, indent=2)
        self._saved_last = dict(PED._M335_O3_LAST)
        self._flog = _FakeLogger()
        self._stack = []

        def _p(obj, name, val):
            m = mock.patch.object(obj, name, val)
            m.start()
            self._stack.append(m)

        _p(PED, "_module_logger", self._flog)
        # 入池四件套 + 稳定 id：全部打桩，不碰生产判据
        _p(PED, "_m365_o3_review_items",
           lambda cand, gate: {"probe_out_of_graph": {}, "misjudge_sample": None,
                               "god_file": False})
        _p(PED, "_m343_o3_stable_id", lambda *a, **k: "o3_unit_fixed_id")
        _p(SI, "_m333_intake_rate", lambda root=None, now=None: {"count": 0, "limit": 1})
        _p(SI, "_m335_o3_intake_candidates",
           lambda limit=1: {"candidates": [dict(_TARGET)], "reason": "单测候选"})
        _p(SI, "_m339_intake_whitelisted", lambda f: True)

    def tearDown(self):
        for m in reversed(self._stack):
            m.stop()
        PED._M335_O3_LAST.clear()
        PED._M335_O3_LAST.update(self._saved_last)
        for name in os.listdir(self._tmp):
            try:
                os.remove(os.path.join(self._tmp, name))
            except OSError as _e:
                silent_exc(_e, where="test_m473_knife19::tearDown rm",
                           level="debug")
        try:
            os.rmdir(self._tmp)
        except OSError as _e:
            silent_exc(_e, where="test_m473_knife19::tearDown rmdir",
                       level="debug")

    # ── 公共执行 ──
    def _run(self):
        return PED._m335_o3_consume(now=_DAY0, pending_path=self._pool,
                                    gate_report=dict(_GATE))

    def _pool_rows(self):
        with open(self._pool, encoding="utf-8") as fh:
            return json.load(fh)

    def _lines_with(self, needle):
        return [t for _, t in self._flog.records if needle in t]


class TestTerminalStatusesSource(unittest.TestCase):
    """终态集合必须**只有一个源**，否则必然漂移。"""

    def test_01_equals_patch_dedup_source(self):
        self.assertEqual(tuple(REAL_TERMINAL), PED._m473_terminal_statuses())

    def test_02_fallback_when_import_fails(self):
        # 把 patch_dedup 替换成没有 TERMINAL_STATUSES 的壳 ⇒ from-import 必失败
        with mock.patch.dict(sys.modules,
                             {"nucleus.evolution.patch_dedup":
                              types.SimpleNamespace(no_such_attr=True)}):
            got = PED._m473_terminal_statuses()
        self.assertEqual(("wont_fix", "wontfix", "cancelled", "revoked",
                          "rejected", "rolled_back"), got)

    def test_03_fallback_never_wider_than_real(self):
        # 兜底只允许「少判」；多出一种"在办"状态被当终态 = 静默吞补丁
        fallback = ("wont_fix", "wontfix", "cancelled", "revoked",
                    "rejected", "rolled_back")
        for st in fallback:
            self.assertIn(st, REAL_TERMINAL)

    def test_04_returns_tuple(self):
        # `in` 判断依赖可哈希容器；返回 list 也能用，但契约是 tuple
        self.assertIsInstance(PED._m473_terminal_statuses(), tuple)


class TestAdjudicatedBranch(_Base):
    """终态 ⇒ 明说「已被裁决」，并带上裁决人/原因。"""

    ROWS: ClassVar[list] = [{"id": "o3_26fcf0a3c2e49964", "file": "nucleus/dead_target.py",
             "method": "ghost_method", "status": "wont_fix",
             "wont_fix_by": "human:xinggui",
             "wont_fix_reason": "不修，仅清队列"}]

    def test_05_status_is_no_candidate(self):
        self.assertEqual("no_candidate", self._run()["status"])

    def test_06_reason_names_adjudication(self):
        self.assertEqual("同题已被裁决为 wont_fix", self._run()["reason"])

    def test_07_log_says_adjudicated_not_in_pool(self):
        self._run()
        hit = self._lines_with("同题已被裁决")
        self.assertEqual(1, len(hit), self._flog.text())
        self.assertIn("wont_fix", hit[0])
        # 旧文案不得再出现（它是本刀要治的病）
        self.assertFalse(self._lines_with("已存在同题候选"))

    def test_08_log_carries_reviewer_and_reason(self):
        self._run()
        txt = self._flog.text()
        self.assertIn("human:xinggui", txt)
        self.assertIn("不修，仅清队列", txt)

    def test_09_existing_fields_backfilled(self):
        r = self._run()
        self.assertEqual("o3_26fcf0a3c2e49964", r["existing_id"])
        self.assertEqual("wont_fix", r["existing_status"])

    def test_10_no_write(self):
        before = _sha(self._pool)
        self._run()
        self.assertEqual(before, _sha(self._pool))
        self.assertEqual(1, len(self._pool_rows()))


class TestAdjudicatedReasonFallbacks(_Base):
    """裁决原因/裁决人取不到时不许崩，也不许编造。"""

    ROWS: ClassVar[list] = [{"id": "o3_a", "file": "nucleus/dead_target.py",
             "method": "ghost_method", "status": "wont_fix"}]

    def test_11_no_reason_logged_as_placeholder(self):
        self._run()
        self.assertIn("无记录", self._flog.text())

    def test_12_no_reviewer_no_empty_parens(self):
        self._run()
        self.assertNotIn("（）", self._flog.text())


class TestDecisionReasonFallback(_Base):
    """`wont_fix_reason` 缺、`decision_reason` 在 ⇒ 用后者（别报无记录）。"""

    ROWS: ClassVar[list] = [{"id": "o3_b", "file": "nucleus/dead_target.py",
             "method": "ghost_method", "status": "rejected",
             "decision_reason": "重复条目，合并处理"}]

    def test_13_decision_reason_used(self):
        self._run()
        self.assertIn("重复条目，合并处理", self._flog.text())


class TestInPoolBranch(_Base):
    """非终态 ⇒ 仍是「已在池」，但必须把 status 打出来。"""

    def _one(self, status_marker):
        self.ROWS = None
        row = {"id": "o3_live", "file": "nucleus/dead_target.py",
               "method": "ghost_method"}
        if status_marker is not None:
            row["status"] = status_marker
        with open(self._pool, "w", encoding="utf-8") as fh:
            json.dump([row], fh, ensure_ascii=False, indent=2)

    def test_14_verified_uses_in_pool_branch(self):
        self._one("verified")
        r = self._run()
        self.assertEqual("同题已在池", r["reason"])
        self.assertEqual("verified", r["existing_status"])
        self.assertIn("status=verified", self._flog.text())
        self.assertFalse(self._lines_with("同题已被裁决"))

    def test_15_pending_uses_in_pool_branch(self):
        self._one("pending")
        r = self._run()
        self.assertEqual("pending", r["existing_status"])
        self.assertIn("status=pending", self._flog.text())

    def test_16_missing_status_shows_placeholder(self):
        self._one(None)
        r = self._run()
        self.assertEqual("", r["existing_status"])
        self.assertIn("status=?", self._flog.text())

    def test_17_no_write(self):
        self._one("verified")
        before = _sha(self._pool)
        self._run()
        self.assertEqual(before, _sha(self._pool))


class TestAllTerminalStatuses(_Base):
    """六种终态逐一走「已裁决」分支（防只测 wont_fix 漏掉别的）。"""

    ROWS: ClassVar[list] = []

    def test_18_each_terminal_status_adjudicated(self):
        for st in REAL_TERMINAL:
            with self.subTest(status=st):
                with open(self._pool, "w", encoding="utf-8") as fh:
                    json.dump([{"id": "o3_" + st, "file": "nucleus/dead_target.py",
                                "method": "ghost_method", "status": st,
                                "wont_fix_reason": "单测"}], fh, ensure_ascii=False)
                self._flog.records.clear()
                r = self._run()
                self.assertEqual("同题已被裁决为 " + st, r["reason"])
                self.assertEqual(st, r["existing_status"])


class TestStatusNormalization(_Base):
    """大小写/空白必须归一，否则终态识别会漏。"""

    ROWS: ClassVar[list] = [{"id": "o3_c", "file": "nucleus/dead_target.py",
             "method": "ghost_method", "status": "  WONT_FIX  "}]

    def test_19_uppercase_and_blank_still_adjudicated(self):
        r = self._run()
        self.assertEqual("同题已被裁决为 wont_fix", r["reason"])
        self.assertEqual("wont_fix", r["existing_status"])


class TestBrokenPoolRows(_Base):
    """池被写坏（混入非 dict）时不许 AttributeError 炸整条通道。"""

    ROWS: ClassVar[list] = ["坏行", 123, None, ["x"],
            {"id": "o3_d", "file": "nucleus/dead_target.py",
             "method": "ghost_method", "status": "wont_fix",
             "wont_fix_reason": "单测"}]

    def test_20_mixed_rows_still_judged(self):
        r = self._run()
        self.assertEqual("no_candidate", r["status"])
        self.assertEqual("o3_d", r["existing_id"])

    def test_21_mixed_rows_no_write(self):
        before = _sha(self._pool)
        self._run()
        self.assertEqual(before, _sha(self._pool))


class TestIntakeStillWorks(_Base):
    """反向保护：没有同题时必须照常入池（防本刀把主路径改坏）。"""

    ROWS: ClassVar[list] = [{"id": "o3_other", "file": "nucleus/dead_target.py",
             "method": "another_method", "status": "wont_fix"}]

    def test_22_different_method_intakes(self):
        r = self._run()
        self.assertEqual("intake", r["status"], self._flog.text())
        self.assertEqual("o3_unit_fixed_id", r["entry_id"])
        self.assertEqual(2, len(self._pool_rows()))

    def test_23_intake_not_logged_as_dup(self):
        self._run()
        self.assertFalse(self._lines_with("同题已被裁决"))
        self.assertFalse(self._lines_with("已存在同题候选"))


class TestSelfInspectorIsModule(unittest.TestCase):
    """铁律 60 守卫：桩点必须打在 `_m335_o3_consume` 真正 import 到的那个对象上。"""

    def test_24_self_inspector_is_module(self):
        self.assertTrue(hasattr(SI, "_m333_intake_rate"))
        self.assertTrue(hasattr(SI, "_m335_o3_intake_candidates"))
        self.assertTrue(hasattr(SI, "_m339_intake_whitelisted"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
