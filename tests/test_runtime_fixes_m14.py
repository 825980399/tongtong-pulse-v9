# -*- coding: utf-8 -*-
"""
test_runtime_fixes_m14.py —— 主线第14批 任务1 门控单测

覆盖：
  · P0 抢修：PulseNode.py 误删 import os/sys 的回归护栏
  · T1.1 胃「知识免疫」：query 参数名修复 + 开关 + 真实异常日志
  · T1.2 肝「代码调用关联」：dict.items() 解包顺序修复 + 开关

设计：直接**切片并执行真实源码片段**（而非复刻逻辑），
      保证测的是线上真正跑的代码，源码一旦回退即失败。
"""
import os
import re
import sys
import textwrap
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.const import LogLevel  # noqa: E402


def _read(rel):
    with open(os.path.join(_PROJECT_ROOT, rel.replace("/", os.sep)),
                 encoding="utf-8") as f:
        return f.read()


def _slice_lines(rel, start_pred, end_pred, dedent=8):
    """截取 [start, end) 行区间（end 为第一条满足 end_pred 的行，不含）。"""
    lines = _read(rel).splitlines()
    s = next(i for i, ln in enumerate(lines) if start_pred(ln))
    e = next(i for i, ln in enumerate(lines) if i > s and end_pred(ln))
    return textwrap.dedent("\n".join(lines[s:e]))


class _Recorder:
    """记录 _log 调用，模拟器官日志。"""

    def __init__(self):
        self.records = []


class _FakeOrgan:
    """最小化器官替身：只提供免疫段需要的 _log 与 node_pool。"""

    def __init__(self, node_pool=None, log_sink=None):
        self.node_pool = node_pool
        self._sink = log_sink if log_sink is not None else []
        self._log = self._record
        # ★B156-3 T-A05：免疫段现引用 self._kal（KAL 集成），替身须提供，否则 AttributeError。
        self._kal = _FakeKal()

    def _record(self, level, msg):
        self._sink.append((level, msg))


class _FakeKal:
    """最小化 KAL 替身：免疫段已迁移至 self._kal.query_nodes/add_node（m69/m70 KAL 集成），
    测试替身须提供该接口，否则 AttributeError 会被误判为生产回归。"""
    def __init__(self):
        self.query_nodes_calls = []
    def query_nodes(self, **kw):
        self.query_nodes_calls.append(kw)
        # 返回非空列表，确保免疫段一致性检查分支被触发（与 _self_nodes 真值性一致）。
        return [_FakeNode("_kal_self", ["自我"], "v")]
    def add_node(self, node):
        pass


class _FakePool:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def query(self, **kw):
        self.calls.append(kw)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeNode:
    def __init__(self, node_id, keywords=None, value=""):
        self.node_id = node_id
        self.keywords = keywords or []
        self.value = value
        self.space_path = "/自我"
        self.trust_score = 50.0


# =====================================================================
# P0：PulseNode.py 头部 import 回归护栏
# =====================================================================
class TestPulseNodeHeadRepair(unittest.TestCase):
    """护栏：凌晨批次头部脚本曾误删 PulseNode.py 的 import os/sys。"""

    def test_module_head_has_os_and_sys_imports(self):
        head = "\n".join(_read("nucleus/mnemosyne/PulseNode.py").splitlines()[:40])
        self.assertRegex(head, r"(?m)^import os$")
        self.assertRegex(head, r"(?m)^import sys$")

    def test_import_order_os_sys_before_path_insert(self):
        lines = _read("nucleus/mnemosyne/PulseNode.py").splitlines()
        i_os = next(i for i, l in enumerate(lines) if l.strip() == "import os")
        i_sys = next(i for i, l in enumerate(lines) if l.strip() == "import sys")
        i_ins = next(i for i, l in enumerate(lines) if l.strip().startswith("sys.path.insert("))
        self.assertLess(i_os, i_ins, "import os 必须在 sys.path.insert 之前")
        self.assertLess(i_sys, i_ins, "import sys 必须在 sys.path.insert 之前")

    def test_module_importable(self):
        import nucleus.mnemosyne.PulseNode as m
        self.assertTrue(hasattr(m, "PulseNode"))


# =====================================================================
# T1.1：胃「知识免疫」段
# =====================================================================
_STOMACH_REL = "organs/body/PulseStomach.py"


def _immune_block_source():
    return _slice_lines(
        _STOMACH_REL,
        lambda ln: "if (_final_trust >= 50.0 and keywords" in ln,
        lambda ln: ln.strip() == "if self.node_pool:",
    )


