# -*- coding: utf-8 -*-
"""420/L02 · `_pulse_bus.request` 假通路 → 真实 KAL 入口（第420批 刀L02）。

## 缺陷（实测，非推断）

`_pulse_bus` 这个**属性本身全库从未被赋值**：
  * 器官基类没有；`nucleus/events/EventBus.py` 只有
    `publish / publish_async / subscribe / unsubscribe`，**没有 `request`**；
  * 全库仅 `PulseKidney.py:718/734` 用**直接属性访问**调它 ⇒ 一调必
    `AttributeError`，被 `except` 吞掉后回落直连；
  * `config.py:5987 ENABLE_KAL_CALL_SITES = True`（是开的）⇒ 这不是
    「关掉的功能」，是「假通路」：失败且静默，比缺功能更坏。

三处实现对比（同一需求、三种写法）：

| 位置 | 写法 | 判定 |
|---|---|---|
| `PulseLiver.py:3979` | `get_kal().get_node(...)` | ✅ 正确范式 |
| `PulseStomach.py:2467` | `getattr(self,"_pulse_bus",None)` 三重探测 | ⚠️ 安全但**永不生效** |
| `PulseKidney.py:707` | `self._pulse_bus.request(...)` | ❌ **必崩** |

历史备份 `data/code_backups/backup_20260920_181326/organs/body/PulseKidney.py:706`
写的就是 `get_kal().get_node(node_id)` ⇒ 是后续补丁污染，不是原始设计。

## 修法

新增 `_m420_kal_entry()`：取法顺序 `self._kal`（`set_node_pool()` 构造，pool 一定对）
→ `get_kal()`（全局单例兜底）→ `None`（调用方必须回落直连）。
`search` 走 `query_nodes()`（KAL 对 `node_pool.query` 的**官方透传**），
不走 `search_by_evol_level()` —— 后者签名是 `(evol_level)` **不吃 top_k**。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.body.PulseKidney import PulseKidney            # noqa: E402
from organs.body.PulseStomach import PulseStomach          # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode          # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402


def _mk_pool():
    """造 4 个节点：L1×1 / L2×2 / L3×1（纯内存池，零落盘）。"""
    pool = PulseNodePool()
    made = []
    for _i, _lv in ((1, "L1"), (2, "L2"), (3, "L2"), (4, "L3")):
        n = PulseNode("L02验证节点%d" % _i, keywords=["k%d" % _i],
                      space_path="/m420/l02/n%d" % _i)
        n.evol_level = _lv
        pool.add(n)
        made.append(n)
    return pool, made


class TestA_KidneyBehavior(unittest.TestCase):
    """A 组：PulseKidney 行为（原「必崩」路径现在真能工作）。"""

    @classmethod
    def setUpClass(cls):
        cls.pool, cls.nodes = _mk_pool()

    def setUp(self):
        self.k = PulseKidney()
        self.k.set_node_pool(self.pool)

    def test_01_entry_helper_exists(self):
        """★418 教训 #34：`self.X()` 必须在 `dir(类)` 里，否则 F821 之外的悬空调用无人抓。"""
        self.assertIn("_m420_kal_entry", dir(PulseKidney))

    def test_02_pulse_bus_really_absent(self):
        """钉住缺陷前提：`_pulse_bus` 不存在 —— 这正是原实现必崩的原因。"""
        self.assertFalse(hasattr(self.k, "_pulse_bus"),
                         "若哪天真的注入了 _pulse_bus，本刀的结论需要重新核")
        with self.assertRaises(AttributeError):
            _ = self.k._pulse_bus

    def test_03_get_node_hits_pool(self):
        """命中：KAL 优先通道必须真能取到节点（修复前这里恒为「靠回退」）。"""
        _nid = self.nodes[0].node_id
        _r = self.k._m70_kal_get_node(_nid)
        self.assertIsNotNone(_r, "KAL 通道没取到已入池的节点")
        self.assertEqual(_nid, getattr(_r, "node_id", None))

    def test_04_get_node_miss_returns_none(self):
        self.assertIsNone(self.k._m70_kal_get_node("m420-not-exist-xxx"))

    def test_05_get_node_empty_id_safe(self):
        """空 id 不许抛（下游有心跳路径，抛了就是线上事故）。"""
        try:
            self.k._m70_kal_get_node("")
        except Exception as e:  # noqa: BLE001
            self.fail("空 id 抛异常: %s: %s" % (type(e).__name__, e))

    def test_06_search_by_level_respects_top_k(self):
        """★`top_k` 必须真生效 —— 历史版本传 `top_k` 给不吃它的方法是 TypeError。"""
        _all = self.k._m70_kal_search_by_level("L2", top_k=10)
        self.assertEqual(2, len(_all), "L2 应有 2 条，实际 %r" % ([getattr(x, 'value', x) for x in _all],))
        _one = self.k._m70_kal_search_by_level("L2", top_k=1)
        self.assertEqual(1, len(_one), "top_k=1 没生效")

    def test_07_search_by_level_levels(self):
        for _lv, _want in (("L1", 1), ("L2", 2), ("L3", 1)):
            self.assertEqual(_want, len(self.k._m70_kal_search_by_level(_lv, top_k=10)),
                             "%s 条数不符" % _lv)

    def test_08_search_unknown_level_empty(self):
        self.assertEqual([], self.k._m70_kal_search_by_level("L9", top_k=5))

    def test_09_switch_off_goes_direct(self):
        """开关关掉 ⇒ 完全回到直连（search 必须是空，get 仍能取到）。"""
        self.k._m70_kal_callsites_on = lambda: False
        self.assertEqual([], self.k._m70_kal_search_by_level("L2", top_k=10))
        self.assertIsNotNone(self.k._m70_kal_get_node(self.nodes[0].node_id))

    def test_10_kal_none_falls_back_to_get_kal(self):
        """`self._kal` 为 None 时必须回落全局单例，而不是直接放弃。"""
        from nucleus.knowledge_access_layer import get_kal
        self.k._kal = None
        try:
            _e = self.k._m420_kal_entry("test_10")
            self.assertIsNotNone(_e, "self._kal 为 None 时没有回落 get_kal()")
            # ★不 refresh：refresh 会重建全局单例、污染同进程的其他用例
            self.assertIs(_e, get_kal(), "_m420_kal_entry 拿到的不是全局单例")
        finally:
            self.k.set_node_pool(self.pool)  # 复原

    def test_11_get_kal_raises_returns_none(self):
        """★418 教训 #40：日志/工具函数绝不允许决定返回值 —— 取不到就返 None，不抛。"""
        import nucleus.knowledge_access_layer as kal_mod
        _orig = kal_mod.get_kal
        self.k._kal = None

        def _boom(*a, **kw):
            raise RuntimeError("m420 故意炸")

        kal_mod.get_kal = _boom
        try:
            self.assertIsNone(self.k._m420_kal_entry("test_11"))
            # 且不因此影响主流程：未命中就回落直连
            self.assertIsNone(self.k._m70_kal_get_node("m420-not-exist-xxx"))
        finally:
            kal_mod.get_kal = _orig
            self.k.set_node_pool(self.pool)


