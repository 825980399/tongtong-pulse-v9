"""第409批 刀3 判据：三判据探针（复发 / 效果 / 复用）真接数据源。

判据分组：
  A 复发判据（audit pool 同签名）
  B 效果判据（effect_verify.verdict.passed 三态）
  C 复用判据（llm_traces 文本级近似）
  D 安装与接线（探针真的装上了、main.py 真的调了）
  E 真实数据（三份数据源可用性 + 三态都落在合法集合里）

★ 单测全部在临时目录里造数据（`hr.set_data_dir`），**不读也不写真实 data/**，
  除了 E 组只读真实数据源做可用性下界断言。
"""

import ast
import io
import json
import os
import shutil
import tempfile
import unittest

from nucleus.evolution import hypothesis_criteria as hc
from nucleus.evolution import hypothesis_probes as hp
from nucleus.evolution import hypothesis_reconciler as hr

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NOW = 1_800_000_000.0
_WIN = 86400.0


def _w(path, obj):
    _d = os.path.dirname(path)
    if _d and not os.path.isdir(_d):
        os.makedirs(_d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as _f:
        if isinstance(obj, str):
            _f.write(obj)
        else:
            json.dump(obj, _f, ensure_ascii=False, indent=2)


def _audit(dirpath, ts, entries):
    _p = os.path.join(dirpath, "data", "reports", "audit",
                      f"audit_{int(ts)}.json")
    _w(_p, {"generated_at": ts, "entries": entries})
    return _p


def _trace(dirpath, day, rows):
    _p = os.path.join(dirpath, "data", "llm_traces", f"calls_{day}.jsonl")
    _d = os.path.dirname(_p)
    if not os.path.isdir(_d):
        os.makedirs(_d, exist_ok=True)
    with open(_p, "w", encoding="utf-8") as _f:
        for _r in rows:
            _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
    return _p


def _entry(sig, due=None, window="24h", kind=None):
    _k = kind or hc.KIND_RECURRENCE
    _crit = {"kind": _k, "signature": sig}
    return {"rule": "type::x", "strategy": "s",
            hc.F_CRITERION: _crit, hc.F_WINDOW: window,
            hc.F_DUE: _NOW if due is None else due}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m409k3_")
        hr.set_data_dir(self._tmp)

    def tearDown(self):
        hr.set_data_dir(None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ctx(self, crit):
        return {"now": _NOW, "criterion": crit, "key": "k", "ctx": {}}


# =========================================================== A 复发判据
class TestA_Recurrence(_Base):
    """audit pool 里同签名在窗口内是否再现。"""

    SIG = r"D:\p\a.py::cc_ge50_new::m1"

    def _run(self, entry=None, now=_NOW):
        _e = entry or _entry(self.SIG)
        _ctx = {"now": now, "criterion": _e[hc.F_CRITERION], "key": "k", "ctx": {}}
        return hp.probe_recurrence(_e, _ctx)

    def test_10_hit_is_refuted(self):
        _audit(self._tmp, _NOW - 3600, [{
            "id": "x1", "file": r"D:\p\a.py", "issue_type": "cc_ge50_new",
            "method": "m1", "status": "open",
            "first_seen_ts": _NOW - 1800, "last_seen_ts": _NOW - 1800}])
        _r = self._run()
        self.assertEqual(hc.RESULT_REFUTED, _r["result"])
        self.assertEqual(1, _r["evidence"]["hits"])

    def test_11_no_hit_with_coverage_is_confirmed(self):
        _audit(self._tmp, _NOW - 3600, [{
            "id": "y1", "file": r"D:\p\other.py", "issue_type": "x",
            "method": "m2", "status": "open",
            "first_seen_ts": _NOW - 1800, "last_seen_ts": _NOW - 1800}])
        _r = self._run()
        self.assertEqual(hc.RESULT_CONFIRMED, _r["result"])
        self.assertEqual(0, _r["evidence"]["hits"])

    def test_12_no_snapshot_in_window_is_unknown_not_confirmed(self):
        """★窗口内没人跑审计 ⇒ 零命中不是"没复发"，是"没看"。"""
        _audit(self._tmp, _NOW - 10 * _WIN, [{
            "id": "z1", "file": r"D:\p\a.py", "issue_type": "cc_ge50_new",
            "method": "m1", "status": "open",
            "last_seen_ts": _NOW - 10 * _WIN}])
        _r = self._run()
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("no_coverage", _r["reason"])

    def test_13_no_audit_files_at_all_is_unknown(self):
        _r = self._run()
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("no_audit_data", _r["reason"])

    def test_14_wontfix_excluded_from_hits(self):
        """★wontfix 是"主动不修"，它的存在不能拿来说补丁失效。"""
        _audit(self._tmp, _NOW - 3600, [{
            "id": "w1", "file": r"D:\p\a.py", "issue_type": "cc_ge50_new",
            "method": "m1", "status": "wontfix",
            "first_seen_ts": _NOW - 1800, "last_seen_ts": _NOW - 1800}])
        _r = self._run()
        self.assertEqual(hc.RESULT_CONFIRMED, _r["result"])
        self.assertEqual(1, _r["evidence"]["excluded_wontfix"])

    def test_15_fixed_status_still_counts_as_recurrence(self):
        """修过（fixed）的问题又出现在池子里 —— 那正是复发。"""
        _audit(self._tmp, _NOW - 3600, [{
            "id": "f1", "file": r"D:\p\a.py", "issue_type": "cc_ge50_new",
            "method": "m1", "status": "fixed",
            "first_seen_ts": _NOW - 1800, "last_seen_ts": _NOW - 1800}])
        self.assertEqual(hc.RESULT_REFUTED, self._run()["result"])

    def test_16_old_last_seen_outside_window_not_hit(self):
        _audit(self._tmp, _NOW - 3600, [{
            "id": "o1", "file": r"D:\p\a.py", "issue_type": "cc_ge50_new",
            "method": "m1", "status": "open",
            "first_seen_ts": _NOW - 5 * _WIN, "last_seen_ts": _NOW - 5 * _WIN}])
        self.assertEqual(hc.RESULT_CONFIRMED, self._run()["result"])

    def test_17_no_signature_is_unknown(self):
        _e = _entry("")
        _e["rule"] = "nodots"
        _r = hp.probe_recurrence(_e, self._ctx(_e[hc.F_CRITERION]))
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("no_signature", _r["reason"])

    def test_18_audit_sig_maps_issue_type_field(self):
        """★审核池字段叫 issue_type，而 issue_signature() 读 type ——
        不映射就永远是 `file::::method`，与假设签名对不上，探针白跑。"""
        _s = hp._audit_sig({"file": "a.py", "issue_type": "cc_ge50_new",
                            "method": "m1"})
        self.assertEqual("a.py::cc_ge50_new::m1", _s)

    def test_19_snapshot_only_after_window_is_no_coverage(self):
        """★★覆盖判据必须有**时间上界**。窗口在 40 天前、快照都在最近几天 ⇒
        那是"用今天的观测给上个月的窗口背书"，会把 unknown 判成 confirmed。
        （409 真实数据实测就是这个错，--probe 里"复发/窗口外无审计"曾报 confirmed。）"""
        _audit(self._tmp, _NOW - 3600, [])          # 只有窗口之后的快照
        _r = self._run(_entry(self.SIG, due=_NOW - 40 * _WIN))
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("no_coverage", _r["reason"])

    def test_20_late_recurrence_outside_grace_not_hit(self):
        """窗口之后很久才冒出来的同签名，不属于这次观察。"""
        _audit(self._tmp, _NOW - 3600, [])
        _audit(self._tmp, _NOW - 3600 + _WIN, [{
            "id": "late", "file": r"D:\p\a.py", "issue_type": "cc_ge50_new",
            "method": "m1", "status": "open",
            "first_seen_ts": _NOW - 3600 + _WIN,
            "last_seen_ts": _NOW - 3600 + _WIN}])
        _r = self._run()
        self.assertEqual(0, _r["evidence"]["hits"])
        self.assertIn("coverage_grace_sec", _r["evidence"])


# =========================================================== B 效果判据
class TestB_Effect(_Base):
    """effect_verify.verdict.passed 三态，None 必须留成 unknown。"""

    def _seed(self, verdicts):
        _w(os.path.join(self._tmp, "data", "evolution", "effect_verify.json"),
           {"version": "x", "verdicts": verdicts})

    def _run(self, crit):
        return hp.probe_effect(_entry("", kind=hc.KIND_EFFECT),
                               self._ctx(crit))

    def test_20_passed_true_is_confirmed(self):
        self._seed({"p1": {"passed": True, "reason": "问题已消失",
                           "dims": {"problem_gone": True}}})
        _r = self._run({"kind": "effect", "patch_id": "p1"})
        self.assertEqual(hc.RESULT_CONFIRMED, _r["result"])

    def test_21_passed_false_is_refuted(self):
        self._seed({"p2": {"passed": False, "reason": "问题未解决"}})
        _r = self._run({"kind": "effect", "patch_id": "p2"})
        self.assertEqual(hc.RESULT_REFUTED, _r["result"])

    def test_22_passed_none_is_unknown_not_false(self):
        """★★这一条就是"补齐三态"：passed=None 曾经被下游当 falsy 算成"没通过"。
        "没判过" ≠ "判了没通过"。"""
        self._seed({"p3": {"passed": None, "reason": "三项均未测量",
                           "dims": {"problem_gone": None, "function_ok": None}}})
        _r = self._run({"kind": "effect", "patch_id": "p3"})
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("verdict_none", _r["reason"])
        self.assertIn("problem_gone", _r["evidence"]["dims"])

    def test_23_missing_patch_id_is_unknown(self):
        self._seed({})
        self.assertEqual("no_patch_id", self._run({"kind": "effect"})["reason"])

    def test_24_patch_id_not_in_verdicts_is_unknown(self):
        self._seed({"p1": {"passed": True}})
        _r = self._run({"kind": "effect", "patch_id": "不存在"})
        self.assertEqual("no_verdict", _r["reason"])

    def test_25_no_effect_file_is_unknown(self):
        _r = self._run({"kind": "effect", "patch_id": "p1"})
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("no_effect_data", _r["reason"])

    def test_26_string_boolean_tolerated(self):
        self._seed({"p4": {"passed": "false"}, "p5": {"passed": "TRUE"}})
        self.assertEqual(hc.RESULT_REFUTED,
                         self._run({"kind": "effect", "patch_id": "p4"})["result"])
        self.assertEqual(hc.RESULT_CONFIRMED,
                         self._run({"kind": "effect", "patch_id": "p5"})["result"])


# =========================================================== C 复用判据
class TestC_Reuse(_Base):
    """llm_traces 里 7 天内是否又提到同一个方法（文本级近似）。"""

    SIG = r"D:\p\a.py::cc_ge50_new::PulseBloodVessel.start"

    def _seed(self, rows, day="20270101"):
        _trace(self._tmp, day, rows)

    def _run(self, entry=None):
        _e = entry or _entry(self.SIG, window="7d", kind=hc.KIND_REUSE)
        _e[hc.F_CRITERION]["kind"] = hc.KIND_REUSE
        return hp.probe_reuse(_e, self._ctx(_e[hc.F_CRITERION]))

    def test_30_method_hit_is_refuted(self):
        self._seed([{"ts": _NOW - 3600, "origin": "system_internal",
                     "prompt": "方法名: PulseBloodVessel.start\n代码: ...",
                     "response": ""}])
        _r = self._run()
        self.assertEqual(hc.RESULT_REFUTED, _r["result"])
        self.assertEqual(1, _r["evidence"]["hits"])
        self.assertEqual("text", _r["evidence"]["match"])

    def test_31_no_hit_with_coverage_is_confirmed(self):
        self._seed([{"ts": _NOW - 3600, "origin": "user_query",
                     "prompt": "方法名: 别的东西", "response": ""}])
        _r = self._run()
        self.assertEqual(hc.RESULT_CONFIRMED, _r["result"])
        self.assertEqual(0, _r["evidence"]["hits"])

    def test_32_no_trace_in_window_is_unknown_not_confirmed(self):
        """★窗口内一条 trace 都没有 ⇒ 零命中不是"没再问"，是"没留记录"。"""
        self._seed([{"ts": _NOW - 30 * _WIN, "prompt": "老早的", "response": ""}])
        _r = self._run()
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("no_coverage", _r["reason"])

    def test_33_empty_method_is_unknown_not_guessed(self):
        """★只剩文件名时，文本匹配会大面积误中 ⇒ 直接 unknown，不硬判。"""
        _e = _entry(r"D:\p\a.py::cc_ge50_new::", kind=hc.KIND_REUSE)
        _r = self._run(_e)
        self.assertEqual(hc.RESULT_UNKNOWN, _r["result"])
        self.assertEqual("coarse_signature", _r["reason"])

    def test_34_origin_breakdown_in_evidence(self):
        self._seed([{"ts": _NOW - 3600, "origin": "user_query",
                     "prompt": "PulseBloodVessel.start", "response": ""},
                    {"ts": _NOW - 3700, "origin": "system_internal",
                     "prompt": "PulseBloodVessel.start", "response": ""}])
        _r = self._run()
        self.assertEqual({"user_query": 1, "system_internal": 1},
                         _r["evidence"]["by_origin"])

    def test_35_evidence_declares_text_level_limit(self):
        """★文档化判据：证据里必须写明这是文本级近似，否则下批会误当结构匹配。"""
        self._seed([{"ts": _NOW - 3600, "prompt": "别的", "response": ""}])
        _r = self._run()
        self.assertIn("issue_signature", _r["evidence"]["note"])
        self.assertTrue(_r["evidence"]["matched_on"])

    def test_36_split_signature_keeps_double_colon_in_method(self):
        _f, _t, _m = hp.split_signature("a.py::t::x::y")
        self.assertEqual(("a.py", "t", "x::y"), (_f, _t, _m))


# =========================================================== D 安装接线
class TestD_Install(unittest.TestCase):
    def setUp(self):
        self._saved = hr.probes()
        hr._PROBES.clear()

    def tearDown(self):
        hr._PROBES.clear()
        hr._PROBES.update(self._saved)

    def test_40_install_registers_three(self):
        _o = hp.install()
        self.assertEqual({True}, set(_o.values()))
        self.assertEqual(set(hc.ALL_KINDS), set(hr.probes()))

    def test_41_main_py_calls_install(self):
        """★语义判据：从 hypothesis_probes 导进来的 install，必须真的被调用。"""
        _src = io.open(os.path.join(_ROOT, "main.py"), "rb").read().decode("utf-8")
        _tree = ast.parse(_src)
        _names = set()
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ImportFrom) and \
                    _n.module == "nucleus.evolution.hypothesis_probes":
                for _a in _n.names:
                    if _a.name == "install":
                        _names.add(_a.asname or _a.name)
        self.assertTrue(_names, "main.py 未从 hypothesis_probes 导入 install")
        _called = {n.func.id for n in ast.walk(_tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertTrue(_names & _called, f"导入了却没调：{_names}")

    def test_42_all_probes_return_legal_results(self):
        hp.install()
        _ctx = {"now": _NOW, "criterion": {"kind": hc.KIND_EFFECT}, "key": "k", "ctx": {}}
        for _k, _fn in hp._PROBE_FNS.items():
            _r = _fn({"rule": "t::x"}, {**_ctx, "criterion": {"kind": _k}})
            self.assertIn(_r["result"], hc.ALL_RESULTS, _k)
            self.assertIn("detail", _r, _k)


# =========================================================== E 真实数据
class TestE_RealData(unittest.TestCase):
    """★只读真实数据源；只钉下界，不钉精确值（新增一个正常文件不该顶红）。"""

    def test_50_selfcheck_sources_alive(self):
        _s = hp.selfcheck()
        self.assertGreaterEqual(_s["audit"]["files"], 1, "审核池快照一个都没有")
        self.assertGreaterEqual(_s["audit"]["entries"], 1)
        self.assertGreaterEqual(_s["effect"]["verdicts"], 1)
        self.assertGreaterEqual(_s["trace"]["files"], 1)

    def test_51_real_data_yields_at_least_two_distinct_states(self):
        """★真跑一遍：如果三态恒为 unknown，说明探针根本没接上数据。
        不钉具体结论（数据天天变），只要求"至少能分出两种不同结论"。

        ★顺序很重要：`set_data_dir()` 会同时改**数据源根**和**落盘根**，所以
        必须先读真实 verdicts、再把真实 `effect_verify.json` 复制进临时目录，
        否则探针读到的是空目录（首跑即此：passed=True 一个都找不到）。
        """
        _real_eff = os.path.join(hr.data_root(), "data", "evolution",
                                 "effect_verify.json")
        _eff = hp._load_effect()          # 重定向**之前**读真实数据
        _pid_true = next((k for k, v in (_eff.get("verdicts") or {}).items()
                          if v.get("passed") is True), None)
        _pid_none = next((k for k, v in (_eff.get("verdicts") or {}).items()
                          if v.get("passed") is None), None)
        self.assertIsNotNone(_pid_true, "effect_verify 里没有 passed=True 的记录")

        _tmp = tempfile.mkdtemp(prefix="m409k3_real_")
        _saved = hr.probes()
        try:
            hr.set_data_dir(_tmp)
            _dst_dir = os.path.join(_tmp, "data", "evolution")
            os.makedirs(_dst_dir, exist_ok=True)
            shutil.copy2(_real_eff, os.path.join(_dst_dir, "effect_verify.json"))
            hp.install()
            _l = {"version": "335.1", "updated_at": 0.0, "strategies": {
                "a": {hc.F_CRITERION: {"kind": hc.KIND_EFFECT, "patch_id": _pid_true},
                      hc.F_WINDOW: "24h", hc.F_DUE: 0.0},
                "b": {hc.F_CRITERION: {"kind": hc.KIND_EFFECT,
                                       "patch_id": _pid_none or "无"},
                      hc.F_WINDOW: "24h", hc.F_DUE: 0.0},
            }}
            _p = hr.ledger_path()
            os.makedirs(os.path.dirname(_p), exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _f:
                json.dump(_l, _f, ensure_ascii=False)
            _r = hr.reconcile(now=_NOW, path=_p)
            _states = {x["result"] for x in _r["rows"]}
            self.assertGreaterEqual(len(_states), 2,
                                    f"真数据上三种结论分不开：{_states}")
        finally:
            hr._PROBES.clear()
            hr._PROBES.update(_saved)
            hr.set_data_dir(None)
            shutil.rmtree(_tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
