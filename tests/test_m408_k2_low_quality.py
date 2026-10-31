# -*- coding: utf-8 -*-
"""408 刀2 判据：低质推理经验记录的判定 / 标记 / 回滚 / 工具端到端。

背景（408 T0 实测）
------------------
`data/context/reasoning_experience.json` 128 条，98 条已被 407 标为污染，
**30 条干净记录参与打分**，其中 13 条的 `example_question` 存的不是问题：
7 条是**回答正文**（`[自主推导参考]` …）、3 条搜索引擎串、2 条模板片段、1 条模型自述。

★为什么不按任务书原判据（"不以疑问词开头 / 不含问号"）：
  那是**缺席证据**。`你是否包含身份相关的知识`、`1+1 等于几`、`你是谁`、
  `现象：…请你分析` 都不带问号但都是真问题 ⇒ 一刀切会误删真经验。
  B 组专门守这些反例。

五组：
  A 判据（正面证据四类 + 反例不误判 + tier2 默认关）
  B 标记语义（写的全是既有字段 / 不碰 quality_* / 幂等 / dry-run 不动数据）
  C 回滚（只回滚本批本类，不伤第50批的标记）
  D 闸门联动（`is_retrievable` 与 `ReasoningExperience._m407_is_clean` 同结论）
  E 工具端到端（临时池：dry-run 不写 / apply 写 / restore 复原）
"""
from __future__ import annotations

import ast
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.data import experience_cleanup as ec                # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_ROOT, "tools", "m408_clean_low_quality_experience.py")


def _rec(q, **kw):
    _e = {"confidence": 0.3, "derivation_type": "unknown",
          "example_question": q, "features": {"length": len(q)},
          "source": "local_fallback", "timestamp": 1.0}
    _e.update(kw)
    return _e


# 408 实测里的 13 条代表样本（脱敏到能判定的前缀）
LEAK = "你好\n\n[自主推导参考] [归纳升华] 从「代码」领域的66个知识点中，发现…"
SERP = "site:baike.baidu.com 你好 本能、未分类 跨领域 类比 概念 原理 -拼音 -部首 -笔顺 -怎么读"
TPL = "内化'\"向善\"'本能——反思温柔"
SELF = "我需要更多的一手资料和实践来建立扎实的认知"

# 408 实测里**必须保留**的真问题（都不带问号 / 不以疑问词开头）
KEEP = (
    "你是否包含身份相关的知识",
    "1+1 等于几",
    "你是谁",
    "现象：曈曈内在世界连续运行几个小时之后，动机循环运行越来越慢。\n请你分析：\n1、列出 4 个原因",
    "请归纳\"知识压缩\"和\"知识融合\"的共同特点",
    "脉冲研究院下属的记忆基座档案馆由浅至深分为L0接待大厅、L1短期缓存区…",
)


# ------------------------------------------------------------------ A 判据
class TestA_Classify(unittest.TestCase):
    def test_01_answer_leak(self):
        self.assertEqual(ec.LQ_ANSWER_LEAK,
                         ec.classify_low_quality(_rec(LEAK)))

    def test_02_serp_query(self):
        self.assertEqual(ec.LQ_SERP_QUERY, ec.classify_low_quality(_rec(SERP)))

    def test_03_template_frag(self):
        self.assertEqual(ec.LQ_TEMPLATE_FRAG, ec.classify_low_quality(_rec(TPL)))

    def test_04_self_meta(self):
        self.assertEqual(ec.LQ_SELF_META, ec.classify_low_quality(_rec(SELF)))

    def test_05_real_questions_not_flagged(self):
        """★核心反例：真问题一条都不许判低质（这是"不用缺席证据"的证明）。"""
        _bad = [(q, ec.classify_low_quality(_rec(q))) for q in KEEP]
        _bad = [x for x in _bad if x[1]]
        self.assertEqual([], _bad, "真问题被误判：%s" % _bad)

    def test_06_tier2_off_by_default(self):
        """陈述句在 tier1 不判 —— 弱判据必须显式开。"""
        _q = "第零部分与四、精神宪法与技术宪法的对应关系的深层联系"
        self.assertEqual("", ec.classify_low_quality(_rec(_q)))
        self.assertEqual(ec.LQ_STATEMENT_ONLY,
                         ec.classify_low_quality(_rec(_q), tier2=True))

    def test_07_tier2_keeps_multiline_prompt(self):
        """带换行的长题干（背景/约束/现象）即使无问号也不判 —— 那是真推理题。"""
        _q = "背景：脉冲框架三条规则\n\n1. 只要共振引擎处于激活状态…\n2. 如果…"
        self.assertEqual("", ec.classify_low_quality(_rec(_q), tier2=True))

    def test_08_non_dict_and_empty(self):
        self.assertEqual("", ec.classify_low_quality(None))
        self.assertEqual("", ec.classify_low_quality({"example_question": ""}))


