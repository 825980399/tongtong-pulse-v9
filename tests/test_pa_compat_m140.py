# -*- coding: utf-8 -*-
"""T-140a 回归测试：pyarrow 跨版本兼容垫片 + 全库 from_pylist 清零。

背景：pyarrow 25.0.1 移除了 ``pa.Table.from_pylist``，历史代码直接调用会抛
``AttributeError: type object 'Table' has no attribute 'from_pylist'``，导致
Parquet 快照无法保存。本测试锁定三件事：
  1. ``table_from_rows`` 在任何 pyarrow 版本下都能产出等价 Table（六场景）。
  2. 源码区（nucleus/ + tests/）不再有 ``pa.Table.from_pylist`` 调用。
  3. 端到端：``table_from_rows`` 写盘后可被 ``pq.read_table`` 读回。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from nucleus.mnemosyne.pa_compat import table_from_rows  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPaCompat(unittest.TestCase):
    """pa_compat.table_from_rows 六场景。"""

    def test_01_basic_no_schema(self):
        t = table_from_rows([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
        # 注意：pyarrow 25.x 中 pa.Table 与 pyarrow.lib.Table 是不同类对象，
        # isinstance(x, pa.Table) 不可靠，故按类名判定。
        self.assertEqual(type(t).__name__, "Table")
        self.assertTrue(t.__class__.__module__.startswith("pyarrow"))
        self.assertEqual(t.num_rows, 2)
        self.assertEqual(t.schema.names, ["a", "b"])

    def test_02_empty_rows(self):
        t = table_from_rows([])
        self.assertEqual(t.num_rows, 0)
        self.assertEqual(t.num_columns, 0)

    def test_03_schema_fills_missing(self):
        sch = pa.schema([("a", pa.int64()), ("b", pa.string()), ("c", pa.float64())])
        t = table_from_rows([{"a": 1, "b": "x"}], schema=sch)
        self.assertEqual(t.schema.names, ["a", "b", "c"])
        self.assertEqual(t.to_pylist(), [{"a": 1, "b": "x", "c": None}])

    def test_04_write_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            pq.write_to_dataset(
                table_from_rows([{"a": 1, "b": "x"}]),
                root_path=td, partition_cols=["b"], compression="snappy")
            self.assertEqual(pq.read_table(td).to_pylist(), [{"a": 1, "b": "x"}])

    def test_05_empty_row_with_schema(self):
        """IndexStore 的空表标记路径：``[{}]`` + schema。"""
        sch = pa.schema([("k", pa.string())])
        t = table_from_rows([{}], schema=sch)
        self.assertEqual(t.num_rows, 1)
        self.assertEqual(t.schema.names, ["k"])

    def test_06_empty_row_no_schema(self):
        t = table_from_rows([{}])
        self.assertEqual(t.num_rows, 0)

    def test_07_matches_native_semantics(self):
        """与旧版 ``Table.from_pylist`` 语义对齐（若该 API 存在则直接比对）。"""
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        native = getattr(pa.Table, "from_pylist", None)
        if native is None:
            self.skipTest("当前 pyarrow 无 Table.from_pylist，跳过原生比对")
        self.assertEqual(table_from_rows(rows).to_pylist(), native(rows).to_pylist())


class TestNoResidualFromPylist(unittest.TestCase):
    """源码区不得残留 ``pa.Table.from_pylist``（pa_compat 自身除外）。"""

    def test_01_no_table_from_pylist_in_source(self):
        _skip_dirs = {".git", "__pycache__", "tmp", "node_modules", ".venv", "data", "logs"}
        offenders = []
        for _root in ("nucleus", "tests", "functions", "organs", "utils", "tools", "core"):
            _base = os.path.join(_ROOT, _root)
            if not os.path.isdir(_base):
                continue
            for dp, dn, fn in os.walk(_base):
                dn[:] = [d for d in dn if d not in _skip_dirs]
                for f in fn:
                    if not f.endswith(".py"):
                        continue
                    if f in ("pa_compat.py", "test_pa_compat_m140.py"):
                        continue
                    p = os.path.join(dp, f)
                    try:
                        s = open(p, encoding="utf-8", errors="ignore").read()
                    except Exception:
                        continue
                    if "Table.from_pylist" in s:
                        offenders.append(os.path.relpath(p, _ROOT))
        self.assertEqual(offenders, [], f"仍有文件调用 pa.Table.from_pylist: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