class TestStomachImmuneFix(unittest.TestCase):
    def _run_block(self, *, trust=60.0, keywords=("知识", "架构"),
                   pool=None, node=None, content="测试内容",
                   consistency=None, monkey_check=None):
        """在受控命名空间里执行真实的免疫段源码。"""
        ns = {
            "_final_trust": trust,
            "keywords": list(keywords),
            "content": content,
            "node": node if node is not None else _FakeNode("n1"),
            "self": _FakeOrgan(node_pool=pool),
            "config": config,
            "LogLevel": LogLevel,
            "sys": sys,
            "isinstance": isinstance,
        }
        if monkey_check is not None:
            ns["check_self_consistency_for_node"] = monkey_check
        else:
            from nucleus.knowledge_noise_filter import (
                check_self_consistency_for_node as _real,
            )
            ns["check_self_consistency_for_node"] = _real
        exec(compile(_immune_block_source(), "<immune-block>", "exec"), ns)
        return ns

    # --- 根因证明：旧参数名确实会抛 TypeError ---
    def test_old_kwarg_raises_typeerror(self):
        """证伪：query(space_path=...) 不被接受（这正是 995 次静默异常的根因）。"""
        from nucleus.mnemosyne.PulseNodePool import PulseNodePool
        import inspect
        params = inspect.signature(PulseNodePool.query).parameters
        self.assertIn("space_path_prefix", params)
        self.assertNotIn("space_path", params)

    def test_source_uses_correct_kwarg(self):
        src = _read(_STOMACH_REL)
        # ★B156-3 T-A05：免疫段查询已迁 self._kal.query_nodes(space_path_prefix=...)，锚同步。
        self.assertIn('query_nodes(space_path_prefix="/自我", limit=20)', src)
        self.assertNotIn('query(space_path="/自我"', src)

    def test_source_log_line_number_not_hardcoded_833(self):
        src = _read(_STOMACH_REL)
        self.assertNotIn("PulseStomach.py:833", src,
                         "硬编码的过期行号必须移除")
        self.assertIn("tb_lineno", src, "应打印真实异常行号")

    # --- 功能：修复后不再抛异常，且真的查询了自我节点 ---
    def test_query_called_with_prefix_and_no_exception(self):
        pool = _FakePool([_FakeNode("s1", ["知识", "架构"], "架构说明")])
        ns = self._run_block(pool=pool)
        organ = ns["self"]
        # ★B156-3 T-A05：免疫段已迁移至 self._kal.query_nodes（非 node_pool.query），锚同步。
        self.assertEqual(len(organ._kal.query_nodes_calls), 1)
        self.assertEqual(organ._kal.query_nodes_calls[0].get("space_path_prefix"), "/自我")
        # 未吞异常：不应出现「检查跳过」日志
        msgs = [m for _, m in organ._sink]
        self.assertFalse(any("检查跳过" in m for m in msgs), msgs)

    def test_trust_penalty_applied_when_contradiction(self):
        node = _FakeNode("n1")
        pool = _FakePool([_FakeNode("s1", ["知识", "架构"], "架构说明")])
        ns = self._run_block(
            trust=60.0, pool=pool, node=node,
            monkey_check=lambda **kw: {
                "contradiction_found": True, "contradiction_count": 2,
                "trust_penalty": 25.0, "conflicting_self_paths": [],
            },
        )
        self.assertAlmostEqual(ns["node"].trust_score, 35.0, places=3)

    # --- 灰度开关：关闭时完全跳过（零副作用） ---
    def test_switch_off_skips_entire_block(self):
        old = getattr(config, "ENABLE_STOMACH_IMMUNE_GUARD", None)
        try:
            config.ENABLE_STOMACH_IMMUNE_GUARD = False
            pool = _FakePool([_FakeNode("s1", ["知识", "架构"], "x")])
            ns = self._run_block(pool=pool)
            self.assertEqual(pool.calls, [], "开关关闭时不得发起查询")
            self.assertEqual(ns["node"].trust_score, 50.0, "开关关闭时信任分不变")
        finally:
            if old is None:
                delattr(config, "ENABLE_STOMACH_IMMUNE_GUARD")
            else:
                config.ENABLE_STOMACH_IMMUNE_GUARD = old

    def test_switch_on_by_default(self):
        self.assertIs(getattr(config, "ENABLE_STOMACH_IMMUNE_GUARD", None), True)

    # --- 异常路径：必须留下真实原因与行号，不再静默 ---
    def test_exception_logs_real_reason_and_line(self):
        def _boom(**kw):
            raise ValueError("boom-in-immune")
        pool = _FakePool([_FakeNode("s1", ["知识", "架构"], "x")])
        ns = self._run_block(pool=pool, monkey_check=_boom)
        msgs = [m for lv, m in ns["self"]._sink if "检查跳过" in m]
        self.assertEqual(len(msgs), 1, ns["self"]._sink)
        msg = msgs[0]
        self.assertIn("ValueError", msg)
        self.assertIn("boom-in-immune", msg)
        self.assertRegex(msg, re.compile(r"PulseStomach\.py:\d+"))
        self.assertNotIn(":833", msg)

    def test_non_list_query_result_does_not_crash(self):
        pool = _FakePool(None)
        ns = self._run_block(pool=pool)
        msgs = [m for _, m in ns["self"]._sink]
        self.assertFalse(any("检查跳过" in m for m in msgs), msgs)

    def test_none_consistency_does_not_crash(self):
        pool = _FakePool([_FakeNode("s1", ["知识", "架构"], "x")])
        ns = self._run_block(pool=pool, monkey_check=lambda **kw: None)
        msgs = [m for _, m in ns["self"]._sink]
        self.assertFalse(any("检查跳过" in m for m in msgs), msgs)

    def test_low_trust_skips_block(self):
        pool = _FakePool([_FakeNode("s1", ["知识", "架构"], "x")])
        self._run_block(trust=20.0, pool=pool)
        self.assertEqual(pool.calls, [])


