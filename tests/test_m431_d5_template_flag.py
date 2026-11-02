# -*- coding: utf-8 -*-
"""第431批 刀5：经验池 is_template 覆盖率封顶。

★T0 纠偏两处：
  ① 任务书「405 条记录」→ 实测 **343 条**（405 是 407 批时的数）。
  ② 任务书「is_template 字段 0 覆盖、全是非模板」→ 实测**已有 200 条 True**
     （412 批 `mark_templates` 跑过），缺 143 条**没有这个键**。

根因：`annotate()` 只回写**差异条目**（412/413 的取舍，避免全量白写 False），
所以"从没被标过"的条目在盘上没有这个键，覆盖率永远到不了 100%，
下游 `exp.get("is_template")` 拿到 None 而不是 False。

被守护的七条：
  1) `record_experience` 入池**自带** `is_template=False`（新条目从此有键）；
  2) 缺键与 False **语义等价**（`is_template()` 判据不变）；
  3) 补齐只动**缺键**的条目，已有键的一个字节都不改；
  4) `annotate()` 不会把已标的 True 翻成 False（纯增量）；
  5) 幂等：连跑两次，第二次 `changed=0`；
  6) 补齐后覆盖率 100%（have_key == total）；
  7) 打标质量：True 组的归一化键重复次数 ≥ TEMPLATE_MIN_DUP，False 组为 1。
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.observability import experience_governance as eg


def _exp(eid, text, **kw):
    d = {"id": eid, "summary": text, "motivation": "", "timestamp": 1.0}
    d.update(kw)
    return d


class _FakePool:
    """最小池替身：只实现 annotate 用到的两个口。"""

    def __init__(self, rows, path):
        self._rows = rows
        self._pool_file = path

    def query_experiences(self, limit=100000):
        return [dict(x) for x in self._rows[:limit]]

    def annotate_flags(self, flags):
        _n = 0
        for _r in self._rows:
            _u = flags.get(str(_r.get("id") or ""))
            if not _u:
                continue
            _hit = False
            for _k, _v in _u.items():
                if _v is None:
                    if _k in _r:
                        _r.pop(_k, None)
                        _hit = True
                elif _r.get(_k) != _v:
                    _r[_k] = _v
                    _hit = True
            _n += 1 if _hit else 0
        return _n

    def _save(self):
        with open(self._pool_file, "w", encoding="utf-8") as _f:
            json.dump({"experiences": self._rows}, _f, ensure_ascii=False)


class TestM431D5TemplateFlag(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.pool_file = os.path.join(self.tmp, "experience_pool.json")
        # 4 条重复文本（模板）+ 2 条唯一文本（非模板）
        self.rows = [
            _exp("e1", "重复模板文本A"),
            _exp("e2", "重复模板文本A"),
            _exp("e3", "重复模板文本A"),
            _exp("e4", "重复模板文本B"),
            _exp("e5", "重复模板文本B"),
            _exp("e6", "重复模板文本B"),
            _exp("u1", "这是一条独一无二的经验"),
            _exp("u2", "这又是一条独一无二的经验"),
        ]
        self.pool = _FakePool(self.rows, self.pool_file)
        self.pool._save()

    def tearDown(self):
        self._td.cleanup()

    # ---- 1) 入池自带 is_template=False ----
    def test_01_record_experience_sets_default(self):
        """★隔离：`ExperiencePool()` 的 base_dir 默认是**相对 cwd** 的
        `data/experience` ⇒ 在 PULSE_FRAMEWORK=1 下跑单测会直接写生产池。
        这里显式注入 tmp（`_m44_base_dir_explicit=True` 也是写盘守卫的放行判据）。
        """
        from nucleus.mnemosyne import experience_pool as ep

        _p = ep.ExperiencePool(base_dir=self.tmp)
        _id = _p.record_experience(motivation="测试", content="一条新经验")
        _rows = _p.query_experiences(limit=100000)
        _hit = [x for x in _rows if str(x.get("id")) == str(_id)]
        self.assertEqual(len(_hit), 1, "新记录应当能查回来")
        self.assertIn("is_template", _hit[0],
                      "431刀5：入池必须自带 is_template 键（否则覆盖率永远封不了顶）")
        self.assertIs(_hit[0]["is_template"], False, "新记录默认非模板")

    # ---- 2) 缺键 == False（语义等价）----
    def test_02_missing_key_equals_false(self):
        self.assertFalse(eg.is_template({"id": "x"}))
        self.assertFalse(eg.is_template({"id": "x", "is_template": False}))
        self.assertTrue(eg.is_template({"id": "x", "is_template": True}))
        self.assertFalse(eg.is_template(None))
        self.assertEqual(eg.retrieval_weight({"id": "x"}), 1.0, "缺键 => 不降权")
        self.assertEqual(eg.retrieval_weight({"id": "x", "is_template": True}), eg.TEMPLATE_WEIGHT)

    # ---- 3) 补齐只动缺键的 ----
    def test_03_backfill_only_touches_missing(self):
        # 先给 e1 手动标 True 且给个自定义 template_key（用来验证"不动已有键"）
        self.rows[0]["is_template"] = True
        self.rows[0]["template_key"] = "CUSTOM_KEEP_ME"
        _missing = [x for x in self.rows if "is_template" not in x]
        self.assertEqual(len(_missing), 7)
        _flags = {str(x["id"]): {"is_template": False} for x in _missing}
        _n = self.pool.annotate_flags(_flags)
        self.assertEqual(_n, 7)
        self.assertIs(self.rows[0]["is_template"], True, "已有 True 不得被改")
        self.assertEqual(self.rows[0]["template_key"], "CUSTOM_KEEP_ME",
                         "已有 template_key 不得被改")

    # ---- 4) annotate 纯增量：不把 True 翻 False ----
    def test_04_annotate_never_flips_true_to_false(self):
        # 把 8 条全标 True（人为制造"标多了"的局面）
        for _r in self.rows:
            _r["is_template"] = True
        _r = eg.annotate(pool=self.pool, dry_run=True)
        # dry_run 不会真改，但 changed 里应包含需要翻回 False 的那些
        self.assertGreaterEqual(_r["scanned"], 8)
        self.assertEqual(_r["template_rows"], 6, "只有两组重复文本算模板")
        # 真跑
        _r2 = eg.annotate(pool=self.pool, dry_run=False)
        _t = [x["id"] for x in self.rows if x.get("is_template") is True]
        self.assertEqual(sorted(_t), ["e1", "e2", "e3", "e4", "e5", "e6"],
                         "唯一文本的两条必须被翻回 False")
        self.assertTrue(_r2.get("ok"))

    # ---- 5) 幂等 ----
    def test_05_idempotent(self):
        eg.annotate(pool=self.pool, dry_run=False)
        _r2 = eg.annotate(pool=self.pool, dry_run=False)
        self.assertEqual(_r2["changed"], 0, "第二次跑必须零变化")
        self.assertEqual(_r2["reason"], "no_change")

    # ---- 6) 覆盖率封顶 ----
    def test_06_coverage_is_full(self):
        _flags = {str(x["id"]): {"is_template": False}
                  for x in self.rows if "is_template" not in x}
        self.pool.annotate_flags(_flags)
        eg.annotate(pool=self.pool, dry_run=False)
        self.pool._save()
        with open(self.pool_file, "r", encoding="utf-8") as _f:
            _disk = json.load(_f)["experiences"]
        _have = sum(1 for x in _disk if "is_template" in x)
        self.assertEqual(_have, len(_disk), "覆盖率必须 100%")

    # ---- 7) 打标质量 ----
    def test_07_quality_by_dup_count(self):
        eg.annotate(pool=self.pool, dry_run=False)
        from collections import Counter
        _c = Counter(eg.norm_key(x) for x in self.rows)
        for _x in self.rows:
            _dup = _c[eg.norm_key(_x)]
            if _x.get("is_template"):
                self.assertGreaterEqual(
                    _dup, eg.TEMPLATE_MIN_DUP,
                    f"标 True 的必须够重复次数：{_x['id']!r} dup={_dup}")
            else:
                self.assertLess(
                    _dup, eg.TEMPLATE_MIN_DUP,
                    f"标 False 的不该够重复：{_x['id']!r} dup={_dup}")


    # ---- 8) ★加载期补齐：缺键补 False、已有 True 不动 ----
    def test_08_load_backfills_missing_key(self):
        """431 整修：补齐必须在**加载路径**做。

        为什么：离线改盘会被活体进程的内存池整份覆盖（431 实测 1 分钟内退回）。
        加载期补 ⇒ 内存有键 ⇒ 后续任何一次 `_save()` 自动持久化。
        """
        from nucleus.mnemosyne import experience_pool as ep

        _disk = [
            _exp("a1", "老条目一"),                        # 缺键
            _exp("a2", "老条目二", is_template=True),       # 已判 True，必须保留
            _exp("a3", "老条目三", is_template=False),      # 已判 False
        ]
        with open(self.pool_file, "w", encoding="utf-8") as _f:
            json.dump({"experiences": _disk, "total_recorded": 3}, _f, ensure_ascii=False)

        _p = ep.ExperiencePool(base_dir=self.tmp)
        _rows = {str(x["id"]): x for x in _p.query_experiences(limit=100000)}
        self.assertIn("is_template", _rows["a1"], "缺键必须被补上")
        self.assertIs(_rows["a1"]["is_template"], False, "缺键按 False 计")
        self.assertIs(_rows["a2"]["is_template"], True, "已判 True 不得被翻")
        self.assertIs(_rows["a3"]["is_template"], False, "已有 False 保持")
        self.assertEqual(sum(1 for x in _rows.values() if "is_template" in x), 3,
                         "加载后覆盖率必须 100%")

    # ---- 9) 幂等 + 落盘带键 ----
    def test_09_load_backfill_is_idempotent(self):
        from nucleus.mnemosyne import experience_pool as ep

        _disk = [_exp("b1", "缺键条目"), _exp("b2", "另一个缺键")]
        with open(self.pool_file, "w", encoding="utf-8") as _f:
            json.dump({"experiences": _disk, "total_recorded": 2}, _f, ensure_ascii=False)

        _p = ep.ExperiencePool(base_dir=self.tmp)
        self.assertEqual(_p._m431_backfill_template_flag(), 0, "第二次补必须 0 条（幂等）")
        _p._save()
        with open(self.pool_file, encoding="utf-8") as _f:
            _back = json.load(_f)["experiences"]
        self.assertTrue(all("is_template" in x for x in _back),
                        "任何一次 _save() 后盘上必须全覆盖")


if __name__ == "__main__":
    unittest.main()
