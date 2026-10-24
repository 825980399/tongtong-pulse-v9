# -*- coding: utf-8 -*-
"""第360批 B 组（烛微 359 静默吞异常收口）验收测试。

覆盖：
- 刀1/刀4：logger 轮转族 11 处静默点（pass/break）→ 留痕 + 计数 + 完整性事件
- 刀2：语义层「故障伪装空结果」→ warning + 失败标记（可区分真无召回 vs 调用失败）
- 刀3：数据条目静默丢失 9 处 → note_drop 计数（可观测、可对账）

★纪律：断言一律用**增量**而非绝对值（活体一写就变），且每个用例自带
  reset，避免相互污染（铁律 A2/A5）。
"""

import ast
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tmp"))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_HINTS = ("log", "warn", "error", "debug", "info", "print", "silent_exc",
             "logger", "critical", "exception", "_note", "m360_",
             "semantic_failure", "note_", "note_drop")


def _has_log(node) -> bool:
    for _n in ast.walk(node):
        if isinstance(_n, ast.Call):
            _f = _n.func
            _name = ""
            if isinstance(_f, ast.Name):
                _name = _f.id
            elif isinstance(_f, ast.Attribute):
                _name = _f.attr
                if isinstance(_f.value, ast.Name):
                    _name = _f.value.id + "." + _name
            if _name and any(h in _name.lower() for h in LOG_HINTS):
                return True
    return False


def _silent_handlers(path, func_names=None):
    """返回该文件（可选：仅指定函数内）的静默 handler 列表。"""
    with open(path, "r", encoding="utf-8") as f:
        _tree = ast.parse(f.read())
    _out = []

    class _V(ast.NodeVisitor):
        def __init__(self):
            self._stack = []

        def visit_FunctionDef(self, node):
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Try(self, node):
            _fn = self._stack[-1] if self._stack else "<module>"
            if func_names is None or _fn in func_names:
                for _h in node.handlers:
                    _b = _h.body
                    if not _b:
                        continue
                    _last = _b[-1]
                    if len(_b) == 1 and isinstance(_last, ast.Pass):
                        _out.append(("pass_only", _h.lineno, _fn))
                    elif isinstance(_last, (ast.Continue, ast.Break)) \
                            and not _has_log(_h):
                        _out.append(("silent_jump", _h.lineno, _fn))
            self.generic_visit(node)

    _V().visit(_tree)
    return _out


# ======================================================================
class TestM360LoggerRollover(unittest.TestCase):
    """刀1 + 刀4：轮转族静默面收口。"""

    def test_01_no_silent_handler_in_rollover_family(self):
        """doRollover / _copy_truncate_rollover / cleanup_old_logs 内不得再有静默。"""
        _p = os.path.join(_PROJECT_ROOT, "nucleus", "logger.py")
        _bad = _silent_handlers(
            _p, {"doRollover", "_copy_truncate_rollover", "cleanup_old_logs"})
        self.assertEqual(_bad, [], "轮转族仍有静默 handler：%s" % _bad)

    def test_02_rollover_note_writes_integrity_event(self):
        """轮转失败必须落 log_integrity_events.log（验收硬指标）。"""
        from nucleus import logger as _lg
        _lg.m360_rollover_notes()   # 确保可读（不抛）
        _tmp = tempfile.mkdtemp(prefix="m360log_")
        _before = _lg.m360_rollover_notes().get("unit_test", 0)
        _r = _lg._m360_rollover_note(_tmp, "unit_test", RuntimeError("boom"),
                                     attempt=1, retry=3, force=True)
        self.assertEqual(_r["stage"], "unit_test")
        self.assertGreater(_r["count"], _before, "计数必须递增")
        _p = os.path.join(_tmp, "log_integrity_events.log")
        self.assertTrue(os.path.isfile(_p), "完整性事件文件必须被写出")
        with open(_p, "r", encoding="utf-8") as f:
            _recs = [json.loads(x) for x in f.read().splitlines() if x.strip()]
        self.assertTrue(_recs, "完整性事件不得为空")
        self.assertEqual(_recs[-1]["event"], "rollover_failed")
        self.assertEqual(_recs[-1]["stage"], "unit_test")
        self.assertIn("RuntimeError", _recs[-1]["error"])

    def test_03_rollover_note_counts_and_reads_back(self):
        _before = __import__("nucleus.logger", fromlist=["x"]).m360_rollover_notes()
        self.assertIsInstance(_before, dict)

    def test_04_note_never_raises(self):
        """留痕函数自身绝不能把轮转路径炸掉（路径非法也要静默降级）。"""
        from nucleus import logger as _lg
        try:
            _lg._m360_rollover_note("/no/such/dir/xyz", "unit_test_bad",
                                    RuntimeError("x"), force=True)
        except Exception as e:
            self.fail("留痕不得抛出：%r" % e)


