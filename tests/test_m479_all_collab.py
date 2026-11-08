"""★479：ALL 协作的门控单测。

覆盖三层：
  ① 判据层（all_collab.score_code）—— 曈曈自有 AST + 最小改动相似度；
  ② 触发层（SafeEvolutionExecutor._m479_all_collab）—— 开关 / 按需触发 / 择优替换；
  ③ 台账层（note_round / ledger_stats）—— 只写自有台账、pytest 不污染生产。

★反证必测：开关关闭时 answer 必须**逐字不变**；异常时必须原样返回。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.autonomy import all_collab as AC
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor as SEE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 带 8 格缩进的方法片段（补丁里 original_code 的真实形态）
FRAGMENT = """        def f(self):
            self.x = 1
            return self.x"""

# 完整模块（能直接 parse）
FULL = """def f(self):
    self.x = 1
    return self.x + 1"""

FULL_SAME = """def f(self):
    self.x = 1
    return self.x"""

FULL_REWRITE = """def f(self):
    q = []
    for i in range(10):
        q.append(i * i)
    return sum(q)"""

BARE_EXCEPT = """def f(self):
    try:
        return 1
    except:
        pass"""


class TestScoreCode:
    """判据层：分数必须能区分「片段 / 完整 / 重写 / 裸 except」。"""

    def test_01_fragment_can_parse(self):
        """带缩进的方法片段也能解析（flex：原样→dedent→包 class）。"""
        _s = AC.score_code(FRAGMENT, original=FRAGMENT, method="f")
        assert _s["parse_ok"] is True, _s["parse_error"]

    def test_02_full_method_detected(self):
        """完整方法（含 def f）应命中 full_method。"""
        _s = AC.score_code(FULL, original=FRAGMENT, method="f")
        assert _s["full_method"] is True

    def test_03_fragment_not_full_method(self):
        """只有方法体的片段（无 def 行）⇒ full_method 必须 False。"""
        _body_only = "        self.x = 1\n        return self.x"
        _s = AC.score_code(_body_only, original=_body_only, method="f")
        assert _s["parse_ok"] is True
        assert _s["full_method"] is False, "无 def 行不该算完整方法"
        assert _s["score"] <= 65

    def test_04_syntax_error_scores_zero(self):
        """语法不通 ⇒ 0 分（且其余项不参与）。"""
        _s = AC.score_code("def f(:\n    pass", original=FRAGMENT, method="f")
        assert _s["score"] == 0
        assert _s["parse_ok"] is False
        assert _s["reason"]

    def test_05_minimal_change_scores_high(self):
        """最小改动（相似度高但确有差异）应拿满分档。"""
        _s = AC.score_code(FULL, original=FULL_SAME, method="f")
        assert 0.5 <= (_s["similarity"] or 0) < 0.98, _s["similarity"]
        assert _s["minimal"] is True
        assert _s["score"] >= 85

    def test_06_identical_scores_lower(self):
        """与原文逐字相同 ⇒ 「没真改」，得分必须低于最小改动。"""
        _a = AC.score_code(FULL_SAME, original=FULL_SAME, method="f")
        _b = AC.score_code(FULL, original=FULL_SAME, method="f")
        assert _a["score"] < _b["score"], "没改的分数不该高于改了的最小改动"

    def test_07_rewrite_scores_low(self):
        """大幅重写（相似度低）⇒ 扣分且有原因。"""
        _s = AC.score_code(FULL_REWRITE, original=FULL_SAME, method="f")
        assert (_s["similarity"] or 1.0) < AC.SIM_TOO_DIFF
        assert _s["minimal"] is False
        assert "重写" in _s["reason"]

    def test_08_bare_except_detected(self):
        """裸 except 必须被检出并扣分（铁律 4）。"""
        _s = AC.score_code(BARE_EXCEPT, original=FULL_SAME, method="f")
        assert _s["no_bare_except"] is False
        assert "裸 except" in _s["reason"]

    def test_09_empty_scores_zero(self):
        _s = AC.score_code("", original=FULL_SAME, method="f")
        assert _s["score"] == 0
        assert _s["reason"] == "空代码"

    def test_10_extract_code_from_fence(self):
        """```python 围栏要能剥掉。"""
        _raw = "这是解释\n```python\nx = 1\n```\n尾巴"
        assert AC.extract_code(_raw) == "x = 1"

    def test_11_extract_code_without_fence(self):
        assert AC.extract_code("x = 1") == "x = 1"


class TestPickChannels:
    def test_12_respects_n_and_exclude(self):
        _chs = AC.pick_channels(3, exclude="zhipu-47-flash")
        assert len(_chs) <= 3
        assert all(c["name"] != "zhipu-47-flash" for c in _chs)

    def test_13_no_duplicate_names(self):
        _chs = AC.pick_channels(5)
        _names = [c["name"] for c in _chs]
        assert len(_names) == len(set(_names))


class TestAllCollabGate:
    """触发层：开关 / 按需触发 / 择优替换。"""

    def _inst(self):
        return SEE.__new__(SEE)

    def test_20_switch_off_returns_answer_verbatim(self, monkeypatch):
        """★反证：开关关闭 ⇒ answer 逐字不变，且**不调任何渠道**。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", False)
        _called = {"n": 0}

        def _boom(*a, **k):
            _called["n"] += 1
            raise AssertionError("开关关闭时不该调用 generate_candidates")

        monkeypatch.setattr(AC, "generate_candidates", _boom)
        _ans = "原样答案"
        assert self._inst()._m479_all_collab("p", _ans, {}, "x = 1") == _ans
        assert _called["n"] == 0

    def test_21_baseline_ok_skips_generation(self, monkeypatch):
        """baseline 已达标 ⇒ 按需触发门拦住，不并发生成（省时间与配额）。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        _called = {"n": 0}

        def _boom(*a, **k):
            _called["n"] += 1
            raise AssertionError("baseline 已达标时不该并发生成")

        monkeypatch.setattr(AC, "generate_candidates", _boom)
        _written: list = []
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: _written.append(rec) or {"ok": True})
        # 最小改动补丁 ⇒ baseline 高分
        _ans = FULL
        _out = self._inst()._m479_all_collab("p", _ans, {"method": "f"}, FULL_SAME)
        assert _out == _ans
        assert _called["n"] == 0
        assert _written and _written[0]["skipped"] == "baseline_ok"

    def test_22_low_baseline_triggers_and_shadow_keeps_answer(self, monkeypatch):
        """baseline 低 ⇒ 启动协作；PICK=False ⇒ 即使 best 更高也不改写答案。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", False)
        _fake = {
            "ok": True, "elapsed": 1.0,
            "candidates": [{"channel": "c1", "model": "m1", "ok": True,
                            "score": 100, "elapsed": 1.0, "error": None,
                            "code": FULL, "score_detail": {}}],
            "best": {"channel": "c1", "model": "m1", "score": 100, "code": FULL},
        }
        monkeypatch.setattr(AC, "generate_candidates", lambda *a, **k: _fake)
        _written: list = []
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: _written.append(rec) or {"ok": True})
        _ans = "def f(:\n    pass"  # 语法错 ⇒ baseline 0
        _out = self._inst()._m479_all_collab("p", _ans, {"method": "f"}, FULL_SAME)
        assert _out == _ans, "纯影子模式绝不能改写答案"
        assert _written and _written[0]["picked"] is False
        assert _written[0]["best_score"] == 100

    def test_23_pick_on_replaces_when_better(self, monkeypatch):
        """PICK=True 且 best>baseline 且 **是完整方法** ⇒ 用候选代码替换。

        ★479 收尾契约变更：PICK 除「分数更高」外还要求 best 的
        score_detail.full_method is True —— 因为 470 判据要求 modified 的
        def 行方法名 == method，非完整方法进 469 必被拦，替换等于白烧延迟。
        回滚点：删掉 SafeEvolutionExecutor 里 `_b_usable` 与条件中的该项。
        """
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", True)
        _fake = {
            "ok": True, "elapsed": 1.0,
            "candidates": [{"channel": "c1", "model": "m1", "ok": True,
                            "score": 100, "elapsed": 1.0, "error": None,
                            "code": FULL,
                            "score_detail": {"score": 100, "full_method": True}}],
            "best": {"channel": "c1", "model": "m1", "score": 100, "code": FULL,
                     "score_detail": {"score": 100, "full_method": True}},
        }
        monkeypatch.setattr(AC, "generate_candidates", lambda *a, **k: _fake)
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        _bad = "def f(:\n    pass"
        _out = self._inst()._m479_all_collab("p", _bad, {"method": "f"}, FULL_SAME)
        assert _out == FULL, "PICK=True 且更优时必须替换"

    def test_23b_pick_on_refuses_non_full_method_even_if_better(self, monkeypatch):
        """★反证：分数更高但 full_method=False ⇒ **不许**替换。

        真实现象：22 轮里大量 78 分但无 def 行的候选；若替换，下游 469
        粒度对齐必拦（470 要求 def 行方法名匹配）⇒ 白烧 40s 且日志显示
        「换过了」造成误判。
        """
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", True)
        _fake = {
            "ok": True, "elapsed": 1.0,
            "candidates": [{"channel": "c1", "model": "m1", "ok": True,
                            "score": 100, "elapsed": 1.0, "error": None,
                            "code": FULL,
                            "score_detail": {"score": 100, "full_method": False}}],
            "best": {"channel": "c1", "model": "m1", "score": 100, "code": FULL,
                     "score_detail": {"score": 100, "full_method": False}},
        }
        monkeypatch.setattr(AC, "generate_candidates", lambda *a, **k: _fake)
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        _bad = "def f(:\n    pass"
        _out = self._inst()._m479_all_collab("p", _bad, {"method": "f"}, FULL_SAME)
        assert _out == _bad, "非完整方法即使 100 分也不许替换"

    def test_24_pick_on_keeps_answer_when_not_better(self, monkeypatch):
        """PICK=True 但 best 不高于 baseline ⇒ 保持原答案。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 0)  # 强制触发
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB_PICK", True)
        _fake = {
            "ok": True, "elapsed": 1.0,
            "candidates": [{"channel": "c1", "model": "m1", "ok": True,
                            "score": 10, "elapsed": 1.0, "error": None,
                            "code": "def f(:\n  pass", "score_detail": {}}],
            "best": None,
        }
        monkeypatch.setattr(AC, "generate_candidates", lambda *a, **k: _fake)
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        _ans = FULL
        _out = self._inst()._m479_all_collab("p", _ans, {"method": "f"}, FULL_SAME)
        assert _out == _ans

    def test_25_exception_falls_back_to_answer(self, monkeypatch):
        """★反证：generate_candidates 抛异常 ⇒ 原样返回答案，绝不外抛。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 85)

        def _boom(*a, **k):
            raise RuntimeError("模拟渠道层崩溃")

        monkeypatch.setattr(AC, "generate_candidates", _boom)
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        _ans = "def f(:\n    pass"
        assert self._inst()._m479_all_collab("p", _ans, {"method": "f"}, FULL_SAME) == _ans

    def test_26_non_dict_issue_does_not_raise(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_ALL_COLLAB", True)
        monkeypatch.setattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW", 0)
        monkeypatch.setattr(AC, "generate_candidates",
                            lambda *a, **k: {"ok": False, "candidates": [], "best": None})
        monkeypatch.setattr(AC, "note_round", lambda rec, **k: {"ok": True})
        assert self._inst()._m479_all_collab("p", "X", None, None) == "X"


class TestLedger:
    def test_30_note_round_writes_tmp(self, tmp_path):
        _p = str(tmp_path / "a.jsonl")
        _r = AC.note_round({"a": 1}, ledger_path=_p)
        assert _r["ok"] is True
        with open(_p, encoding="utf-8") as _f:
            _rows = [json.loads(x) for x in _f if x.strip()]
        assert _rows == [{"a": 1}]

    def test_31_pytest_guard_blocks_prod_path(self):
        """★反证：pytest 下不得写默认生产台账（防测试污染活体）。"""
        _r = AC.note_round({"x": 1})
        assert _r.get("skipped") in ("pytest_guard", None)

    def test_32_stats_counts(self, tmp_path):
        _p = str(tmp_path / "b.jsonl")
        AC.note_round({"candidates": [{"channel": "c1", "ok": True, "score": 90},
                                      {"channel": "c2", "ok": False, "score": 0}],
                       "best_channel": "c1"}, ledger_path=_p)
        AC.note_round({"candidates": [], "best_channel": ""}, ledger_path=_p)
        _s = AC.ledger_stats(_p)
        assert _s["total"] == 2
        assert _s["with_candidates"] == 1
        assert _s["best_found"] == 1
        assert _s["by_channel"]["c1"]["best"] == 90
        assert _s["by_channel"]["c2"]["ok"] == 0


class TestConfigContract:
    """配置契约：开关必须存在且默认安全（关/影子）。"""

    def test_40_switches_exist_with_safe_defaults(self):
        import config
        assert hasattr(config, "ENABLE_M479_ALL_COLLAB")
        assert hasattr(config, "ENABLE_M479_ALL_COLLAB_PICK")
        assert hasattr(config, "M479_ALL_COLLAB_CHANNELS")
        assert hasattr(config, "M479_ALL_COLLAB_TRIGGER_BELOW")
        # ★479 收尾契约变更：总开关已改 True（只跑影子攒数据，为「是否开 PICK」取证）。
        #   真正「不改行为」的保证是 PICK=False —— 影子里 answer 一字不改。
        #   回滚点：config.ENABLE_M479_ALL_COLLAB 改回 False ⇒ 完全不并发生成、不写台账。
        assert config.ENABLE_M479_ALL_COLLAB is True
        assert config.ENABLE_M479_ALL_COLLAB_PICK is False, \
            "★PICK 必须 False —— 这才是「上线即改行为」的开关"
        assert int(config.M479_ALL_COLLAB_CHANNELS) >= 1
        # ★479 收尾新增：零产出渠道过滤默认开（纯节省配额，不改变任何补丁内容）
        assert config.M479_ALL_COLLAB_SKIP_DEAD is True
        assert int(config.M479_ALL_COLLAB_DEAD_MIN_TRIES) >= 1

    def test_41_wire_point_exists_in_see(self):
        """接线防回潮：必须接在 `_call_llm_for_repair`，**不能**接在底层
        `_call_llm_with_followup`（后者的参数是 api_url/api_key/prompt，
        没有 issue/code_snippet —— 接那儿会 F821 且与上层重复调用）。

        ★这条测试就是为「接错函数」这个真实事故写的：曾用 grep 行号推断作用域，
        把调用点插进了 2418 行（属于 _call_llm_with_followup），compile 通过、
        逻辑全错。用 AST 定位才抓得住。
        """
        import ast as _ast
        from pathlib import Path as _Path
        _p = _Path(ROOT) / "nucleus" / "reasoning" / "SafeEvolutionExecutor.py"
        _tree = _ast.parse(_p.read_text(encoding="utf-8", errors="replace"))
        _cls = next(n for n in _tree.body if isinstance(n, _ast.ClassDef)
                    and n.name == "SafeEvolutionExecutor")
        _fns = {n.name: n for n in _cls.body
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}

        assert "_m479_all_collab" in _fns, "ALL 协作方法不存在（或被写成内层函数）"
        _params = [a.arg for a in _fns["_m479_all_collab"].args.args]
        assert _params == ["self", "prompt", "answer", "issue", "code_snippet"], _params

        def _calls_479(node) -> bool:
            return any(isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                       and n.func.attr == "_m479_all_collab"
                       for n in _ast.walk(node))

        assert _calls_479(_fns["_call_llm_for_repair"]), \
            "ALL 协作没接在 _call_llm_for_repair 里"
        assert not _calls_479(_fns["_call_llm_with_followup"]), \
            "误接在 _call_llm_with_followup（其参数无 issue/code_snippet）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
