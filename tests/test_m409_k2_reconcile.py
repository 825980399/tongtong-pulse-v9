"""第409批 刀2 判据：DailyScheduler 每日 03:00 假设对账。

判据分组：
  A 到期选题（该判谁 / 不该判谁）
  B 三态收敛（判不了必须落 unknown 且给出 reason）
  C 对账写回（三态落盘 / refuted 清单 / confirmed 候选 / dry_run 零写入 / 读坏不覆盖）
  D 灰度（关掉就一个字节都不动）
  E 挂载（钩子名 / 永不抛 / main.py 真接线）
  F 候选预检诚实（不能假装 confirmed 就等于本能）

★ 这一批的判据都**不钉精确数字**（本能条数、轮次），只钉「有没有发生」与下界，
  否则新增一个正常文件就会顶红。
"""

import ast
import io
import json
import os
import shutil
import tempfile
import unittest

from nucleus.evolution import hypothesis_criteria as hc
from nucleus.evolution import hypothesis_reconciler as hr

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------- 工具
def _entry(criterion=None, window="24h", due=1000.0, **kw):
    """造一条账本条目（默认已到期）。"""
    _e = {"rule": "type::x", "strategy": "s",
          hc.F_WINDOW: window, hc.F_DUE: due}
    if criterion is not None:
        _e[hc.F_CRITERION] = criterion
    _e.update(kw)
    return _e


def _ledger(**entries):
    return {"version": "335.1", "updated_at": 0.0, "strategies": entries}


def _write(path, text):
    _d = os.path.dirname(path)
    if _d and not os.path.isdir(_d):
        os.makedirs(_d, exist_ok=True)
    with open(path, "wb") as _f:
        _f.write(text.encode("utf-8"))


def _read(path):
    with open(path, "rb") as _f:
        return _f.read()


