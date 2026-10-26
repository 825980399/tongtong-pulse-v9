# -*- coding: utf-8 -*-
"""371A5 第1笔 · 三条静默隐患收口（T2×2 + 轮转去重标记不回滚）。

被测：
1. main.py `_probe_loop`：等待心跳异常**不再 break**（原实现让整条探查链永久停掉）。
2. AsyncEncodeQueue.reconcile：逐节点失败**计数 + 汇总 WARNING**（原实现只 debug 跳过）。
3. LLMCallRecorder._rotate：
   a) 清理抛异常时**回滚「今天已轮转」标记**（原实现当天永不重试，注释与实现矛盾）；
   b) 单个过期文件删不掉要**计数出声**。

★铁律188 防自噬：本条只读 main.py 与两个被测模块，不含本文件自身。
★反向判别：test_02 用「含 break 的同构片段」证明 test_01 的判据不是恒真。
"""
import ast
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---------------- 共用判据：except 块里是否有 Break ----------------

def _except_has_break(src: str, func_name: str):
    """返回 (found_func, has_break, has_continue)。只统计 func_name 内的 ExceptHandler。"""
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            target = node
            break
    if target is None:
        return False, False, False
    has_break = False
    has_continue = False
    for node in ast.walk(target):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Break):
                has_break = True
            elif isinstance(sub, ast.Continue):
                has_continue = True
    return True, has_break, has_continue


class TestProbeLoopNoBreak(unittest.TestCase):
    """1. main.py::_probe_loop 异常不得终止整条探查链。"""

    def test_01_probe_loop_except_has_no_break(self):
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            src = f.read()
        found, has_break, has_continue = _except_has_break(src, "_probe_loop")
        self.assertTrue(found, "未在 main.py 找到 _probe_loop")
        self.assertFalse(has_break, "_probe_loop 的 except 块里仍有 break ⇒ 异常会终止整条探查链")
        self.assertTrue(has_continue, "_probe_loop 的 except 块应改为 continue（本轮跳过、链路继续）")

    def test_02_reverse_discriminating_old_shape_is_caught(self):
        """反向判别：把旧形状（except 内 break）喂给同一判据，必须被判「有 break」。"""
        old_shape = (
            "def _probe_loop():\n"
            "    while True:\n"
            "        try:\n"
            "            pass\n"
            "        except Exception as _e:\n"
            "            print(_e)\n"
            "            break\n"
        )
        found, has_break, _ = _except_has_break(old_shape, "_probe_loop")
        self.assertTrue(found)
        self.assertTrue(has_break, "判据没有判别力：旧形状未被抓出")

    def test_03_probe_loop_keeps_warning_and_counter(self):
        """留痕要求：升 WARNING + 连续次数属性（不是只 debug）。"""
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_probe_wait_fail_n", src, "缺少连续失败计数")
        idx = src.find("_probe_wait_fail_n")
        window = src[idx - 900: idx + 900]
        self.assertIn("LogLevel.WARNING", window, "异常路径未升 WARNING")
        self.assertIn("[371A5]", window, "缺少批次留痕标记")


class TestReconcileCountsFailures(unittest.TestCase):
    """2. reconcile 逐节点失败要计数并汇总出声。"""

    def _make_queue(self, nodes):
        from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue

        q = AsyncEncodeQueue()
        store = mock.Mock()
        store.needs_encode.return_value = True
        store.reap_orphans.return_value = 0
        q._store = store
        q._encoder = mock.Mock()

        class _Pool:
            def get_all_including_evicted(self):
                return nodes

        q.set_node_pool(_Pool())
        return q

    def test_04_failed_nodes_counted_in_warning(self):
        from nucleus.semantic import AsyncEncodeQueue as _mod

        class _Nd:
            def __init__(self, i):
                self.node_id = "n%d" % i

        nodes = [_Nd(0), _Nd(1), _Nd(2)]
        q = self._make_queue(nodes)

        # 第 1 个节点取文本即抛；其余正常
        def _node_text(nd):
            if nd is nodes[0]:
                raise RuntimeError("取值爆炸")
            return "文本"

        q._node_text = _node_text
        q.submit = lambda nid, text: True

        with mock.patch.object(_mod._logger, "warning") as w:
            added = q.reconcile()

        self.assertEqual(added, 2, "正常节点应全部入队")
        self.assertEqual(w.call_count, 1, "应打且只打 1 条汇总 WARNING（避免刷屏）")
        msg = str(w.call_args[0][0])
        self.assertIn("1/3", msg, "汇总应给出失败数/总数")
        self.assertIn("[371A5]", msg)

    def test_05_all_ok_no_warning(self):
        """负样本：无失败时不得凭空告警。"""
        from nucleus.semantic import AsyncEncodeQueue as _mod

        class _Nd:
            def __init__(self, i):
                self.node_id = "n%d" % i

        nodes = [_Nd(0), _Nd(1)]
        q = self._make_queue(nodes)
        q.set_node_pool(type("P", (), {"get_all_including_evicted": staticmethod(lambda: nodes)})())
        q._node_text = lambda nd: "文本"
        q.submit = lambda nid, text: True

        with mock.patch.object(_mod._logger, "warning") as w:
            added = q.reconcile()

        self.assertEqual(added, 2)
        self.assertEqual(w.call_count, 0, "无失败却告警 ⇒ 误报")


class TestRotateRollback(unittest.TestCase):
    """3. _rotate 失败要回滚当天标记（原实现当天永不重试）。"""

    def _recorder(self):
        from nucleus.llm.call_recorder import LLMCallRecorder

        tmpd = tempfile.mkdtemp(prefix="m371a5_")
        rec = LLMCallRecorder(base_dir=tmpd)
        rec.retention_days = lambda: 3
        return rec, tmpd

    def test_06_rollback_day_marker_on_listdir_failure(self):
        rec, _tmpd = self._recorder()
        rec._last_rotate_day = "PREV"
        with mock.patch("os.listdir", side_effect=OSError("目录炸了")):
            rec._rotate(now=1_800_000_000.0)
        self.assertEqual(rec._last_rotate_day, "PREV",
                         "清理失败必须回滚「今天已轮转」标记，否则当天永不重试")

    def test_07_remove_failure_counted_not_silent(self):
        rec, tmpd = self._recorder()
        # 造一个「过期」文件
        fp = os.path.join(tmpd, "calls_20000101.jsonl")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("{}\n")

        from nucleus.llm import call_recorder as _cr

        with mock.patch("os.remove", side_effect=OSError("占用中")), \
                mock.patch.object(_cr._module_logger, "warning") as w:
            rec._rotate(now=1_800_000_000.0)

        self.assertEqual(w.call_count, 1, "单个删除失败应汇总出声一次")
        self.assertIn("[371A5]", str(w.call_args[0][0]))
        # 成功路径未失败 ⇒ 标记保持今天（不重复扫目录）
        self.assertEqual(rec._last_rotate_day, rec._day_str(1_800_000_000.0))

    def test_08_success_path_no_warning(self):
        """负样本：正常删除成功时不得告警、标记保持今天。"""
        rec, tmpd = self._recorder()
        fp = os.path.join(tmpd, "calls_20000101.jsonl")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("{}\n")

        from nucleus.llm import call_recorder as _cr

        with mock.patch.object(_cr._module_logger, "warning") as w:
            rec._rotate(now=1_800_000_000.0)

        self.assertEqual(w.call_count, 0, "正常路径不该告警")
        self.assertFalse(os.path.exists(fp), "过期文件应被删除")
        self.assertEqual(rec._last_rotate_day, rec._day_str(1_800_000_000.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
