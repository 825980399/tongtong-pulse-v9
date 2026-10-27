# -*- coding: utf-8 -*-
"""372 刀9 定向单测：B 堆 25 条开关错位分档的**产物一致性**与**生产默认值零改动**。

对应任务书「定向单测」三条：
  ① 22 条对齐后转绿、条数与清单一致 1 例；
  ② 3 口子书面结论齐备（豁免/另立票二选一）1 例；
  ③ 生产默认值零改动断言 1 例。

被测对象是**证据件**（机读 JSON）＋**config 现态**，不重复跑那 25 个用例本身
（全文件复跑结果记在证据件 `verify_runs`，见交付报告）。这样单测本身是秒级的，
且能持续防三类回退：
  - 证据件条数被改小/漏项（→ ①）
  - 口子结论被写成空话或悄悄改成「另立票」却没落票（→ ②）
  - 有人事后把开关默认值改回 False 再说「对齐完成」（→ ③）
"""
import io
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                    "刀9_B25开关错位分档_20261026.json")
ARCHIVE = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "369",
                       "刀3_既有134失败三档归档_20261026.json")


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestB25Evidence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._ev = _load(EVID)
        cls._ar = _load(ARCHIVE)

    # ------------------------------------------------------------ ① 条数一致
    def test_01_counts_match_archive_and_rows_complete(self):
        rows = self._ev["rows"]
        # 与归档 B 堆逐 id 一一对应（不是「数量碰巧相等」，而是集合相等）
        _arch_ids = sorted(r["id"] for r in self._ar["rows"] if r.get("tier") == "B")
        _ev_ids = sorted(r["id"] for r in rows)
        self.assertEqual(_arch_ids, _ev_ids, "证据件 id 集合与归档 tier=B 不一致")

        _aligned = [r for r in rows if r["status"] == "aligned_green"]
        _slots = [r for r in rows if r["status"] == "slot_concluded"]
        self.assertEqual(22, len(_aligned), "「对齐产品现态」应为 22 条")
        self.assertEqual(3, len(_slots), "「口子逐条查」应为 3 条")
        self.assertEqual(len(rows), len(_aligned) + len(_slots), "分档之和须等于总量")
        self.assertEqual(25, len(rows))
        self.assertEqual({"total": 25, "aligned_green": 22, "slot_concluded": 3},
                         self._ev["counts"], "counts 汇总与 rows 实际不符")

        # 每行要素齐备，且用例真在仓库里（防伪造 nodeid）
        for r in rows:
            for _k in ("nodeid", "file", "case", "product_basis", "action"):
                self.assertTrue(str(r.get(_k) or "").strip(), "行 %s 缺字段 %s" % (r["id"], _k))
            _fp = os.path.join(ROOT, r["file"])
            self.assertTrue(os.path.isfile(_fp), "行 %s 的用例文件不存在：%s" % (r["id"], r["file"]))
            with io.open(_fp, encoding="utf-8", errors="ignore") as fh:
                _src = fh.read()
            _meth = r["case"].split("::")[-1]
            self.assertIn("def " + _meth + "(", _src,
                          "行 %s 的用例 %s 在文件中不存在" % (r["id"], _meth))

    # ------------------------------------------------------------ ② 口子结论
    def test_02_three_slots_have_written_conclusions(self):
        _slots = [r for r in self._ev["rows"] if r["status"] == "slot_concluded"]
        self.assertEqual(3, len(_slots), "口子必须恰好 3 个")
        _new_tickets = 0
        for r in _slots:
            _c = r.get("slot_conclusion")
            self.assertIn(_c, ("豁免", "另立改代码票"),
                          "口子 %s 结论须二选一（现为 %r）" % (r["id"], _c))
            _why = str(r.get("slot_reason") or "").strip()
            self.assertGreaterEqual(len(_why), 20,
                                    "口子 %s 结论理由过薄（须写清依据，不能只写『已查』）" % r["id"])
            if _c == "另立改代码票":
                _new_tickets += 1
        # 本刀书面结论：3 个口子全部豁免 ⇒ 另立改代码票 0 张（与证据件 §三 一致）
        self.assertEqual(0, _new_tickets, "若确证真缺陷须另立票并同步改证据件 §三 合计")

    # ------------------------------------------------------------ ③ 生产默认值
    def test_03_production_defaults_untouched(self):
        """12 个相关开关的**现态取值**必须等于本刀落盘时的值（本刀未改 config.py）。"""
        import config as _cfg

        for row in self._ev["switch_state"]:
            _name, _want = row["name"], row["value"]
            if _name.startswith("EVOLUTION_CONFIG["):
                _key = _name.split("'")[1]
                _got = _cfg.EVOLUTION_CONFIG.get(_key)
            else:
                _got = getattr(_cfg, _name, None)
            self.assertIs(_want, _got,
                          "开关 %s 现态 %r != 本刀记录 %r（生产默认值被改动？）" % (_name, _got, _want))
        self.assertEqual(12, len(self._ev["switch_state"]), "开关现态表应为 12 键")


if __name__ == "__main__":
    unittest.main(verbosity=2)
