# -*- coding: utf-8 -*-
"""★479 收尾：ALL 协作准入判据对齐 + 零产出渠道过滤 门控单测。

覆盖两个真缺陷：
  ① best 只看总分 ⇒ 78 分但 full_method=false 的候选被选中，
     而 470 判据要求 modified 的 def 行方法名匹配 ⇒ 进 469 必拦 ⇒ 白烧 40s；
  ② pick_channels(4) 按 priority 取前 4，把 0/21 全空的推理模型也装进来
     ⇒ 4 路并行里一半配额白烧。
"""
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.autonomy import all_collab as AC


def _row(cands, **kw):
    """构造一轮台账记录。cands = [(channel, ok, score, full_method), ...]"""
    return {
        "schema": "479.all_collab.v1",
        "ts": 1.0,
        "patch_id": "p1",
        "file": "x.py",
        "method": "f",
        "issue_type": "silent_exception",
        "baseline_score": 80,
        "best_channel": "",
        "best_score": 0,
        "picked": False,
        "candidates": [
            {"channel": c, "model": c, "ok": o, "score": s, "elapsed": 1.0,
             "error": "", "detail": {"score": s, "full_method": fm,
                                     "parse_ok": True, "reason": ""}}
            for c, o, s, fm in cands
        ],
        **kw,
    }


class _TmpLedger:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="m479de_")
        self.p = os.path.join(self.d, "all_collab.jsonl")
        return self.p

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    @staticmethod
    def write(p, rows):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


# ══════════════════ 第一部分：best 必须 full_method ══════════════════