# ======================================================================
class TestM360SemanticFailure(unittest.TestCase):
    """刀2：语义层故障不再伪装成空结果。"""

    def setUp(self):
        from nucleus.semantic import sem_failure_note as _m
        _m.reset_for_test()
        self._m = _m

    def test_05_note_and_count(self):
        self.assertEqual(self._m.sem_failure_counts(), {}, "reset 后应为空 dict")
        _r = self._m.note_semantic_failure("UnitTest", "op", ValueError("bad"))
        self.assertEqual(_r["count"], 1)
        self._m.note_semantic_failure("UnitTest", "op", ValueError("bad"))
        self.assertEqual(self._m.sem_failure_counts()["UnitTest"]["op"], 2)
        _last = self._m.last_failure("UnitTest")
        self.assertIsNotNone(_last, "失败快照必须可读（否则上层仍无法区分）")
        self.assertEqual(_last["op"], "op")

    def test_06_last_failure_none_when_clean(self):
        """从未失败 ⇒ None（不是空 dict，铁律155）。"""
        self.assertIsNone(self._m.last_failure("NeverFailed"))

    def test_07_load_config_failure_is_visible(self):
        """注入 config 读取异常 ⇒ 4 件 _load_config 必须留痕（不再静默）。"""
        _cases = [
            ("nucleus.semantic.VectorStore", "VectorStore"),
            ("nucleus.semantic.VectorEncoder", "VectorEncoder"),
            ("nucleus.semantic.SemanticKernelService", "SemanticKernelService"),
            ("nucleus.semantic.AsyncEncodeQueue", "AsyncEncodeQueue"),
        ]

        class _Boom:
            def __getattr__(self, name):
                raise RuntimeError("注入的配置读取故障")

        _old = sys.modules.get("config")
        try:
            sys.modules["config"] = _Boom()
            for _modname, _comp in _cases:
                _mod = __import__(_modname, fromlist=["_load_config"])
                _out = _mod._load_config()
                self.assertEqual(_out, {}, "失败时仍返回空配置（零行为变化）")
                _c = self._m.sem_failure_counts().get(_comp, {}).get("load_config")
                self.assertGreater(int(_c or 0), 0,
                                   "%s._load_config 失败必须被计数" % _comp)
        finally:
            if _old is not None:
                sys.modules["config"] = _old

    def test_08_vectorstore_search_failure_marks(self):
        """检索失败必须置失败标记（上层才能区分「真无召回」与「调用炸了」）。

        注入点必须选在 try **内部**：`_ensure_loaded()` 在 try 之外（688 行），
        在那里抛异常不会被捕获（第一版测试就是踩了这个坑）。
        """
        import threading

        import numpy as np
        from nucleus.semantic import VectorStore as _vs
        from nucleus.semantic.VectorEncoder import VectorEncoder
        self.assertTrue(hasattr(_vs, "_M360_COMPONENT"))

        _store = _vs.VectorStore.__new__(_vs.VectorStore)
        _store._m360_last_search_failed = False
        _store._matrix = np.zeros((2, 512), dtype="float32")
        _store._ids = ["a", "b"]
        _store._lock = threading.RLock()
        _store._cfg = {}
        _store._quality_flag_provider = None
        # _ensure_loaded 在 try **之外**，让它 no-op（否则它自身缺属性会先炸）
        _store._ensure_loaded = lambda: True

        _orig = VectorEncoder.cosine_matrix

        def _boom(*_a, **_k):
            raise RuntimeError("检索炸了")

        try:
            VectorEncoder.cosine_matrix = staticmethod(_boom)
            _out = _store.search_by_vector(np.zeros(512, dtype="float32"), top_k=3)
        finally:
            VectorEncoder.cosine_matrix = _orig
        self.assertEqual(_out, [], "失败时仍返回空列表（零行为变化）")
        self.assertTrue(getattr(_store, "_m360_last_search_failed", False),
                        "检索失败必须置失败标记")
        self.assertIsNotNone(self._m.last_failure("VectorStore"))