class _ProbeCase(unittest.TestCase):
    """带临时落盘目录 + 探针清理的基类。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m409k2_")
        hr.set_data_dir(self._tmp)
        self._saved = hr.probes()
        hr._PROBES.clear()
        import config
        self._cfg = config
        self._had = hasattr(config, "ENABLE_M409_HYPOTHESIS_CRITERIA")
        self._old = getattr(config, "ENABLE_M409_HYPOTHESIS_CRITERIA", True)

    def tearDown(self):
        hr._PROBES.clear()
        hr._PROBES.update(self._saved)
        hr.set_data_dir(None)
        if self._had:
            self._cfg.ENABLE_M409_HYPOTHESIS_CRITERIA = self._old
        elif hasattr(self._cfg, "ENABLE_M409_HYPOTHESIS_CRITERIA"):
            delattr(self._cfg, "ENABLE_M409_HYPOTHESIS_CRITERIA")
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stub(self, kind, result, detail="", evidence=None):
        hr.register_probe(kind, lambda e, c: {
            "result": result, "detail": detail, "evidence": evidence or {}})


# ===================================================================== A 选题
class TestA_DueSelection(_ProbeCase):
    """到期选题：判谁、不判谁。"""

    def test_01_past_due_and_empty_result_is_due(self):
        _l = _ledger(k=_entry(due=100.0))
        self.assertEqual(["k"], [k for k, _ in hr.due_entries(_l, now=200.0)])

    def test_02_future_due_not_selected(self):
        _l = _ledger(k=_entry(due=500.0))
        self.assertEqual([], hr.due_entries(_l, now=200.0))

    def test_03_no_due_at_never_selected(self):
        """★没有到期时刻就永远不入列 —— 硬判等于把「没安排」当成「已到期」。"""
        _e = _entry()
        _e.pop(hc.F_DUE)
        self.assertEqual([], hr.due_entries(_ledger(k=_e), now=10 ** 9))

    def test_04_garbage_due_at_never_selected(self):
        _l = _ledger(a=_entry(due="明天"), b=_entry(due=True), c=_entry(due=None))
        self.assertEqual([], hr.due_entries(_l, now=10 ** 9))

    def test_05_settled_not_reselected(self):
        _l = _ledger(a=_entry(due=100.0, result=hc.RESULT_CONFIRMED),
                     b=_entry(due=100.0, result=hc.RESULT_REFUTED))
        self.assertEqual([], hr.due_entries(_l, now=200.0))

    def test_06_unknown_retried_until_cap(self):
        """unknown 不是定论：数据可能后到，允许重判到上限为止。"""
        _e = _entry(due=100.0, result=hc.RESULT_UNKNOWN, resolve_attempts=0)
        self.assertEqual(1, len(hr.due_entries(_ledger(k=_e), now=200.0, max_attempts=3)))
        _e["resolve_attempts"] = 2
        self.assertEqual(1, len(hr.due_entries(_ledger(k=_e), now=200.0, max_attempts=3)))
        _e["resolve_attempts"] = 3
        self.assertEqual([], hr.due_entries(_ledger(k=_e), now=200.0, max_attempts=3))

    def test_07_non_dict_entry_skipped(self):
        _l = {"strategies": {"a": "not-a-dict", "b": None}}
        self.assertEqual([], hr.due_entries(_l, now=200.0))

    def test_08_sorted_by_due_at(self):
        _l = _ledger(late=_entry(due=300.0), early=_entry(due=100.0),
                     mid=_entry(due=200.0))
        self.assertEqual(["early", "mid", "late"],
                         [k for k, _ in hr.due_entries(_l, now=400.0)])


# ================================================================= B 三态收敛
class TestB_ThreeState(_ProbeCase):
    """判不了必须落 unknown，并说清卡在哪一环。"""

    def _reason(self, entry):
        return hr.judge_one("k", entry, now=200.0).get("reason")

    def test_10_missing_criterion_is_unknown(self):
        _j = hr.judge_one("k", _entry(), now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("no_criterion", _j["reason"])

    def test_11_text_criterion_is_unknown_not_guessed(self):
        """自由文本判据程序无从执行 ⇒ unknown，不许猜一个结论出来。"""
        _j = hr.judge_one("k", _entry(criterion="观察一周看有没有复发"), now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("not_structured", _j["reason"])

    def test_12_unknown_kind_is_unknown(self):
        _j = hr.judge_one("k", _entry(criterion={"kind": "占卜"}), now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("not_structured", _j["reason"])

    def test_13_no_probe_is_unknown(self):
        _j = hr.judge_one("k", _entry(criterion={"kind": hc.KIND_RECURRENCE}),
                          now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("no_probe", _j["reason"])

    def test_14_probe_error_is_not_refuted(self):
        """★探针抛异常 ≠ 判据被推翻。用异常当反证是这批最容易犯的错。"""
        def _boom(_e, _c):
            raise RuntimeError("数据源没了")
        hr.register_probe(hc.KIND_EFFECT, _boom)
        _j = hr.judge_one("k", _entry(criterion={"kind": hc.KIND_EFFECT}), now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("probe_error", _j["reason"])
        self.assertIn("RuntimeError", _j["detail"])

    def test_15_probe_bad_return_is_unknown(self):
        hr.register_probe(hc.KIND_EFFECT, lambda e, c: "confirmed")
        _j = hr.judge_one("k", _entry(criterion={"kind": hc.KIND_EFFECT}), now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("probe_bad_return", _j["reason"])

    def test_16_probe_illegal_result_is_unknown(self):
        hr.register_probe(hc.KIND_EFFECT, lambda e, c: {"result": "大概吧"})
        _j = hr.judge_one("k", _entry(criterion={"kind": hc.KIND_EFFECT}), now=200.0)
        self.assertEqual(hc.RESULT_UNKNOWN, _j["result"])
        self.assertEqual("probe_bad_result", _j["reason"])

    def test_17_probe_confirmed_passes_through(self):
        self._stub(hc.KIND_REUSE, hc.RESULT_CONFIRMED, detail="7天零调用",
                   evidence={"calls": 0})
        _j = hr.judge_one("k", _entry(criterion={"kind": hc.KIND_REUSE}), now=200.0)
        self.assertEqual(hc.RESULT_CONFIRMED, _j["result"])
        self.assertEqual("7天零调用", _j["detail"])
        self.assertEqual({"calls": 0}, _j["evidence"])

    def test_18_ctx_carries_criterion_and_now(self):
        _seen = {}

        def _grab(_e, c):
            _seen.update(c)
            return {"result": hc.RESULT_UNKNOWN}
        hr.register_probe(hc.KIND_EFFECT, _grab)
        hr.judge_one("k", _entry(criterion={"kind": hc.KIND_EFFECT, "patch_id": "p1"}),
                     now=200.0)
        self.assertEqual(200.0, _seen.get("now"))
        self.assertEqual("p1", (_seen.get("criterion") or {}).get("patch_id"))


# ================================================================= C 写回
class TestC_WriteBack(_ProbeCase):
    """三态落盘 / 旁路清单 / dry_run / 读坏不覆盖。"""

    def _seed(self, **entries):
        _p = hr.ledger_path()
        _write(_p, json.dumps(_ledger(**entries), ensure_ascii=False, indent=2))
        return _p

    def _load(self, path):
        with open(path, encoding="utf-8") as _f:
            return json.load(_f)

    def test_20_three_states_written_back(self):
        self._stub(hc.KIND_RECURRENCE, hc.RESULT_CONFIRMED)
        self._stub(hc.KIND_EFFECT, hc.RESULT_REFUTED)
        self._stub(hc.KIND_REUSE, hc.RESULT_UNKNOWN)
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_RECURRENCE}, due=100.0),
                        b=_entry(criterion={"kind": hc.KIND_EFFECT}, due=100.0),
                        c=_entry(criterion={"kind": hc.KIND_REUSE}, due=100.0),
                        d=_entry(due=100.0))
        _r = hr.reconcile(now=200.0, path=_p)
        self.assertEqual(4, _r["due"])
        self.assertEqual(1, _r["confirmed"])
        self.assertEqual(1, _r["refuted"])
        self.assertEqual(2, _r["unknown"])
        self.assertTrue(_r["written"])
        _d = self._load(_p)["strategies"]
        for _k, _want in (("a", hc.RESULT_CONFIRMED), ("b", hc.RESULT_REFUTED),
                          ("c", hc.RESULT_UNKNOWN), ("d", hc.RESULT_UNKNOWN)):
            self.assertEqual(_want, _d[_k][hc.F_RESULT], _k)
            self.assertEqual(200.0, _d[_k][hc.F_RESOLVED], _k)
            self.assertEqual(1, _d[_k]["resolve_attempts"], _k)

    def test_21_refuted_goes_to_manifest(self):
        self._stub(hc.KIND_EFFECT, hc.RESULT_REFUTED, detail="补丁没生效")
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_EFFECT}, due=100.0))
        _r = hr.reconcile(now=200.0, path=_p)
        self.assertGreaterEqual(_r["refuted_total"], 1)
        _m = self._load(hr.refuted_path())
        self.assertIn("a", _m["items"])
        _it = _m["items"]["a"]
        self.assertEqual("pending_dispose", _it["status"])
        self.assertEqual("补丁没生效", _it["detail"])
        self.assertEqual(1, _it["times"])

    def test_22_confirmed_queued_as_candidate(self):
        self._stub(hc.KIND_REUSE, hc.RESULT_CONFIRMED, evidence={"calls": 0})
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_REUSE}, due=100.0))
        _r = hr.reconcile(now=200.0, path=_p)
        self.assertEqual(1, _r["candidates_queued"])
        _c = self._load(hr.candidate_path())
        self.assertIn("a", _c["items"])
        self.assertEqual("queued", _c["items"]["a"]["status"])
        self.assertEqual({"calls": 0}, _c["items"]["a"]["evidence"])

    def test_23_candidate_not_duplicated(self):
        """同一 key 二次 confirmed 只更新证据，不重复排队。"""
        self._stub(hc.KIND_REUSE, hc.RESULT_CONFIRMED)
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_REUSE}, due=100.0))
        hr.reconcile(now=200.0, path=_p)
        # 抹掉 result 让它再判一次
        _d = self._load(_p)
        _d["strategies"]["a"].pop(hc.F_RESULT)
        _write(_p, json.dumps(_d, ensure_ascii=False, indent=2))
        _r2 = hr.reconcile(now=300.0, path=_p)
        self.assertEqual(0, _r2["candidates_queued"])
        _c = self._load(hr.candidate_path())
        self.assertEqual(1, len(_c["items"]))

    def test_24_dry_run_touches_nothing(self):
        self._stub(hc.KIND_EFFECT, hc.RESULT_REFUTED)
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_EFFECT}, due=100.0))
        _before = _read(_p)
        _r = hr.reconcile(now=200.0, path=_p, dry_run=True)
        self.assertEqual(1, _r["refuted"])
        self.assertFalse(_r["written"])
        self.assertEqual(_before, _read(_p))
        self.assertFalse(os.path.exists(hr.refuted_path()))
        self.assertFalse(os.path.exists(hr.candidate_path()))

    def test_25_unreadable_ledger_never_overwritten(self):
        """★data/ 整目录 gitignore ⇒ 读坏时用空账覆盖 = 30 条真账永久消失。"""
        _p = hr.ledger_path()
        _write(_p, "{ 这不是 json")
        _before = _read(_p)
        _r = hr.reconcile(now=200.0, path=_p)
        self.assertEqual("ledger_unreadable", _r["skipped"])
        self.assertFalse(_r["written"])
        self.assertEqual(_before, _read(_p))

    def test_26_wrong_shape_ledger_never_overwritten(self):
        _p = hr.ledger_path()
        _write(_p, json.dumps({"strategies": "不是字典"}))
        _before = _read(_p)
        _r = hr.reconcile(now=200.0, path=_p)
        self.assertFalse(_r["written"])
        self.assertEqual(_before, _read(_p))

    def test_27_unknown_frozen_after_cap(self):
        """unknown 到上限后不再重判 —— 否则每天重扫一遍永不结束。"""
        self._stub(hc.KIND_REUSE, hc.RESULT_UNKNOWN)
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_REUSE}, due=100.0))
        for _i in range(hr.MAX_ATTEMPTS):
            hr.reconcile(now=200.0 + _i, path=_p)
        _r = hr.reconcile(now=999.0, path=_p)
        self.assertEqual(0, _r["due"])

    def test_28_second_run_idempotent(self):
        self._stub(hc.KIND_EFFECT, hc.RESULT_CONFIRMED)
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_EFFECT}, due=100.0))
        hr.reconcile(now=200.0, path=_p)
        _before = _read(_p)
        _r = hr.reconcile(now=300.0, path=_p)
        self.assertEqual(0, _r["due"])
        self.assertEqual(_before, _read(_p))

    def test_29_run_snapshot_written(self):
        """「每日 03:00 真的跑了吗」必须可取证 —— 落最近 30 轮快照。"""
        self._stub(hc.KIND_EFFECT, hc.RESULT_CONFIRMED)
        _p = self._seed(a=_entry(criterion={"kind": hc.KIND_EFFECT}, due=100.0))
        hr.reconcile(now=200.0, path=_p)
        _s = self._load(hr.last_run_path())
        self.assertGreaterEqual(len(_s["runs"]), 1)
        self.assertEqual(1, _s["runs"][-1]["due"])

    def test_30_crlf_preserved(self):
        """账本原文是 CRLF ⇒ 回写后仍是 CRLF（混行尾会让下一批的按字节判据全红）。"""
        _p = hr.ledger_path()
        _text = json.dumps(_ledger(a=_entry(
            criterion={"kind": hc.KIND_EFFECT}, due=100.0)),
            ensure_ascii=False, indent=2).replace("\n", "\r\n")
        _write(_p, _text)
        self._stub(hc.KIND_EFFECT, hc.RESULT_CONFIRMED)
        hr.reconcile(now=200.0, path=_p)
        _b = _read(_p)
        self.assertIn(b"\r\n", _b)
        self.assertEqual(0, _b.count(b"\n") - _b.count(b"\r\n"))


# ================================================================= D 灰度
class TestD_Switch(_ProbeCase):
    def test_40_off_reads_and_writes_nothing(self):
        self._cfg.ENABLE_M409_HYPOTHESIS_CRITERIA = False
        _p = hr.ledger_path()
        _write(_p, json.dumps(_ledger(a=_entry(due=100.0)), ensure_ascii=False))
        _before = _read(_p)
        _r = hr.reconcile(now=200.0, path=_p)
        self.assertEqual("switch_off", _r["skipped"])
        self.assertEqual(0, _r["due"])
        self.assertEqual(_before, _read(_p))
        self.assertFalse(os.path.exists(hr.last_run_path()))


# ================================================================= E 挂载
class _FakeSched:
    def __init__(self):
        self.hooks = {}

    def add_daily_hook(self, name, fn):
        self.hooks[name] = fn
        return name

    def has_daily_hook(self, name):
        return name in self.hooks


class TestE_Mount(_ProbeCase):
    """★接线门禁：建好了 ≠ 装上车（第24批 append_dialog_turn 的教训）。"""

    def test_50_hook_registered(self):
        _s = _FakeSched()
        self.assertIs(_s, hr.register_hypothesis_reconciler(_s))
        self.assertTrue(_s.has_daily_hook(hr.RECONCILE_HOOK))
        self.assertEqual("hypothesis_reconcile_409", hr.RECONCILE_HOOK)

    def test_51_scheduler_without_hook_api_returns_none(self):
        self.assertIsNone(hr.register_hypothesis_reconciler(object()))

    def test_52_scheduled_never_raises(self):
        """钩子里炸了也不能把每日调度带下水。"""
        _orig = hr.reconcile

        def _boom(*_a, **_k):
            raise RuntimeError("炸")
        hr.reconcile = _boom
        try:
            _r = hr.reconcile_scheduled()
            self.assertEqual("RuntimeError", _r.get("error"))
        finally:
            hr.reconcile = _orig

    def test_53_main_py_really_wires_it(self):
        """★判据钉「语义」不钉「措辞」：main.py 惯例是 import as 别名再调
        （`_295_reg_dispatch` / `_324_reg_graph` / `_409_reg_reconcile`），
        所以不能锁死 `register_hypothesis_reconciler()` 这个字面调用。
        正确的语义判据是：从这个模块 import 进来的那个名字，必须真的被调用。"""
        _src = io.open(os.path.join(_ROOT, "main.py"), "rb").read().decode("utf-8")
        _tree = ast.parse(_src)
        _names = set()
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ImportFrom) and \
                    _n.module == "nucleus.evolution.hypothesis_reconciler":
                for _a in _n.names:
                    if _a.name == "register_hypothesis_reconciler":
                        _names.add(_a.asname or _a.name)
        self.assertTrue(_names,
                        "main.py 未从 hypothesis_reconciler 导入 register_hypothesis_reconciler")
        _called = {n.func.id for n in ast.walk(_tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertTrue(_names & _called,
                        f"导入了却没调用：{_names} 均不在调用集合里")

    def test_54_hook_registers_reconcile_scheduled(self):
        _s = _FakeSched()
        hr.register_hypothesis_reconciler(_s)
        self.assertIs(hr.reconcile_scheduled, _s.hooks[hr.RECONCILE_HOOK])


# ============================================================ F 候选预检诚实
class TestF_CandidateHonesty(_ProbeCase):
    """★confirmed ≠ 本能。7 条件里 5 个账本侧无字段，不许假装能判。"""

    def test_60_eligible_now_false(self):
        _c = hr.queue_instinct_candidate(_entry(), "k", now=1000.0)
        self.assertFalse(_c["eligible_now"])
        self.assertFalse(_c["precheck"]["cooldown_ok"])

    def test_61_cooldown_blocks_and_gives_eligible_after(self):
        _c = hr.queue_instinct_candidate(_entry(), "k", now=1000.0)
        _p = _c["precheck"]
        self.assertEqual(30, _p["cooldown_days"])
        self.assertEqual(1000.0 + 30 * 86400.0, _p["eligible_after"])

    def test_62_not_evaluable_lists_node_only_conditions(self):
        _c = hr.queue_instinct_candidate(_entry(), "k", now=1000.0)
        _ne = set(_c["precheck"]["not_evaluable"])
        self.assertEqual({"source_organ", "activation_count", "abstraction",
                          "cross_domains", "keyword_overlap"}, _ne)

    def test_63_cap_comes_from_config(self):
        _c = hr.queue_instinct_candidate(_entry(), "k", now=1000.0)
        import config
        self.assertEqual(int(getattr(config, "INSTINCT", {}).get("max_count", 20)),
                         _c["precheck"]["instinct_cap"])

    def test_64_cap_ok_is_bool_not_assumption(self):
        _c = hr.queue_instinct_candidate(_entry(), "k", now=1000.0)
        self.assertIsInstance(_c["precheck"]["cap_ok"], bool)


if __name__ == "__main__":
    unittest.main()
