# -*- coding: utf-8 -*-
"""第360批 C 组（补丁生成器与自进柱闭环）验收测试。

刀5 负样本回灌、刀6 契约白名单 / 虚构模块拦截、
刀7 闭环核销（链路无断点）、刀8 并发认领压测（不丢记录）。
"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import patch_generator as pg  # noqa: E402


def _entry(method="target_method", **kw):
    _e = {"file": "nucleus/demo.py", "method": method,
          "issue_type": "silent_exception", "severity": "P1",
          "klass": "code", "line": 10, "desc": "示例问题",
          "issue_id": "demo-1"}
    _e.update(kw)
    return _e


def _source(code="def target_method(self):\n    return 1\n"):
    return {"why": "demo", "start_line": 1, "end_line": 2, "code": code}


# ======================================================================
class TestM360C05NegativeSamples(unittest.TestCase):
    """刀5：死因负样本回灌生成器提示词。"""

    def test_01_render_empty_is_empty(self):
        self.assertEqual(pg.render_negative_block([]), "")
        self.assertEqual(pg.render_negative_block(None), "")

    def test_02_render_contains_death_reason(self):
        _ex = [{"origin": "pool", "file": "a/b.py", "method": "m",
                "issue_type": "silent_exception", "reason": "该问题属协议接口，改代码无解"}]
        _b = pg.render_negative_block(_ex)
        self.assertIn("该问题属协议接口，改代码无解", _b)
        self.assertIn("a/b.py::m", _b)

    def test_03_prompt_includes_negative_block(self):
        """验收硬指标：构造 1 条 wontfix/dup 死因 ⇒ prompt 里必须含它。"""
        _death = "dup:与已结案条目重复，不再修复"
        _neg = [{"origin": "rejected", "file": "nucleus/demo.py",
                 "method": "target_method", "issue_type": "silent_exception",
                 "reason": _death}]
        _p = pg.build_prompt(_entry(), _source(), None, _neg)
        self.assertIn(_death, _p, "负样本死因必须出现在提示词里")
        self.assertIn("负样本", _p)

    def test_04_prompt_without_negatives_unchanged(self):
        """默认 None ⇒ 与 360 前一致（零回归）。"""
        _a = pg.build_prompt(_entry(), _source(), None)
        _b = pg.build_prompt(_entry(), _source(), None, None)
        self.assertEqual(_a, _b)
        self.assertNotIn("负样本", _a)

    def test_05_load_is_deterministic(self):
        """同一份台账 ⇒ 同一组负样本（352刀5 同纪律）。"""
        _a = pg.load_negative_examples(n=4).get("examples")
        _b = pg.load_negative_examples(n=4).get("examples")
        self.assertEqual(_a, _b, "取样必须确定性")

    def test_06_shared_stream_same_source(self):
        """共享样本流与生成器同源（避免两处各学各的）。"""
        _a = pg.negative_sample_stream(n=3).get("examples")
        _b = pg.load_negative_examples(n=3).get("examples")
        self.assertEqual(_a, _b)


# ======================================================================
class TestM360C06ContractWhitelist(unittest.TestCase):
    """刀6：虚构模块拦截 + 契约三件套白名单。"""

    def test_07_pulsebus_rejected(self):
        _d = {"modified_code": "def m(self):\n    PulseBus.publish('x')\n"}
        _r = pg.screen_forbidden_refs(_d)
        self.assertFalse(_r["ok"])
        self.assertIn("PulseBus", _r["forbidden"])
        self.assertIn("生产不存在", _r["why"])

    def test_08_contract_triple_allowed(self):
        """契约三件套（self._emit / EventBus.publish / insight_board）必须放行。"""
        _code = ("def m(self):\n"
                 "    self._emit('x')\n"
                 "    EventBus.publish('y')\n"
                 "    get_insight_board().post('z')\n")
        _r = pg.screen_forbidden_refs({"modified_code": _code})
        self.assertTrue(_r["ok"], "契约三件套不得被误拦：%s" % _r["why"])
        self.assertEqual(_r["forbidden"], [])

    def test_09_unparsable_is_not_rejected_here(self):
        """语法坏 ⇒ 不在此判（交 method_contract），避免一稿两判互相掩盖。"""
        _r = pg.screen_forbidden_refs({"modified_code": "def m(:  ???"})
        self.assertTrue(_r["ok"])
        self.assertEqual(_r["why"], "unparsable")

    def test_10_def_name_mismatch_rejected(self):
        """def 名一致性校验（352刀3 已落地，本例为核销回归）。"""
        _d = {"method": "real_name",
              "modified_code": "def wrong_name(self):\n    return 1\n"}
        _r = pg.check_method_contract(_d)
        self.assertFalse(_r["ok"])
        self.assertIn("def 名", _r["why"])

    def test_11_def_name_match_passes(self):
        _d = {"method": "real_name",
              "modified_code": "def real_name(self):\n    return 1\n",
              "original_code": "def real_name(self):\n    return 0\n"}
        _r = pg.check_method_contract(_d)
        self.assertTrue(_r["ok"], "def 名一致应通过：%s" % _r["why"])

    def test_12_forbidden_ref_wired_into_review(self):
        """审核链必须真的查这一项（不是造了函数没挂）。"""
        import inspect
        _src = inspect.getsource(pg)
        self.assertIn("_checks[\"forbidden_refs\"]", _src)
        self.assertIn("forbidden_refs:%s", _src)


# ======================================================================
class TestM360C07Closure(unittest.TestCase):
    """刀7：六段闭环核销（发现断点才修，本轮结论=无断点）。"""

    def test_13_close_loop_wired_after_success(self):
        """派单成功 ⇒ 必须调 `_m356_close_loop_fixed`（第 3 段接线存在）。"""
        _p = os.path.join("nucleus", "evolution", "patch_execution_dispatch.py")
        with open(_p, "r", encoding="utf-8") as f:
            _src = f.read()
        self.assertIn("if _v.get(\"success\"):", _src)
        self.assertIn("_m356_close_loop_fixed(_p)", _src)

    def test_14_problem_fixed_verified_written(self):
        """验证通过 ⇒ problem_fixed=verified（第 5 段双写存在）。"""
        _p = os.path.join("nucleus", "audit_problem_pool.py")
        with open(_p, "r", encoding="utf-8") as f:
            _src = f.read()
        self.assertIn('_hit["problem_fixed"] = "verified"', _src)
        self.assertIn("356_effect_verify_passed", _src)


# ======================================================================
class TestM360C08ClaimPersistence(unittest.TestCase):
    """刀8：并发认领压测（356 阶段A刀3 已加 RLock，本例验证不丢）。"""

    def test_15_concurrent_claim_no_loss(self):
        """8 线程并发认领 8 个不同条目 ⇒ 全部落到终态，零丢失。"""
        from nucleus import audit_problem_pool as pool

        _tmp = tempfile.mkdtemp(prefix="m360claim_")
        # ★契约：`persist_pool` 吃的是 `report["issues"]`（问题原始列表），
        #   不是 entries；传错字段会静默 skipped（不写空快照）⇒ 压测读到空池。
        _issues = [{"file": "nucleus/demo_%d.py" % _i,
                    "method": "m%d" % _i,
                    "issue_type": "silent_exception",
                    "severity": "P1"} for _i in range(8)]
        _r0 = pool.persist_pool({"issues": _issues}, audit_dir_path=_tmp)
        self.assertEqual(_r0.get("state"), "ok",
                         "压测前必须先造出池（实得 %s）" % _r0.get("state"))
        _back0 = pool.load_pool(audit_dir_path=_tmp)
        self.assertEqual(_back0.get("state"), "ok")
        _keys = [str(e.get("dedup_key") or "")
                 for e in (_back0.get("entries") or [])]
        self.assertEqual(len(set(_keys)), 8,
                         "8 个条目键必须互不相同（实得 %d）" % len(set(_keys)))

        _errs = []

        def _claim(_k):
            try:
                pool.set_status(_k, "claimed", audit_dir_path=_tmp,
                                note="360压测")
            except Exception as _e:      # noqa: BLE001
                _errs.append("%s" % _e)

        _ths = [threading.Thread(target=_claim, args=(_k,)) for _k in _keys]
        for _t in _ths:
            _t.start()
        for _t in _ths:
            _t.join(30)
        self.assertEqual(_errs, [], "并发认领不得抛异常：%s" % _errs[:3])

        _back = pool.load_pool(audit_dir_path=_tmp)
        self.assertEqual(_back.get("state"), "ok")
        _got = {str(e.get("dedup_key")): str(e.get("status"))
                for e in (_back.get("entries") or [])}
        _claimed = sum(1 for _k in _keys if _got.get(_k) == "claimed")
        self.assertEqual(_claimed, 8,
                         "8 条认领必须全部落盘（实得 %d，丢 %d）"
                         % (_claimed, 8 - _claimed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
