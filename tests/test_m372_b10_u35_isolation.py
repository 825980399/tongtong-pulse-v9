# -*- coding: utf-8 -*-
"""372 刀10 定向单测：U 档 5 条「取数丢失」的隔离产物 + 隔离夹具不污染 + 生产路径零改。

对应任务书「定向单测」三条：
  ① 5 条明细取得并归档 1 例；
  ② 隔离夹具不污染其他用例 1 例；
  ③ 生产取数路径零 diff 1 例。

③ 刻意**不断言文件字节**（锁 sha 会变成「改个注释就红」的脆弱断言），
   改为断言 `_enforce_cold_cache` 的**分派语义**仍与开关一致 —— 这正是本刀
   承诺「没碰生产取数/驱逐路径」的可执行证据。
"""
import ast
import io
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                    "刀10_U35取数隔离_20261026.json")
ARCHIVE = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "369",
                       "刀3_既有134失败三档归档_20261026.json")
KNIFE3 = os.path.join(ROOT, "tests", "test_knife3_cold_index_m162.py")


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestU35Isolation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._ev = _load(EVID)
        cls._ar = _load(ARCHIVE)

    # -------------------------------------------------- ① 5 条明细并归档
    def test_01_five_rows_archived_with_real_findings(self):
        rows = self._ev["rows"]
        _want = sorted(r["id"] for r in self._ar["rows"]
                       if r.get("tier") == "U" and "m162" in r["file"])
        self.assertEqual(5, len(_want), "归档里 U 档 m162 条目数应为 5")
        self.assertEqual(_want, sorted(r["id"] for r in rows),
                         "证据件 id 集合与归档 U 档 m162 不一致")

        for r in rows:
            for _k in ("real_finding", "tier", "root_cause", "isolation", "result_now"):
                self.assertTrue(str(r.get(_k) or "").strip(),
                                "行 %s 缺字段 %s（取数丢失的条目就是靠这几列才算「取到明细」）"
                                % (r["id"], _k))
            # 归档原文必须留着，否则无法证明「原来是取数丢失」
            self.assertIn("取数丢失", str(r.get("archive_reason") or ""),
                          "行 %s 的归档理由不是取数丢失，混入了别的档" % r["id"])

        _green = [r for r in rows if r["result_now"].startswith("通过")]
        _red = [r for r in rows if r["result_now"].startswith("仍红")]
        self.assertEqual(len(rows), len(_green) + len(_red), "定档之和须等于总量")
        self.assertEqual({"total": 5, "got_green": len(_green), "still_red": len(_red),
                          "new_tickets": 1}, self._ev["counts"],
                         "counts 汇总与 rows 实际不符")

    # -------------------------------------------------- ② 夹具不污染
    def test_02_isolation_is_fixture_scoped_not_module_level(self):
        """隔离必须落在用例内（monkeypatch 夹具），不能是模块级 setattr 污染全局。"""
        import config as _cfg

        # a) 模块导入本身不得改全局开关（模块级 setattr 的典型症状）
        self.assertIs(True, bool(getattr(_cfg, "ENABLE_COLD_POOL_PERCENTILE_SHRINK", None)),
                      "模块导入后全局开关被改了 ⇒ 隔离发生了泄漏")

        # b) 两条隔离用例必须显式接收 monkeypatch 夹具（函数级、自动还原）
        with io.open(KNIFE3, encoding="utf-8", errors="ignore") as fh:
            _src = fh.read()
        _tree = ast.parse(_src.replace("\r\n", "\n"))
        _fn = {n.name: n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)}
        for _name in ("test_knife3_enforce_cold_cache_not_blocked_by_throttle",
                      "test_knife3_eviction_rebuild_closure"):
            self.assertIn(_name, _fn, "用例名变了：%s" % _name)
            _args = [a.arg for a in _fn[_name].args.args]
            self.assertIn("monkeypatch", _args,
                          "%s 未用 monkeypatch 夹具 ⇒ 隔离可能不是函数级" % _name)
            # 该函数体内必须真的 setattr 了分位开关
            self.assertIn("ENABLE_COLD_POOL_PERCENTILE_SHRINK",
                          ast.get_source_segment(_src.replace("\r\n", "\n"), _fn[_name]) or "",
                          "%s 未显式关分位开关，等于没隔离" % _name)

    # -------------------------------------------------- ③ 生产路径零改
    def test_03_production_dispatch_semantics_unchanged(self):
        """`_enforce_cold_cache` 仍按开关分派：True→分位路径，False→LRU 路径。"""
        import config as _cfg
        from nucleus.mnemosyne.PulseNodePool import PulseNodePool

        pool = PulseNodePool.__new__(PulseNodePool)
        pool._cold_storage_enabled = True
        _calls = []
        pool._enforce_cold_cache_percentile = lambda: _calls.append("pct")
        pool._enforce_cold_cache_lru = lambda: _calls.append("lru")

        _saved = getattr(_cfg, "ENABLE_COLD_POOL_PERCENTILE_SHRINK", None)
        try:
            _cfg.ENABLE_COLD_POOL_PERCENTILE_SHRINK = True
            pool._enforce_cold_cache()
            self.assertEqual(["pct"], _calls, "开关开启时应走分位驱逐路径")
            _calls.clear()
            _cfg.ENABLE_COLD_POOL_PERCENTILE_SHRINK = False
            pool._enforce_cold_cache()
            self.assertEqual(["lru"], _calls, "开关关闭时应走固定阈值 LRU 路径")
        finally:
            _cfg.ENABLE_COLD_POOL_PERCENTILE_SHRINK = _saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
