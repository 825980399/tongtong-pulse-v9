# -*- coding: utf-8 -*-
"""第431批 刀1：语义关系字符串共享池（无损内存瘦身）。

被守护的八条：
  1) **值逐字不变** —— 池化只换对象同一性，`==` / 排序 / 聚合结果一字不差；
  2) 重复取值**真的被复用**（同一 target_node_id 两次出现 ⇒ 同一对象）；
  3) 非 dict 元素 / 非 str 值原样保留（不许把 int/None 改成 str）；
  4) 关闭开关 ⇒ 逐字回到改动前（一对象一实例，不池化）；
  5) 空/None/非 list 入参 ⇒ 原样返回，不抛；
  6) 池达上限 ⇒ 一次性停用并把 `disabled` 置 True（防常驻单调上涨）；
  7) 只处理 list[dict]，不递归、不改 list 结构长度与顺序；
  8) `from_dict` 走这条链（不是只在单测里直调工具函数）。

★不用 `sys.intern` 的理由写在实现注释里：那是进程级永生表，
  本库 target_node_id 有上万个不同取值且会持续新增，用它等于造泄漏面。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.mnemosyne import PulseNode as pn


def _rels(*targets, rt="semantic_similarity", src="liver_semantic"):
    return [{"target_node_id": t, "relation_type": rt, "weight": 0.5,
             "source": src, "established_at": 1.0, "updated_at": 2.0}
            for t in targets]


class TestM431D1ShareRelations(unittest.TestCase):
    def setUp(self):
        self._on = pn._M431_SR_POOL.clear
        pn._M431_SR_POOL.clear()
        pn._M431_SR_POOL_OFF = False
        self._saved_switch = pn._m431_intern_enabled
        self._saved_max = pn._M431_SR_POOL_MAX

    def tearDown(self):
        pn._M431_SR_POOL.clear()
        pn._M431_SR_POOL_OFF = False
        pn._m431_intern_enabled = self._saved_switch
        pn._M431_SR_POOL_MAX = self._saved_max

    def _on_switch(self, on):
        pn._m431_intern_enabled = lambda: bool(on)

    # ---- 1) 值逐字不变 ----
    def test_01_values_stay_identical(self):
        _in = _rels("node:a", "node:b", "node:a")
        _snapshot = [dict(x) for x in _in]
        _out = pn._m431_share_relations(_in)
        self.assertEqual(len(_out), len(_snapshot))
        for _a, _b in zip(_out, _snapshot):
            self.assertEqual(_a, _b, "池化后值必须逐字相同")
            self.assertEqual(sorted(_a.keys()), sorted(_b.keys()), "键集合不得变")

    # ---- 2) 重复取值真的共用一个对象 ----
    def test_02_duplicates_share_one_object(self):
        self._on_switch(True)
        _out = pn._m431_share_relations(_rels("node:a", "node:b", "node:a"))
        _ids = [id(x["target_node_id"]) for x in _out]
        self.assertIs(_out[0]["target_node_id"], _out[2]["target_node_id"],
                      "同一取值必须复用同一对象")
        self.assertNotEqual(_ids[0], _ids[1], "不同取值不该被合并")
        # 枚举字段同理
        self.assertIs(_out[0]["relation_type"], _out[1]["relation_type"])

    # ---- 3) 非 str 值原样 ----
    def test_03_non_str_values_untouched(self):
        self._on_switch(True)
        _in = [{"target_node_id": "node:a", "weight": 0.75, "flag": True,
                "none": None, "n": 3}]
        _out = pn._m431_share_relations(_in)
        _r = _out[0]
        self.assertEqual(_r["weight"], 0.75)
        self.assertIsInstance(_r["weight"], float, "数值不许变成 str")
        self.assertIs(_r["flag"], True)
        self.assertIsNone(_r["none"])
        self.assertEqual(_r["n"], 3)
        self.assertIsInstance(_r["n"], int)

    # ---- 4) 关开关 = 逐字回到改动前 ----
    def test_04_switch_off_is_noop(self):
        self._on_switch(False)
        _in = _rels("node:a", "node:a")
        _out = pn._m431_share_relations(_in)
        self.assertEqual(_out, _in)
        # 关开关时不该往池里塞任何东西
        self.assertEqual(len(pn._M431_SR_POOL), 0, "关开关时池必须为空")

    # ---- 5) 空/异常入参 ----
    def test_05_empty_and_bad_input(self):
        self._on_switch(True)
        for _bad in (None, [], (), "notalist", 0):
            self.assertEqual(pn._m431_share_relations(_bad), _bad,
                             f"非 list / 空入参必须原样返回：{_bad!r}")

    # ---- 6) 池上限 ⇒ 停用 ----
    def test_06_pool_cap_disables_pool(self):
        self._on_switch(True)
        pn._M431_SR_POOL_MAX = 3
        pn._m431_share_relations(_rels("t1", "t2", "t3", "t4", "t5"))
        self.assertLessEqual(len(pn._M431_SR_POOL), 3)
        self.assertTrue(pn._M431_SR_POOL_OFF, "达到上限后必须置停用标记")
        # 停用后仍要能正常返回值（只是不再共享）
        _r = pn._m431_share_relations(_rels("t9", "t9"))
        self.assertEqual(len(_r), 2)
        self.assertEqual(_r[0]["target_node_id"], "t9")

    # ---- 7) 不改结构 ----
    def test_07_structure_preserved(self):
        self._on_switch(True)
        _in = _rels("a", "b", "c") + ["raw-string-item", 42]
        _out = pn._m431_share_relations(_in)
        self.assertEqual(len(_out), 5, "长度不得变")
        self.assertEqual(_out[3], "raw-string-item", "非 dict 元素原样保留")
        self.assertEqual(_out[4], 42)
        self.assertEqual([x["target_node_id"] for x in _out[:3]], ["a", "b", "c"],
                         "顺序不得变")

    # ---- 8) from_dict 真走这条链 ----
    def test_08_from_dict_uses_pool(self):
        self._on_switch(True)
        pn._M431_SR_POOL.clear()
        _data = {
            "node_id": "node:self", "value": "v", "space_path": ["a"],
            "evol_level": "L3",
            "semantic_relations": [
                {"target_node_id": "node:x", "relation_type": "causal",
                 "weight": 0.1, "source": "s"},
                {"target_node_id": "node:x", "relation_type": "causal",
                 "weight": 0.2, "source": "s"},
            ],
        }
        _n = pn.PulseNode.from_dict(_data)
        self.assertEqual(len(_n.semantic_relations), 2)
        self.assertIs(_n.semantic_relations[0]["target_node_id"],
                      _n.semantic_relations[1]["target_node_id"],
                      "from_dict 必须走共享池（同一 target 应是同一对象）")
        self.assertGreater(len(pn._M431_SR_POOL), 0, "from_dict 必须真的用池")


if __name__ == "__main__":
    unittest.main()