class TestB_StomachBehavior(unittest.TestCase):
    """B 组：PulseStomach 的「安全但永不生效」假通路。"""

    @classmethod
    def setUpClass(cls):
        cls.pool, cls.nodes = _mk_pool()

    def setUp(self):
        self.s = PulseStomach()
        if hasattr(self.s, "set_node_pool"):
            self.s.set_node_pool(self.pool)
        else:
            self.s.node_pool = self.pool
            self.s._kal = None

    def test_20_entry_helper_exists(self):
        self.assertIn("_m420_kal_entry", dir(PulseStomach))

    def test_21_three_bus_attrs_absent(self):
        """钉住缺陷前提：原探测链的 `_pulse_bus` / `_bus` / `_pulse` **三个都不存在**，
        所以 `_bus.call(...)` 分支永远进不去 —— KAL 从未被真正调用过。"""
        for _a in ("_pulse_bus", "_bus", "_pulse"):
            self.assertFalse(hasattr(self.s, _a), "器官上居然有 %s" % _a)

    def test_22_get_node_hits_pool(self):
        _r = self.s._m70_kal_get_node(self.nodes[0].node_id)
        self.assertIsNotNone(_r, "PulseStomach 的 KAL 通道没取到已入池节点")
        self.assertEqual(self.nodes[0].node_id, getattr(_r, "node_id", None))

    def test_23_search_keywords_hits(self):
        _r = self.s._m70_kal_search_keywords(["k1"], top_k=5)
        self.assertEqual(1, len(_r), "关键词搜索应命中 1 条，实际 %d" % len(_r))

    def test_24_search_keywords_empty_input(self):
        self.assertEqual([], self.s._m70_kal_search_keywords([], top_k=5))


