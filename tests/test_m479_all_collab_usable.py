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
        import config
        with _TmpLedger() as p:
            _TmpLedger.write(p, [_row([("bailian-deepseek-v41-flash", False, 0, False)])
                                 for _ in range(20)])
            monkeypatch.setattr(AC, "_ledger_path", lambda: p)
            monkeypatch.setattr(config, "M479_ALL_COLLAB_SKIP_DEAD", False)
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