# ======================================================================
class TestM360DropTracker(unittest.TestCase):
    """刀3：数据条目静默丢失可计数、可观测。"""

    def setUp(self):
        from nucleus import drop_tracker as _d
        _d.reset_for_test()
        self._d = _d

    def test_09_note_and_total(self):
        self.assertEqual(self._d.drop_counts(), {})
        self._d.note_drop("UnitTest", "bad_line", "x")
        self._d.note_drop("UnitTest", "bad_line")
        self.assertEqual(self._d.drop_counts()["UnitTest"]["bad_line"], 2)
        self.assertEqual(self._d.drop_total("UnitTest"), 2)
        self.assertEqual(self._d.drop_total(), 2)

    def test_10_bad_line_drop_counted_and_loop_continues(self):
        """坏行必须被计数，且**不中断**主循环（好行仍要读出）。"""
        from nucleus.audit_problem_pool_consumer import _read_jsonl_ids
        _tmp = tempfile.mkdtemp(prefix="m360drop_")
        _p = os.path.join(_tmp, "ids.jsonl")
        with io.open(_p, "w", encoding="utf-8") as f:
            f.write('{"id": "good-1"}\n')
            f.write("这不是 JSON\n")
            f.write('{"id": "good-2"}\n')
            f.write("{broken\n")
        _before = self._d.drop_total(
            "audit_problem_pool_consumer._read_jsonl_ids")
        _ids = _read_jsonl_ids(_p)
        self.assertEqual(sorted(_ids), ["good-1", "good-2"],
                         "坏行不得中断主循环，好行必须全部读出")
        _after = self._d.drop_total(
            "audit_problem_pool_consumer._read_jsonl_ids")
        self.assertEqual(_after - _before, 2, "两条坏行都应被计数（实得 %d）"
                         % (_after - _before))

    def test_11_training_pair_bad_line_counted(self):
        """训练样本转换：坏行/无效对都必须计数（转化率分母不再虚高）。"""
        from nucleus.llm.call_recorder import convert_calls_to_training_pairs
        _tmp = tempfile.mkdtemp(prefix="m360pair_")
        _src = os.path.join(_tmp, "calls.jsonl")
        _out = os.path.join(_tmp, "pairs.jsonl")
        with io.open(_src, "w", encoding="utf-8") as f:
            f.write("not-json-at-all\n")
        _before = self._d.drop_total(
            "call_recorder.convert_calls_to_training_pairs")
        _n = convert_calls_to_training_pairs(_src, _out)
        self.assertEqual(_n, 0)
        _after = self._d.drop_total(
            "call_recorder.convert_calls_to_training_pairs")
        self.assertGreater(_after, _before, "坏行丢失必须被计数")

    def test_12_pool_cast_failure_counted(self):
        """first_seen 转 float 失败 ⇒ 条目被跳过，必须计数（池统计失真可查）。"""
        from nucleus.audit_problem_pool import _first_ts
        _before = self._d.drop_total("audit_problem_pool._first_ts")
        _v = _first_ts({"first_seen_ts": "not-a-number"})
        self.assertEqual(_v, 0.0, "转换失败仍回落 0.0（零行为变化）")
        _after = self._d.drop_total("audit_problem_pool._first_ts")
        self.assertGreater(_after, _before, "cast 失败必须被计数")

    def test_13_note_drop_never_raises(self):
        try:
            self._d.note_drop(None, None, None)
            self._d.note_drop("", "", object())
        except Exception as e:
            self.fail("note_drop 不得抛出：%r" % e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