# ------------------------------------------------------------------ B 标记
class TestB_Mark(unittest.TestCase):
    def test_10_writes_only_existing_fields(self):
        _r = [_rec(LEAK)]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=123.0)
        self.assertIs(True, _r[0]["polluted"])
        self.assertEqual(ec.LQ_ANSWER_LEAK, _r[0]["pollution_reason"])
        self.assertIs(False, _r[0]["is_cleaned"])
        self.assertEqual("high", _r[0]["pollution_risk"])
        self.assertEqual(ec.CLASS_LOW_QUALITY_STATEMENT, _r[0]["cleanup_reason"])
        self.assertEqual(408, _r[0]["cleanup_batch"])
        self.assertEqual(123.0, _r[0]["cleanup_at"])

    def test_11_does_not_touch_quality_fields(self):
        """`quality_flag` / `quality_weight` 的取值语义属写入侧，清洗侧不硬填。"""
        _r = [_rec(LEAK, quality_flag="ok", quality_weight=0.9)]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=1.0)
        self.assertEqual("ok", _r[0]["quality_flag"])
        self.assertEqual(0.9, _r[0]["quality_weight"])

    def test_12_dry_run_changes_nothing(self):
        _r = [_rec(LEAK), _rec(KEEP[0])]
        _snap = json.dumps(_r, ensure_ascii=False, sort_keys=True)
        _rep = ec.mark_low_quality(_r, batch_no=408, dry_run=True)
        self.assertEqual(1, _rep["marked"])
        self.assertEqual(_snap, json.dumps(_r, ensure_ascii=False, sort_keys=True))

    def test_13_idempotent(self):
        _r = [_rec(LEAK)]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=1.0)
        _rep = ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=2.0)
        self.assertEqual(0, _rep["marked"])
        self.assertEqual(1, _rep["already_marked"])
        self.assertEqual(1.0, _r[0]["cleanup_at"], "重复跑不该刷时间戳")

    def test_14_by_sub_counts(self):
        _r = [_rec(LEAK), _rec(SERP), _rec(TPL), _rec(SELF), _rec(KEEP[0])]
        _rep = ec.mark_low_quality(_r, batch_no=408, dry_run=True)
        self.assertEqual(4, _rep["marked"])
        self.assertEqual({"answer_leak": 1, "serp_query": 1,
                          "template_frag": 1, "self_meta": 1}, _rep["by_sub"])


# ------------------------------------------------------------------ C 回滚
class TestC_Restore(unittest.TestCase):
    def test_20_restore_returns_original_keys(self):
        _orig = _rec(LEAK)
        _keys = sorted(_orig)
        _r = [dict(_orig)]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=1.0)
        _rep = ec.restore_low_quality(_r, batch_no=408)
        self.assertEqual(1, _rep["restored"])
        self.assertEqual(_keys, sorted(_r[0]), "回滚后字段集必须回到原样")

    def test_21_restore_other_class_untouched(self):
        """★第50批 `mark_polluted` 打的标记（别的 cleanup_reason）不许被本刀回滚。"""
        _p50 = _rec("旧污染条", polluted=True, is_cleaned=False,
                    pollution_risk="high", cleanup_reason=ec.CLASS_OTHER,
                    cleanup_batch=50, cleanup_at=9.0)
        _r = [dict(_p50), _rec(LEAK)]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=1.0)
        ec.restore_low_quality(_r, batch_no=408)
        self.assertEqual(_p50, _r[0], "第50批标记被本刀动了")

    def test_22_restore_wrong_batch_noop(self):
        _r = [_rec(LEAK)]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=1.0)
        self.assertEqual(0, ec.restore_low_quality(_r, batch_no=409)["restored"])
        self.assertIs(True, _r[0]["polluted"])


# ------------------------------------------------------------------ D 闸门
class TestD_Gate(unittest.TestCase):
    def test_30_marked_not_retrievable(self):
        _r = _rec(LEAK)
        self.assertTrue(ec.is_retrievable(_r))
        ec.mark_low_quality([_r], batch_no=408, dry_run=False, now=1.0)
        self.assertFalse(ec.is_retrievable(_r))

    def test_31_same_verdict_as_reasoning_gate(self):
        """★口径一致：`is_retrievable` 与 `ReasoningExperience._m407_is_clean` 同结论。"""
        from nucleus.mnemosyne.ReasoningExperience import _m407_is_clean
        _cases = [
            {},
            {"polluted": True},
            {"is_cleaned": False},
            {"polluted": False, "is_cleaned": True},
            _func_marked(),
        ]
        for _c in _cases:
            self.assertEqual(ec.is_retrievable(_c), _m407_is_clean(_c), repr(_c)[:60])

    def test_32_keep_records_stay_retrievable(self):
        _r = [_rec(q) for q in KEEP]
        ec.mark_low_quality(_r, batch_no=408, dry_run=False, now=1.0)
        self.assertTrue(all(ec.is_retrievable(e) for e in _r))


def _func_marked():
    _r = _rec(LEAK)
    ec.mark_low_quality([_r], batch_no=408, dry_run=False, now=1.0)
    return _r