# =====================================================================
# T1.2：肝「代码调用关联」兜底匹配
# =====================================================================
_LIVER_REL = "organs/body/PulseLiver.py"


def _liver_block_source():
    return _slice_lines(
        _LIVER_REL,
        lambda ln: "_callee_node = _method_node_map.get(_callee_key)" in ln,
        lambda ln: ln.strip().startswith("if not _callee_node or _callee_node.node_id =="),
    )


class TestLiverCallEdgeFix(unittest.TestCase):
    def _run_block(self, method_map, callee_key, callee):
        ns = {
            "_method_node_map": method_map,
            "_callee_key": callee_key,
            "_callee": callee,
            "config": config,
            "isinstance": isinstance,
        }
        exec(compile(_liver_block_source(), "<liver-block>", "exec"), ns)
        return ns

    def test_source_unpacking_order_fixed(self):
        src = _read(_LIVER_REL)
        self.assertIn("for _key, _node in _method_node_map.items():", src)
        self.assertNotIn("for _node, _key in _method_node_map.items():", src)

    def test_fuzzy_fallback_finds_node_without_attributeerror(self):
        target = _FakeNode("n-callee", value="")
        method_map = {"PulseHeart.beat": _FakeNode("n-other", value="")}
        method_map["PulseVessel.pump"] = target
        ns = self._run_block(method_map, "unknown.key", "pump")
        self.assertIs(ns["_callee_node"], target)

    def test_no_match_leaves_none(self):
        method_map = {"PulseHeart.beat": _FakeNode("n-other", value="")}
        ns = self._run_block(method_map, "unknown.key", "nonexistent")
        self.assertIsNone(ns["_callee_node"])

    def test_exact_key_hit_short_circuits_fallback(self):
        exact = _FakeNode("n-exact", value="")
        method_map = {"PulseHeart.beat": exact,
                      "PulseHeart.other": _FakeNode("n-x", value="")}
        ns = self._run_block(method_map, "PulseHeart.beat", "beat")
        self.assertIs(ns["_callee_node"], exact)

    def test_switch_off_disables_fallback(self):
        old = getattr(config, "ENABLE_LIVER_CALL_EDGE_GUARD", None)
        try:
            config.ENABLE_LIVER_CALL_EDGE_GUARD = False
            method_map = {"PulseVessel.pump": _FakeNode("n-target", value="")}
            ns = self._run_block(method_map, "unknown.key", "pump")
            self.assertIsNone(ns["_callee_node"], "开关关闭时不做兜底匹配")
        finally:
            if old is None:
                delattr(config, "ENABLE_LIVER_CALL_EDGE_GUARD")
            else:
                config.ENABLE_LIVER_CALL_EDGE_GUARD = old

    def test_switch_on_by_default(self):
        self.assertIs(getattr(config, "ENABLE_LIVER_CALL_EDGE_GUARD", None), True)

    def test_non_str_key_is_tolerated(self):
        """防御：即使 map 里混入非字符串键也不得抛异常。"""
        target = _FakeNode("n-t", value="")
        method_map = {"PulseVessel.pump": target}
        method_map[None] = _FakeNode("n-bad", value="")  # type: ignore[dict-item]
        ns = self._run_block(method_map, "unknown.key", "pump")
        self.assertIs(ns["_callee_node"], target)


if __name__ == "__main__":
    unittest.main()
