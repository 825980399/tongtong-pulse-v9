# -*- coding: utf-8 -*-
"""420/L08 · 线程 spawn 门禁「文件级 .bak 过滤」判据（第420批 刀L08）。

## 修的是什么

`tools/ci/check_thread_spawn_baseline.py::scan()` 此前只过滤 **目录**：

```python
dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".bak")]
```

而**文件名不在 `dirs` 里** ⇒ 归档目录下的单文件备份
`docs/台账/历史备份归档/.bak_batch347_PulseController.py` 一直被计进基线。
caliber 原文写的是 `excluding .bak* (all batch backups)`，**实现与口径不一致**。

实测：非 `tests/`、`data/`、`tmp/` 下的 `.bak` 单文件共 3 个
（`.bak_batch347_EvolutionLoop.py` / `.bak_batch347_PulseController.py` /
`.bak_batch350_ap.py`），只有 PulseController 那个含 1 次 `threading.Thread(`
⇒ 基线虚高 **1 次 / 1 文件**（64/48 → 63/47）。

## 断言策略（为什么不全钉精确值）

铁律 9：钉死精确的 `occurrences` 会被「新增一个正常文件」顶红，那不是回归。
所以：
  * 过滤逻辑用**最小项目夹具**测（数字完全可控）；
  * 真实仓库只断言**具名清单**（`.bak` 一个都不许进 / `tests/` 一个都不许进）；
  * 基线自身数字只断言**下限 + 与 scan 一致**（重锚后仍成立）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ci import check_thread_spawn_baseline as gate  # noqa: E402

_SPAWN = "t = threading.Thread(target=f)\n"


class TestA_MinimalProjectFilter(unittest.TestCase):
    """A 组：最小项目夹具 —— 过滤规则逐条落判据。"""

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        self._old_root = gate.REPO_ROOT
        gate.REPO_ROOT = self.root

    def tearDown(self):
        gate.REPO_ROOT = self._old_root
        self._td.cleanup()

    def _w(self, rel, n=1):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(_SPAWN * n)
        return p

    def _scan(self):
        return gate.scan()

    def test_01_bak_file_at_root_is_excluded(self):
        """根目录 `.bak_x.py` 含 5 次 ⇒ 必须一次都不计。"""
        self._w("a.py", 2)
        self._w(".bak_x.py", 5)
        _t, _f, _rows = self._scan()
        self.assertEqual(2, _t, "根目录 .bak 文件被计入了：%r" % (_rows,))
        self.assertEqual(1, _f)
        self.assertEqual(["a.py"], [r[0] for r in _rows])

    def test_02_bak_file_in_subdir_is_excluded(self):
        """子目录 `.bak_y.py` 含 3 次 ⇒ 必须一次都不计（这是历史漏网的真实形态）。"""
        self._w("pkg/a.py", 1)
        self._w("pkg/.bak_y.py", 3)
        _t, _f, _rows = self._scan()
        self.assertEqual(1, _t)
        self.assertEqual(1, _f)
        self.assertEqual(["pkg/a.py"], [r[0] for r in _rows])

    def test_03_bak_directory_is_excluded(self):
        """`.bak_dir/` 整个目录仍要被排（既有行为回归，别改坏）。"""
        self._w("a.py", 1)
        self._w(".bak_dir/c.py", 4)
        _t, _f, _rows = self._scan()
        self.assertEqual(1, _t, "目录级 .bak 过滤失效：%r" % (_rows,))

    def test_04_tests_dir_is_excluded(self):
        """`tests/` 下不计（既有行为回归）。"""
        self._w("a.py", 1)
        self._w("tests/t_spawn.py", 9)
        self._w("tests/sub/t2.py", 4)
        _t, _f, _rows = self._scan()
        self.assertEqual(1, _t, "tests/ 过滤失效：%r" % (_rows,))

    def test_05_runtime_dirs_are_excluded(self):
        """`data/` `tmp/` 下不计（既有行为回归）。"""
        self._w("a.py", 1)
        self._w("data/d.py", 8)
        self._w("tmp/e.py", 7)
        _t, _f, _rows = self._scan()
        self.assertEqual(1, _t, "data/tmp 过滤失效：%r" % (_rows,))

    def test_06_normal_files_still_counted(self):
        """★不误伤：正常文件名（含 `.bak` 出现在中间而非开头）必须照常计入。"""
        self._w("a.py", 1)
        self._w("keep.bak.py", 2)       # 不以 .bak 开头 ⇒ 是正常文件
        self._w("my.bakery.py", 3)      # 同上
        _t, _f, _rows = self._scan()
        self.assertEqual(6, _t, "文件级过滤误伤了正常文件：%r" % (_rows,))
        self.assertEqual(3, _f)
        names = sorted(r[0] for r in _rows)
        self.assertIn("keep.bak.py", names)
        self.assertIn("my.bakery.py", names)

    def test_07_non_py_bak_is_irrelevant(self):
        """非 .py 文件本来就不计（`.bak_x.txt` 不该造成任何影响）。"""
        self._w("a.py", 1)
        p = os.path.join(self.root, ".bak_x.txt")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(_SPAWN * 6)
        _t, _f, _rows = self._scan()
        self.assertEqual(1, _t)


class TestB_RealRepoGuard(unittest.TestCase):
    """B 组：真实仓库守门 —— 口径一致性必须可执行。"""

    def test_10_no_bak_file_in_per_file(self):
        """★L08 的正面判据：真实仓库的 per_file 里，**没有任何**文件名以 `.bak` 开头。"""
        _t, _f, rows = gate.scan()
        bad = [r[0] for r in rows if os.path.basename(r[0]).startswith(".bak")]
        self.assertEqual([], bad, "仍有 .bak 单文件被计入基线：%r" % (bad,))

    def test_11_no_tests_path_in_per_file(self):
        _t, _f, rows = gate.scan()
        bad = [r[0] for r in rows if r[0].startswith("tests/")]
        self.assertEqual([], bad, "tests/ 下文件被计入：%r" % (bad,))

    def test_12_per_file_paths_are_repo_relative_and_exist(self):
        """★铁律 29 同类守门：scan() 产出的相对路径必须能按 REPO_ROOT 解析到真文件。

        （`scan()` 内部用的是绝对 `full` 读文件、只把 `rel` 吐出来；若将来有人
        改成吐出按 cwd 解析的相对路径，这条会立刻红。）
        """
        _t, _f, rows = gate.scan()
        missing = []
        for rel, _n in rows:
            p = os.path.join(gate.REPO_ROOT, rel.replace("/", os.sep))
            if not os.path.isfile(p):
                missing.append(rel)
        self.assertEqual([], missing, "per_file 相对路径解析不到真文件：%r" % (missing,))

    def test_13_counts_are_consistent_with_rows(self):
        """`total_occurrences` 必须等于逐文件计数之和，`file_count` 等于行数。"""
        t, f, rows = gate.scan()
        self.assertEqual(sum(r[1] for r in rows), t)
        self.assertEqual(len(rows), f)

    def test_14_caliber_declares_bak_exclusion(self):
        """基线文件的 caliber 文本必须声明排除 .bak —— 口径与实现同源。"""
        import json
        with open(gate.BASELINE_PATH, encoding="utf-8") as fh:
            base = json.load(fh)
        self.assertIn(".bak", str(base.get("caliber") or ""),
                      "caliber 没声明 .bak 排除，实现与口径会再次脱节")
        # ★宽松边界（不钉精确值）：只挡"被清空成 0"与"单位写错"这两类事故
        self.assertTrue(20 <= int(base.get("total_occurrences") or 0) <= 4000,
                        "基线 occurrences 越界（多半是被清空或单位写错）：%r"
                        % (base.get("total_occurrences"),))
        self.assertTrue(10 <= int(base.get("file_count") or 0) <= 4000,
                        "基线 file_count 越界：%r" % (base.get("file_count"),))

    def test_15_baseline_matches_scan(self):
        """★门禁自身语义：存量基线与当前扫描一致（重锚后仍成立）。

        这是门禁的**核心承诺**，不是"照抄实现"——基线是外部数据，scan 是计算，
        两者相等才有"红线未被挪动"的保证。
        """
        import json
        with open(gate.BASELINE_PATH, encoding="utf-8") as fh:
            base = json.load(fh)
        t, f, _rows = gate.scan()
        self.assertEqual(int(base["total_occurrences"]), t,
                         "基线与当前扫描不符 ⇒ 要么该重锚，要么有人新增了 spawn 点")
        self.assertEqual(int(base["file_count"]), f)

    def test_16_reanchor_is_signed(self):
        """重锚必须带署名与归因（337刀11 / 340刀5 的纪律，本刀继续守）。"""
        import json
        with open(gate.BASELINE_PATH, encoding="utf-8") as fh:
            base = json.load(fh)
        self.assertTrue(str(base.get("reanchor_by") or "").strip(),
                        "重锚缺 reanchor_by 署名")
        self.assertTrue(str(base.get("reanchor_at") or "").strip(),
                        "重锚缺 reanchor_at 日期")
        att = base.get("attribution") or []
        self.assertTrue(att, "重锚缺 attribution 归因")
        for a in att:
            for k in ("delta_occurrences", "delta_files", "reason"):
                self.assertIn(k, a, "归因项缺字段 %s：%r" % (k, a))
            self.assertTrue(str(a.get("reason") or "").strip(), "归因 reason 为空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