class TestC_SourceGuard(unittest.TestCase):
    """C 组：源码守门 —— 把「不许再写回假通路」变成可执行判据。"""

    _FILES = (("organs/body/PulseKidney.py", "PulseKidney"),
              ("organs/body/PulseStomach.py", "PulseStomach"))

    def _tree(self, rel):
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        with open(p, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=rel)

    def test_30_no_pulse_bus_attribute_access_in_code(self):
        """★AST 判据（注释天然不算）：源码里不许再有 `self._pulse_bus` 的**属性访问**。"""
        for rel, _cls in self._FILES:
            bad = []
            for node in ast.walk(self._tree(rel)):
                if not isinstance(node, ast.Attribute):
                    continue
                if node.attr != "_pulse_bus":
                    continue
                if isinstance(node.value, ast.Name) and node.value.id == "self":
                    bad.append(getattr(node, "lineno", -1))
            self.assertEqual([], bad, "%s 仍有 self._pulse_bus 代码引用（行号 %r）"
                             % (rel, bad))

    def test_31_no_request_call_on_bus_alias(self):
        """`_bus.request(...)` / `_pulse.request(...)` 同类假通路也一并禁掉。"""
        for rel, _cls in self._FILES:
            bad = []
            for node in ast.walk(self._tree(rel)):
                if not isinstance(node, ast.Call):
                    continue
                _f = node.func
                if not isinstance(_f, ast.Attribute) or _f.attr != "request":
                    continue
                _v = _f.value
                _name = getattr(_v, "id", None) or getattr(_v, "attr", None)
                if _name in ("_pulse_bus", "_bus", "_pulse", "pulse_bus"):
                    bad.append(getattr(node, "lineno", -1))
            self.assertEqual([], bad, "%s 仍有 %s.request(...) 假通路（行号 %r）"
                             % (rel, "bus", bad))

    def test_32_kal_methods_use_entry_helper(self):
        """`_m70_kal_get_node` 方法体里必须出现 `_m420_kal_entry`（说明真接了 KAL）。"""
        for rel, cls_name in self._FILES:
            p = os.path.join(ROOT, rel.replace("/", os.sep))
            with open(p, encoding="utf-8") as f:
                _text = f.read()
            _tree = ast.parse(_text, filename=rel)
            _node = _find_func(_tree, "_m70_kal_get_node", cls_name)
            self.assertIsNotNone(_node, "%s 缺 _m70_kal_get_node" % cls_name)
            # ★同一份 _text 取片段：get_source_segment 依赖 col_offset/end_lineno，
            #   换一份重新 parse 的树在行号上虽等价，但一旦文件被改就会出现
            #   「片段取自 A、节点取自 B」的静默错位。
            _src = ast.get_source_segment(_text, _node) or ""
            self.assertIn("_m420_kal_entry", _src,
                          "%s::_m70_kal_get_node 没有走 _m420_kal_entry" % cls_name)

    def test_33_kal_entry_body_has_no_bare_raise(self):
        """★418 教训 #40 的可执行版：取 KAL 的辅助方法**自身不许向外抛**
        （否则「拿不到 KAL」会升级成「整个方法崩」，外层就没有回落机会了）。"""
        for rel, cls_name in self._FILES:
            _node = _find_func(self._tree(rel), "_m420_kal_entry", cls_name)
            self.assertIsNotNone(_node, "%s 缺 _m420_kal_entry" % cls_name)
            _raises = [n.lineno for n in ast.walk(_node) if isinstance(n, ast.Raise)]
            self.assertEqual([], _raises, "%s::_m420_kal_entry 里有 raise（行 %r）"
                             % (cls_name, _raises))


def _find_func(tree, name, cls_name):
    """在指定类里找同名 FunctionDef（找不到返回 None）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and sub.name == name:
                    return sub
    return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