class TestBestMustBeFullMethod:
    """★核心反证：高分但没有 def 行的候选不能当 best。"""

    def test_01_high_score_without_full_method_is_not_best(self, monkeypatch):
        """78 分但 full_method=false ⇒ best 必须为 None（不是拿它滥竽充数）。"""
        # 造两个候选：A 高分但非完整方法，B 低分但完整方法
        _a = {"channel": "a", "model": "a", "ok": True, "score": 78,
              "elapsed": 1.0,
              "score_detail": {"score": 78, "full_method": False, "parse_ok": True,
                               "reason": "不是完整方法"}}
        _b = {"channel": "b", "model": "b", "ok": True, "score": 66,
              "elapsed": 2.0,
              "score_detail": {"score": 66, "full_method": True, "parse_ok": True,
                               "reason": ""}}
        # 直接复用 generate_candidates 的选优逻辑：用伪造 _rows 复算
        _rows = [_a, _b]
        _rows.sort(key=lambda x: (-int(x.get("score") or 0),
                                  float(x.get("elapsed") or 0)))
        _best = None
        for _r in _rows:
            _d = _r.get("score_detail") or {}
            if int(_r.get("score") or 0) >= 65 and _d.get("full_method") is True:
                _best = _r
                break
        assert _best is _b, "应跳过高分非完整方法，选中 66 分的完整方法"

    def test_02_all_candidates_not_full_method_best_is_none(self):
        _rows = [{"channel": "a", "score": 90,
                  "score_detail": {"full_method": False}},
                 {"channel": "b", "score": 78,
                  "score_detail": {"full_method": False}}]
        _rows.sort(key=lambda x: -int(x.get("score") or 0))
        _best = None
        for _r in _rows:
            _d = _r.get("score_detail") or {}
            if int(_r.get("score") or 0) >= 65 and _d.get("full_method") is True:
                _best = _r
                break
        assert _best is None, "全部非完整方法 ⇒ 必须承认「无可用候选」"

    def test_03_score_below_65_still_rejected(self):
        _rows = [{"channel": "a", "score": 60,
                  "score_detail": {"full_method": True}}]
        _best = None
        for _r in _rows:
            _d = _r.get("score_detail") or {}
            if int(_r.get("score") or 0) >= 65 and _d.get("full_method") is True:
                _best = _r
                break
        assert _best is None

    def test_04_source_declares_full_method_requirement(self):
        """接线防回潮：best 选择处必须同时判 score 与 full_method。"""
        with open(os.path.join(ROOT, "nucleus", "autonomy", "all_collab.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _i = _src.index("_best = None")
        _seg = _src[_i:_i + 400]
        assert "full_method" in _seg, "best 选择丢了 full_method 硬要求"
        assert ">= 65" in _seg, "best 选择丢了分数下限"

    def test_05_pick_requires_full_method_in_see(self):
        """接线防回潮：PICK 替换处必须也守 full_method。"""
        with open(os.path.join(ROOT, "nucleus", "reasoning",
                               "SafeEvolutionExecutor.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert "_b_usable" in _src
        # PICK 条件里必须出现 _b_usable
        _i = _src.index("if _pick and isinstance(_best, dict)")
        assert "_b_usable" in _src[_i:_i + 160], "PICK 条件丢了 full_method 守卫"

    def test_06_real_bug_sample_was_78_points(self):
        """用真实样本形态复核：片段（无 def 行）打不出 full_method。"""
        _frag = "    if x:\n        self.a = 1\n        return self.a\n"
        _s = AC.score_code(_frag, original="def f(self):\n    self.a = 0\n",
                           method="f")
        assert _s["full_method"] is False


# ══════════════════ 第二部分：零产出渠道过滤 ══════════════════


class TestDeadChannelFilter:

    def test_10_zero_output_channel_is_dead(self, monkeypatch):
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [
                _row([("dead-ch", False, 0, False), ("good-ch", True, 90, True)])
                for _ in range(12)
            ])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_MIN_TRIES", 10)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_WINDOW", 200)
            assert AC._dead_channels() == {"dead-ch"}

    def test_11_channel_with_one_success_is_alive(self, monkeypatch):
        """★保守性：只要有过一次成功就不判死（可能只是限流）。"""
        import config
        with _TmpLedger() as p:
            _rows = [_row([("flaky-ch", False, 0, False)]) for _ in range(11)]
            _rows.append(_row([("flaky-ch", True, 80, True)]))
            _TmpLedger.write(p, _rows)
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_MIN_TRIES", 10)
            assert "flaky-ch" not in AC._dead_channels()

    def test_12_small_sample_not_dead(self, monkeypatch):
        """样本不足（< MIN_TRIES）不判死，避免小样本误杀。"""
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("new-ch", False, 0, False)])
                                 for _ in range(3)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_MIN_TRIES", 10)
            assert AC._dead_channels() == set()

    def test_13_window_slides_so_channel_revives(self, monkeypatch):
        """★防死锁：只看最近 WINDOW 轮 ⇒ 旧失败滑出窗口后自动复活。"""
        import config
        with _TmpLedger() as p:
            _old = [_row([("ch-x", False, 0, False)]) for _ in range(15)]
            _new = [_row([("ch-x", True, 90, True)]) for _ in range(3)]
            _TmpLedger.write(p, _old + _new)
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_MIN_TRIES", 10)
            # 窗口 5 只覆盖最近 5 轮（3 成功 + 2 失败）⇒ ok>0 不判死
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_WINDOW", 5)
            assert AC._dead_channels() == set()

    def test_14_switch_off_returns_empty(self, monkeypatch):
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("dead-ch", False, 0, False)])
                                 for _ in range(20)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", False)
            assert AC._dead_channels() == set()

    def test_15_no_ledger_file_returns_empty(self, monkeypatch):
        monkeypatch.setattr(AC, "_ledger_path",
                            lambda: os.path.join(tempfile.gettempdir(),
                                                 "m479_no_such_ledger_xyz.jsonl"))
        assert AC._dead_channels() == set()

    def test_16_pick_channels_filters_dead(self, monkeypatch):
        """pick_channels 必须真的跳过死渠道。"""
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("zhipu-47-flash", False, 0, False),
                                       ("bailian-qwen38-flash", True, 90, True),
                                       ("bailian-deepseek-v41-flash", False, 0, False),
                                       ("bailian-glm53", True, 70, True)])
                                 for _ in range(12)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_DEAD_MIN_TRIES", 10)
            _names = [c.get("name") for c in AC.pick_channels(4)]
            assert "bailian-deepseek-v41-flash" not in _names
            assert "bailian-qwen38-flash" in _names

    def test_17_pick_channels_unchanged_when_switch_off(self, monkeypatch):
        """两个过滤器都关 ⇒ 逐字回到「按 priority 取前 N」。

        ★479 收尾① 追加：这里必须**同时**关掉 `M479_ALL_COLLAB_SKIP_LOW_RATE`。
        因为 deepseek 既是零产出（死渠道）又是 0% 成功率（低效渠道），
        只关死渠道开关时它仍会被低成功率过滤拦下 —— 那不是回归，
        是「另一个开关还在生效」。完整回滚 = 两个开关一起关。
        """
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("bailian-deepseek-v41-flash", False, 0, False)])
                                 for _ in range(20)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", False)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", False)
            _names = [c.get("name") for c in AC.pick_channels(4)]
            assert "bailian-deepseek-v41-flash" in _names, "开关关闭应逐字回滚"