# ------------------------------------------------------------------ E 工具端到端
class TestE_Tool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(_ROOT, "tools"))
        cls.tool = importlib.import_module("m408_clean_low_quality_experience")

    def _mk_pool(self, d):
        _p = os.path.join(d, "reasoning_experience.json")
        _doc = {"version": "v1.0", "created_at": "2026-10-31T00:00:00",
                "total_count": 4,
                "experiences": [_rec(LEAK), _rec(SERP), _rec(TPL), _rec(KEEP[0])]}
        with open(_p, "w", encoding="utf-8", newline="\r\n") as f:
            json.dump(_doc, f, ensure_ascii=False, indent=2)
        return _p

    def _run(self, *argv):
        _buf = io.StringIO()
        with redirect_stdout(_buf):
            _rc = self.tool.main(list(argv))
        return _rc, _buf.getvalue()

    def test_40_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as _d:
            _p = self._mk_pool(_d)
            _h = self.tool.sha256(_p)
            _rc, _out = self._run("--file", _p, "--backup",
                                  os.path.join(_d, "bk.json"))
            self.assertEqual(0, _rc)
            self.assertEqual(_h, self.tool.sha256(_p), "dry-run 写盘了")
            self.assertIn("dry-run", _out)

    def test_41_apply_then_restore(self):
        with tempfile.TemporaryDirectory() as _d:
            _p = self._mk_pool(_d)
            _bk = os.path.join(_d, "bk.json")
            _rc, _ = self._run("--file", _p, "--backup", _bk, "--apply")
            self.assertEqual(0, _rc)
            _doc = json.load(open(_p, encoding="utf-8"))
            _recs = _doc["experiences"]
            self.assertEqual(3, sum(1 for e in _recs
                                    if e.get("cleanup_reason")
                                    == ec.CLASS_LOW_QUALITY_STATEMENT))
            self.assertEqual(1, sum(1 for e in _recs if ec.is_retrievable(e)))
            self.assertTrue(os.path.isfile(_bk), "没有备份")
            # 行尾必须保持 CRLF（408 实测本池 3571 CRLF / 0 裸 LF）
            _b = open(_p, "rb").read()
            self.assertGreater(_b.count(b"\r\n"), 0, "行尾被翻成 LF 了")
            self.assertEqual(0, _b.count(b"\n") - _b.count(b"\r\n"))
            # 回滚
            _rc, _ = self._run("--file", _p, "--backup", _bk, "--restore")
            self.assertEqual(0, _rc)
            _doc2 = json.load(open(_p, encoding="utf-8"))
            self.assertEqual(4, sum(1 for e in _doc2["experiences"]
                                    if ec.is_retrievable(e)))
            self.assertEqual(["confidence", "derivation_type", "example_question",
                              "features", "source", "timestamp"],
                             sorted(_doc2["experiences"][0]))

    def test_42_backup_mismatch_refuses(self):
        """★备份与源不一致（备份是旧的）必须拒写，返回 3。"""
        with tempfile.TemporaryDirectory() as _d:
            _p = self._mk_pool(_d)
            _bk = os.path.join(_d, "bk.json")
            with open(_bk, "w", encoding="utf-8") as f:
                f.write('{"experiences": []}')
            _h = self.tool.sha256(_p)
            _rc, _out = self._run("--file", _p, "--backup", _bk, "--apply")
            self.assertEqual(3, _rc, _out[-300:])
            self.assertEqual(_h, self.tool.sha256(_p), "该拒写却写盘了")

    def test_43_missing_file(self):
        with tempfile.TemporaryDirectory() as _d:
            _rc, _ = self._run("--file", os.path.join(_d, "nope.json"))
            self.assertEqual(2, _rc)


# ------------------------------------------------------------------ F 源码口径
class TestF_SourceShape(unittest.TestCase):
    def test_50_tool_has_no_silent_except(self):
        """铁律：`tools/` 与 `tests/` 一样受 cw2 管 —— 每个 except 都要 silent_exc。"""
        _t = ast.parse(open(_TOOL, "rb").read().decode("utf-8"))
        for _h in [n for n in ast.walk(_t) if isinstance(n, ast.ExceptHandler)]:
            _src = ast.unparse(_h)
            self.assertIn("silent_exc", _src,
                          "工具里有静默 except：%s" % _src[:80])

    def test_51_classify_still_reads_summary_only(self):
        """★不许偷偷改 `classify()` 的既有口径（L3 池有 summary，改了会改判既有数据）。"""
        _src = open(os.path.join(_ROOT, "nucleus", "data",
                                "experience_cleanup.py"), "rb").read().decode("utf-8")
        _t = ast.parse(_src)
        _fn = [n for n in ast.walk(_t)
               if isinstance(n, ast.FunctionDef) and n.name == "classify"]
        self.assertEqual(1, len(_fn))
        _txt = ast.unparse(_fn[0])
        self.assertIn('exp.get(\'summary\')', _txt.replace('"', "'"))
        self.assertNotIn("example_question", _txt,
                         "classify() 被改了 —— 既有 L3 池分类会跟着变")


if __name__ == "__main__":
    unittest.main(verbosity=2)
