# -*- coding: utf-8 -*-
"""402 刀3 判据：organ_emits 接线进每日 03:00 + EventStream.py 回收。

守两件事：
  1. `organ_emits.py` 不再是「生产零消费」的僵尸 —— main.py 必须真的注册它，
     且注册进去的是**无参可调用**（DailyScheduler.add_daily_hook 的契约）；
  2. `EventStream.py` 删干净 —— 文件不存在、全仓生产面零 import、
     398 的守卫清单同步收敛（A 类 12 / 冒烟 13），不许留一条"幽灵参数化"。
"""
import ast
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402
from nucleus.cognitive import organ_emits as oe  # noqa: E402


class FakeSched:
    """最小调度器桩：只认 add_daily_hook（与 DailyScheduler 的契约一致）。"""

    def __init__(self):
        self.daily = {}

    def add_daily_hook(self, name, fn):
        self.daily[name] = fn
        return name


class _NoHookSched:
    """不支持钩子的调度器：注册必须**返回 None 而不是抛**。"""


class TestWiring(unittest.TestCase):
    """接线判据。"""

    def test_01_main_registers_it(self):
        """★核心：main.py 必须真的调用注册函数（否则还是僵尸）。"""
        src = io.open(os.path.join(_ROOT, "main.py"), encoding="utf-8",
                      errors="replace").read()
        self.assertIn("register_organ_emits_schedule", src)
        self.assertIn("register_organ_emits_schedule()", src,
                      "只 import 不调用＝没接线")

    def test_02_hook_name_and_target(self):
        s = FakeSched()
        got = oe.register_organ_emits_schedule(s)
        self.assertIs(got, s)
        self.assertIn(oe.ORGAN_EMITS_HOOK, s.daily)
        self.assertIs(s.daily[oe.ORGAN_EMITS_HOOK], oe.dump_zero_emit_report,
                      "挂进去的必须是导出报告那个无参函数")

    def test_03_hook_is_zero_arg(self):
        """`add_daily_hook` 只喂零参函数；带必填参数会在 03:00 炸掉调度线程。"""
        import inspect

        sig = inspect.signature(oe.dump_zero_emit_report)
        required = [p.name for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        self.assertEqual(required, [], "导出函数不得有必填参数")

    def test_04_degrades_without_hook_support(self):
        self.assertIsNone(oe.register_organ_emits_schedule(_NoHookSched()))

    def test_05_degrades_on_bad_scheduler(self):
        """调度器注册抛异常 ⇒ 返回 None，不许把启动路径带崩。"""
        class Boom:
            def add_daily_hook(self, name, fn):
                raise RuntimeError("模拟调度器异常")

        self.assertIsNone(oe.register_organ_emits_schedule(Boom()))

    def test_06_hook_name_is_stable(self):
        """钩子名必须是模块级常量（重复注册靠它去重，改名字会让旧钩子留在表里）。"""
        self.assertIsInstance(oe.ORGAN_EMITS_HOOK, str)
        self.assertTrue(oe.ORGAN_EMITS_HOOK)
        s = FakeSched()
        oe.register_organ_emits_schedule(s)
        oe.register_organ_emits_schedule(s)
        self.assertEqual(list(s.daily), [oe.ORGAN_EMITS_HOOK])


class TestDumpReport(unittest.TestCase):
    """阶段产物：零发射报告落盘。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m402k3_")
        self._orig_dir = oe._report_dir
        oe._report_dir = lambda: self.tmp
        self.empty = os.path.join(self.tmp, "pairs_empty.jsonl")
        io.open(self.empty, "w", encoding="utf-8").close()

    def tearDown(self):
        oe._report_dir = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_10_report_written(self):
        r = oe.dump_zero_emit_report(self.empty)
        self.assertIsNotNone(r, "报告不该返回 None")
        files = [f for f in os.listdir(self.tmp) if f.startswith("organ_emits_")]
        self.assertEqual(len(files), 1, "应落一个 organ_emits_YYYYMMDD.json")
        self.assertRegex(files[0], r"^organ_emits_\d{8}\.json$")
        d = json.load(io.open(os.path.join(self.tmp, files[0]), encoding="utf-8"))
        self.assertEqual(d["schema"], oe.SCHEMA)

    def test_11_report_semantics(self):
        """项目现状：零发射器官都有声明（386~388 的成果），无「未声明」。"""
        r = oe.dump_zero_emit_report(self.empty)
        self.assertGreaterEqual(r["zero_emit_count"], 40,
                                "器官规模应在 40 以上（下限口径，不钉精确值）")
        self.assertEqual(r["undeclared"], [],
                         "有零发射器官没声明 ⇒ 386/387/388 的声明表被破坏")
        self.assertTrue(oe.assert_all_declared(r))

    def test_12_no_tmp_left(self):
        """原子落盘：不许留下 .tmp 残留。"""
        oe.dump_zero_emit_report(self.empty)
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(".tmp")], [])

    def test_13_missing_pairs_is_tolerated(self):
        """当日没有 pulse_pairs 文件也必须出报告（不能因缺文件整批失败）。"""
        r = oe.dump_zero_emit_report(os.path.join(self.tmp, "no_such.jsonl"))
        self.assertIsNotNone(r)
        self.assertIn("zero_emit_count", r)


class TestEventStreamRemoved(unittest.TestCase):
    """回收判据：删干净，且守卫清单同步。"""

    def test_20_file_gone(self):
        p = os.path.join(_ROOT, "nucleus", "chronos", "EventStream.py")
        self.assertFalse(os.path.isfile(p), "EventStream.py 应已删除")

    def test_21_import_fails(self):
        with self.assertRaises(ImportError):
            importlib.import_module("nucleus.chronos.EventStream")

    def test_22_no_production_import(self):
        """★全仓生产面（除 tests/tmp/.bak）零 import —— 删之前必须成立，删之后必须仍成立。"""
        hits = []
        for dp, dns, fns in os.walk(_ROOT):
            dns[:] = [d for d in dns if d not in
                      {".git", "__pycache__", ".venv", "node_modules", "tmp"}]
            if any(seg.startswith(".bak") for seg in
                   os.path.relpath(dp, _ROOT).replace("\\", "/").split("/")):
                continue
            rel_dir = os.path.relpath(dp, _ROOT).replace("\\", "/")
            if rel_dir.startswith("tests") or rel_dir.startswith("tmp"):
                continue
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dp, fn)
                rel = os.path.relpath(fp, _ROOT).replace("\\", "/")
                try:
                    tree = ast.parse(io.open(fp, encoding="utf-8",
                                             errors="replace").read())
                except SyntaxError as exc:
                    silent_exc(exc, where="m402_k3::test_22 parse:%s" % rel,
                               level="debug")
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            if a.name.endswith("EventStream"):
                                hits.append(rel)
                    elif isinstance(node, ast.ImportFrom):
                        if (node.module or "").endswith("EventStream") or any(
                                a.name == "EventStream" for a in node.names):
                            hits.append(rel)
        self.assertEqual(sorted(set(hits)), [], "生产面仍有 import EventStream")

    def test_23_398_guard_converged(self):
        """398 守卫清单必须收敛：A 类 12、冒烟 13，且不再含 EventStream。"""
        path = os.path.join(_ROOT, "tests", "test_m398_k1_orphan_disposal.py")
        src = io.open(path, encoding="utf-8").read()
        self.assertNotIn('"nucleus/chronos/EventStream.py"', src)
        self.assertNotIn('"nucleus.chronos.EventStream"', src)
        spec = importlib.util.spec_from_file_location("m398_guard_mod", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertEqual(len(m._KEEP_SEALED), 12, "A 类应为 12 个")
        self.assertEqual(len(m._SMOKE), 13, "冒烟名单应为 13 个")

    def test_24_manifest_marks_it_deleted(self):
        """处置清单要写明「已删除」，否则后人会以为它还在。"""
        doc = io.open(os.path.join(
            _ROOT, "docs", "路灯与星轨对话", "分析报告",
            "路灯_398批_孤儿处置清单.md"), encoding="utf-8").read()
        self.assertIn("EventStream.py", doc, "清单里应保留历史记录")
        idx = doc.index("EventStream.py")
        self.assertIn("402", doc[max(0, idx - 200):idx + 400],
                      "该行附近必须写明 402 刀3 的删除记录")


if __name__ == "__main__":
    unittest.main()