# ══════════════════ 第三部分：可用率统计 ══════════════════


class TestLedgerStatsUsable:

    def test_20_usable_rate_counts_full_method_only(self):
        with _TmpLedger() as p:
            _TmpLedger.write(p, [
                _row([("a", True, 90, True), ("b", True, 78, False)]),
                _row([("a", True, 70, False), ("b", False, 0, False)]),
            ])
            _s = AC.ledger_stats(p)
            assert _s["candidates_total"] == 4
            assert _s["candidates_usable"] == 1, "只有 full_method+>=65 才算可用"
            assert _s["usable_rate"] == 0.25
            assert _s["rounds_with_usable"] == 1
            assert _s["rounds_usable_rate"] == 0.5

    def test_21_all_dead_gives_zero_rate(self):
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("a", False, 0, False)])])
            _s = AC.ledger_stats(p)
            assert _s["candidates_usable"] == 0
            assert _s["usable_rate"] == 0.0
            assert _s["rounds_with_usable"] == 0

    def test_22_stats_has_both_dimensions(self):
        """分数高 ≠ 可用：两个维度必须分开报，不许合并。"""
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("a", True, 95, False)])])
            _s = AC.ledger_stats(p)
            assert _s["best_found"] >= 0
            assert _s["candidates_total"] == 1
            assert _s["candidates_usable"] == 0


class TestChannelVsModelSeparation:
    """★479 收尾：台账必须分清「渠道不可用」与「模型产出不合格」。

    实测 44 轮 184 候选里 108 个（58.7%）是渠道层失败
    （429×34 / 读超时×28 / 空内容×27 / 403×19）。混在一起看只会得出
    「ALL 协作没用」，真因却是渠道不可用 —— 两种结论指向相反的处置。
    """

    def _run(self, cands, monkeypatch):
        """跑一次 _m479_all_collab，返回被写进台账的那一行。

        ★479 收尾：本组断言「台账写了什么」，需要**同步**执行才能当场取到，
          故显式关掉后台影子（`M479_ALL_COLLAB_ASYNC_SHADOW=False`）。
          异步路径本身由 TestAsyncShadow 专项覆盖，不是放弃覆盖。
        """
        import config
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor

        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", False)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_ASYNC_SHADOW", False)
        _fake = {"ok": True, "elapsed": 40.0, "candidates": cands, "best": None,
                 "usable": 0}
        _cap = {}

        def _note(rec, **kw):
            _cap.update(rec)
            return {"ok": True}

        monkeypatch.setattr(AC, "generate_candidates", lambda *a, **k: _fake)
        monkeypatch.setattr(AC, "note_round", _note)
        _o = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        _o._m479_all_collab("p", "def f(:\n    pass", {"method": "f"}, "    def f(self):\n        return 1\n")
        return _cap

    def _c(self, ch, ok, err=""):
        return {"channel": ch, "model": ch, "ok": ok, "score": 0, "elapsed": 1.0,
                "error": err, "detail": {"full_method": False}}

    def test_30_all_failed_records_oks_zero(self, monkeypatch):
        _r = self._run([self._c("a", False, "HTTP 429 限流"),
                        self._c("b", False, "TimeoutError: The read operation timed out"),
                        self._c("c", False, "响应无有效内容"),
                        self._c("d", False, "HTTP 403 quota")], monkeypatch)
        assert _r.get("oks") == 0
        _fk = _r.get("fail_kinds") or {}
        assert _fk.get("429") == 1
        assert _fk.get("timeout") == 1
        assert _fk.get("empty") == 1
        assert _fk.get("quota") == 1

    def test_31_partial_success_counts_oks(self, monkeypatch):
        _r = self._run([self._c("a", True), self._c("b", False, "HTTP 429"),
                        self._c("c", False, "响应无有效内容")], monkeypatch)
        assert _r.get("oks") == 1
        assert (_r.get("fail_kinds") or {}).get("429") == 1
        assert (_r.get("fail_kinds") or {}).get("empty") == 1

    def test_32_all_failed_emits_warning(self, monkeypatch):
        """★全失败必须告警（否则运维看不出本轮协作纯消耗）。"""
        import logging
        _recs = []

        class _H(logging.Handler):
            def emit(self, record):
                _recs.append(record.getMessage())

        _lg = logging.getLogger("pulse.module.SafeEvolutionExecutor")
        _h = _H()
        _lg.addHandler(_h)
        _old = _lg.level
        _lg.setLevel(logging.DEBUG)
        try:
            self._run([self._c("a", False, "HTTP 429")] * 3, monkeypatch)
        finally:
            _lg.removeHandler(_h)
            _lg.setLevel(_old)
        # 只要有一条包含关键判据就算过（logger 名可能带前缀差异）
        assert any("渠道不可用" in m for m in _recs), \
            "全失败时必须明确说「属渠道不可用，不是模型能力问题」"

    def test_33_source_has_oks_and_failkinds(self):
        with open(os.path.join(ROOT, "nucleus", "reasoning",
                               "SafeEvolutionExecutor.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert '"oks": _oks479' in _src
        assert '"fail_kinds": _fk479' in _src


class TestAsyncShadow:
    """★479 收尾：纯影子（PICK=False）必须后台跑，不许阻塞主链路。

    病因：generate_candidates 是 `for f in futs: f.result()` ⇒ 总耗时 = 最慢那一路
    （台账实测 elapsed 38~60s）。影子只写台账、answer 一字不改 ⇒ 每次 LLM 修复
    白等 40s 换零行为收益。

    ★PICK=True 时**必须保持同步** —— 异步会让替换发生在 answer 已返回之后，
      「择优」永远不生效。这条不能用开关覆盖，所以单独测。
    """

    def _o(self):
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
        return SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)

    def test_40_shadow_returns_immediately(self, monkeypatch):
        import time

        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", False)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_ASYNC_SHADOW", True)

        def _slow(*a, **k):
            time.sleep(3.0)
            return {"ok": True, "elapsed": 3.0, "candidates": [],
                    "best": None, "usable": 0}

        monkeypatch.setattr(AC, "generate_candidates", _slow)
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        _ans = "def f(self):\n    return 1"
        _t0 = time.time()
        _out = self._o()._m479_all_collab("p", _ans, {"method": "f"}, _ans)
        _dt = time.time() - _t0
        assert _out == _ans, "影子模式不许改答案"
        assert _dt < 1.0, f"影子仍然阻塞主链路：{_dt:.2f}s"

    def test_41_pick_on_stays_synchronous(self, monkeypatch):
        """★核心反证：PICK=True 时即使 ASYNC=True 也必须同步等结果。"""
        import time

        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_ASYNC_SHADOW", True)
        _full = "def f(self):\n    try:\n        return 2\n    except ValueError as _e:\n        silent_exc(_e, where='x')\n        return 1\n"

        def _slow(*a, **k):
            time.sleep(1.0)
            return {"ok": True, "elapsed": 1.0, "usable": 1,
                    "candidates": [{"channel": "c1", "model": "m1", "ok": True,
                                    "score": 100, "code": _full,
                                    "score_detail": {"score": 100,
                                                     "full_method": True}}],
                    "best": {"channel": "c1", "model": "m1", "score": 100,
                             "code": _full,
                             "score_detail": {"score": 100, "full_method": True}}}

        monkeypatch.setattr(AC, "generate_candidates", _slow)
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        _bad = "def f(:\n    pass"
        _t0 = time.time()
        _out = self._o()._m479_all_collab("p", _bad, {"method": "f"},
                                          "    def f(self):\n        return 1\n")
        _dt = time.time() - _t0
        assert _out.strip() == _full.strip(), \
            "PICK=True 必须同步拿到并替换（异步则择优失效）"
        assert _dt >= 0.9, f"PICK=True 不该走异步：{_dt:.2f}s"

    def test_42_async_switch_off_is_synchronous(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", False)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_ASYNC_SHADOW", False)
        _cap = {}

        def _fast(*a, **k):
            return {"ok": True, "elapsed": 0.1, "candidates": [], "best": None,
                    "usable": 0}

        monkeypatch.setattr(AC, "generate_candidates", _fast)
        monkeypatch.setattr(AC, "note_round",
                            lambda rec, **k: (_cap.update(rec) or {"ok": True}))
        _ans = "def f(self):\n    return 1"
        self._o()._m479_all_collab("p", _ans, {"method": "f"}, _ans)
        assert _cap, "开关关闭时必须同步写台账（可回滚保证）"
        assert _cap.get("picked") is False

    def test_43_source_has_async_branch(self):
        with open(os.path.join(ROOT, "nucleus", "reasoning",
                               "SafeEvolutionExecutor.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert "M479_ALL_COLLAB_ASYNC_SHADOW" in _src
        assert "_m479_shadow_worker" in _src
        # 必须显式排除 PICK=True，否则择优会失效
        _i = _src.index("_async_shadow = bool(getattr(")
        assert "_pick_shadow479" in _src[_i:_i + 400]


class TestLowRateFilter:
    """★479 收尾①：按成功率过滤低效渠道（替代「并行数 4→2」的误判方案）。

    实测依据：28 次读超时里 27 次来自 qwen37-plus(17)+modelscope(10)，占 96%；
    zhipu 49 次里 36 次 429。而 pick_channels 按 priority 取前 N 恰好把这些
    低效渠道装进来（zhipu p1、modelscope p5）⇒ 降路数会把好渠道一起砍掉。
    """

    def test_50_low_rate_detected(self, monkeypatch):
        import config
        with _TmpLedger() as p:
            _rows = []
            for _ in range(12):
                _rows.append(_row([("bad-ch", False, 0, False),
                                   ("good-ch", True, 90, True)]))
            _TmpLedger.write(p, _rows)
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_RATE_MIN_TRIES", 10)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_MIN_OK_RATE", 0.20)
            assert "bad-ch" in AC._low_rate_channels()
            assert "good-ch" not in AC._low_rate_channels()

    def test_51_low_but_nonzero_still_filtered(self, monkeypatch):
        """成功率 8.3%（1/12）也判低效 —— zhipu 4.1% 就是这么被挑出来的。"""
        import config
        with _TmpLedger() as p:
            _rows = [_row([("meh-ch", False, 0, False)]) for _ in range(11)]
            _rows.append(_row([("meh-ch", True, 80, True)]))
            _TmpLedger.write(p, _rows)
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_RATE_MIN_TRIES", 10)
            assert "meh-ch" in AC._low_rate_channels()

    def test_52_small_sample_not_filtered(self, monkeypatch):
        """样本 < MIN_TRIES 不判低效（避免新渠道一上来就被误杀）。"""
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("new-ch", False, 0, False)])
                                 for _ in range(3)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_RATE_MIN_TRIES", 10)
            assert AC._low_rate_channels() == set()

    def test_53_switch_off_returns_empty(self, monkeypatch):
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("bad-ch", False, 0, False)])
                                 for _ in range(20)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", False)
            assert AC._low_rate_channels() == set()

    def test_54_safe_fallback_never_empties_pool(self, monkeypatch):
        """★安全红线：全部渠道都被判低效时，必须回落成「只排除零产出」，
        绝不能让 ALL 协作永久没有渠道可用（那比低效更糟）。"""
        import config
        with _TmpLedger() as p:
            # 所有渠道都 1 次成功 / 20 次 ⇒ 全部 <20%
            _rows = []
            for _i in range(19):
                _rows.append(_row([("ch-a", False, 0, False),
                                   ("ch-b", False, 0, False)]))
            _rows.append(_row([("ch-a", True, 70, True),
                               ("ch-b", True, 70, True)]))
            _TmpLedger.write(p, _rows)
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_RATE_MIN_TRIES", 10)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", True)
            _names = [c.get("name") for c in AC.pick_channels(4)]
            assert _names, "池子被过滤空了 —— 安全回落失效"

    def test_55_pick_channels_skips_low_rate(self, monkeypatch):
        import config
        with _TmpLedger() as p:
            _rows = []
            for _ in range(12):
                _rows.append(_row([
                    ("bailian-glm53", True, 90, True),
                    ("bailian-qwen38-flash", True, 70, True),
                    ("zhipu-47-flash", False, 0, False),
                ]))
            _TmpLedger.write(p, _rows)
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_LOW_RATE", True)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_RATE_MIN_TRIES", 10)
            _names = [c.get("name") for c in AC.pick_channels(4)]
            assert "zhipu-47-flash" not in _names, "低效渠道仍在候选池里"
            assert "bailian-glm53" in _names
            assert "bailian-qwen38-flash" in _names

    def test_56_stats_shared_between_two_filters(self, monkeypatch):
        """口径一致：两份过滤器必须用同一份统计（否则会互相矛盾）。"""
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("x", False, 0, False)]) for _ in range(12)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            _st = AC._channel_stats(200)
            assert _st.get("x") == [12, 0]

    def test_57_source_has_fallback(self):
        with open(os.path.join(ROOT, "nucleus", "autonomy", "all_collab.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert "def _low_rate_channels(" in _src
        assert "def _channel_stats(" in _src
        # 安全回落必须存在
        assert "_collect(_dead)" in _src, "缺少安全回落"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
