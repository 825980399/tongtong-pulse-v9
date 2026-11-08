"""★479：`problem_gone` 静态判据探针的门控单测。

守住四件事：
  ① 每个 issue_type 的判据**正反例都成立**（不能只会判 True）；
  ② 拿不准的一律 False + supported=False（宁可误判 False，不可假 True）；
  ③ `backfill` 判 False 时**逐字返回副本**（不写任何反证据）；
  ④ 端到端：真实 pending 里那条低风险补丁，回填后能过审批五/三闸。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import problem_gone_probe as PG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 构造样本（都是「方法片段」形态，与真实补丁一致）
FRAG_RAW = "    def f(self):\n        open(self.p,'w').write(s)\n"
FRAG_ATOMIC = ("    def f(self):\n        safe_write_json(self.p, s, indent=2)\n")
FRAG_ATOMIC_AND_RAW = ("    def f(self):\n        safe_write_json(self.p, s)\n"
                       "        open(self.q,'w').write(s)\n")
FRAG_REENTRY = ("    def f(self):\n        if getattr(self,'_in_progress',False):\n"
                "            return\n        self.x = 1\n")
FRAG_REENTRY_NO_EXIT = "    def f(self):\n        self._in_progress = True\n        self.x = 1\n"
FRAG_BARE = "    def f(self):\n        try:\n            return 1\n        except:\n            pass\n"
FRAG_CLEAN = ("    def f(self):\n        try:\n            return 1\n"
              "        except Exception as e:\n            silent_exc(e, where='x')\n")
FRAG_WITH_PRINT = "    def f(self):\n        print(1)\n"
FRAG_WITH_LOG = "    def f(self):\n        _module_logger.info('x')\n"


def _long(n: int) -> str:
    return "def f(self):\n" + "    x = 1\n" * (n - 1)


def _probe(it: str, o: str, m: str, monkeypatch) -> dict:
    import config
    monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)
    return PG.probe({"issue_type": it, "original_code": o, "modified_code": m})


class TestNonAtomicWrite:
    def test_01_atomic_true(self, monkeypatch):
        _r = _probe("non_atomic_write", FRAG_RAW, FRAG_ATOMIC, monkeypatch)
        assert _r["gone"] is True and _r["supported"] is True
        assert _r["confidence"] == "high"

    def test_02_still_raw_false(self, monkeypatch):
        """★反证：没改原子写 ⇒ False。"""
        _r = _probe("non_atomic_write", FRAG_RAW, FRAG_RAW, monkeypatch)
        assert _r["gone"] is False

    def test_03_atomic_but_raw_remains_false(self, monkeypatch):
        """★反证：加了原子写但**旁边还留着裸写** ⇒ 不判消失（可能只是新增路径）。"""
        _r = _probe("non_atomic_write", FRAG_RAW, FRAG_ATOMIC_AND_RAW, monkeypatch)
        assert _r["gone"] is False
        assert "裸写" in _r["why"]


class TestReentry:
    def test_10_guard_true(self, monkeypatch):
        _r = _probe("periodic_task_no_reentry", "    def f(self):\n        self.x=1\n",
                    FRAG_REENTRY, monkeypatch)
        assert _r["gone"] is True

    def test_11_marker_without_early_exit_false(self, monkeypatch):
        """★反证：只设标志、没有提前 return ⇒ 不算守卫。"""
        _r = _probe("periodic_task_no_reentry", "    def f(self):\n        self.x=1\n",
                    FRAG_REENTRY_NO_EXIT, monkeypatch)
        assert _r["gone"] is False


class TestLongMethod:
    def test_20_crossed_limit_true(self, monkeypatch):
        """219→175：原文超 200、修改后不超 ⇒ True（真实补丁 5d3b 的形态）。"""
        _r = _probe("long_method", _long(219), _long(175), monkeypatch)
        assert _r["gone"] is True
        assert _r["evidence"]["limit"] == 200

    def test_21_still_over_limit_false(self, monkeypatch):
        """★反证：400→300 降了 25% 但 300 仍 > 200 ⇒ 问题没消失。"""
        _r = _probe("long_method", _long(400), _long(300), monkeypatch)
        assert _r["gone"] is False
        assert "仍超线" in _r["why"]

    def test_22_orig_not_long_false(self, monkeypatch):
        """★反证：原文本来就不超 200 ⇒ 不构成 long_method。"""
        _r = _probe("long_method", _long(150), _long(140), monkeypatch)
        assert _r["gone"] is False
        assert "不构成" in _r["why"]


class TestSilentException:
    def test_30_removed_true(self, monkeypatch):
        _r = _probe("silent_exception", FRAG_BARE, FRAG_CLEAN, monkeypatch)
        assert _r["gone"] is True

    def test_31_still_bare_false(self, monkeypatch):
        _r = _probe("silent_exception", FRAG_BARE, FRAG_BARE, monkeypatch)
        assert _r["gone"] is False

    def test_32_orig_clean_false(self, monkeypatch):
        """★反证：原文就没裸 except ⇒ 问题不成立，不许判 True。"""
        _r = _probe("silent_exception", FRAG_CLEAN, FRAG_CLEAN, monkeypatch)
        assert _r["gone"] is False


class TestPrint:
    def test_40_replaced_true(self, monkeypatch):
        _r = _probe("print_instead_of_log", FRAG_WITH_PRINT, FRAG_WITH_LOG, monkeypatch)
        assert _r["gone"] is True

    def test_41_still_print_false(self, monkeypatch):
        _r = _probe("print_instead_of_log", FRAG_WITH_PRINT, FRAG_WITH_PRINT, monkeypatch)
        assert _r["gone"] is False


class TestUnknownAndGuards:
    def test_50_unknown_type_not_supported(self, monkeypatch):
        """★反证：没有静态判据的类别必须 supported=False（交人工/沙箱）。"""
        _r = _probe("Traceback", "a", "b", monkeypatch)
        assert _r["supported"] is False
        assert _r["gone"] is False

    def test_51_switch_off(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", False)
        _r = PG.probe({"issue_type": "non_atomic_write",
                       "original_code": FRAG_RAW, "modified_code": FRAG_ATOMIC})
        assert _r["gone"] is False
        assert "开关" in _r["why"]

    def test_52_empty_modified(self, monkeypatch):
        _r = _probe("non_atomic_write", FRAG_RAW, "", monkeypatch)
        assert _r["gone"] is False

    def test_53_bad_patch_no_raise(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)
        assert PG.probe(None)["gone"] is False
        assert PG.probe({})["gone"] is False


class TestBackfill:
    def test_60_fills_when_gone(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)
        _p = PG.backfill({"issue_type": "non_atomic_write",
                          "original_code": FRAG_RAW, "modified_code": FRAG_ATOMIC})
        assert _p["problem_gone"] is True
        assert _p["confidence"] == "high"
        assert _p.get("problem_gone_why")

    def test_61_no_negative_evidence_when_false(self, monkeypatch):
        """★反证：判 False 时**不得**写入 problem_gone=False。

        写了就等于把「还没验证」变成「已验证不消失」，比不填更糟。
        """
        import config
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)
        _src = {"issue_type": "non_atomic_write", "original_code": FRAG_RAW,
                "modified_code": FRAG_RAW, "id": "x"}
        _p = PG.backfill(_src)
        assert "problem_gone" not in _p
        assert "confidence" not in _p
        # 其余字段逐字保留
        assert _p["id"] == "x" and _p["issue_type"] == "non_atomic_write"

    def test_62_does_not_mutate_input(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)
        _src = {"issue_type": "non_atomic_write", "original_code": FRAG_RAW,
                "modified_code": FRAG_ATOMIC}
        PG.backfill(_src)
        assert "problem_gone" not in _src, "不得改动入参 patch"


class TestEndToEndRealPatch:
    """★端到端：真实 pending 里那条低风险补丁，回填后应能过审批闸。"""

    def _rows(self):
        import json
        _p = os.path.join(ROOT, "data", "patches", "pending_patches.json")
        if not os.path.isfile(_p):
            pytest.skip("pending_patches 不存在")
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, list) else (_d.get("patches") or [])

    def test_70_reentry_patch_passes_after_backfill(self, monkeypatch):
        import config
        from nucleus.evolution import approval_bridge as ab
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)

        _row = next((r for r in self._rows()
                     if r.get("status") == "verified"
                     and r.get("issue_type") == "periodic_task_no_reentry"), None)
        if _row is None:
            pytest.skip("没有 periodic_task_no_reentry 的 verified 补丁")

        _before = ab.decision(_row)
        _after = ab.decision(PG.backfill(_row))
        assert _before["approved"] is False, "前提：回填前必须不过（否则测不到东西）"
        assert _after["approved"] is True, (
            f"回填后应通过，实际未过: "
            f"{[k for k, v in _after['gates'].items() if not v['ok']]}")

    def test_71_high_risk_patch_still_fails(self, monkeypatch):
        """★反证：高风险补丁回填后**仍须失败**（判据不许放水）。"""
        import config
        from nucleus.evolution import approval_bridge as ab
        monkeypatch.setattr(config, "ENABLE_M479_PROBLEM_GONE_PROBE", True)
        _row = next((r for r in self._rows()
                     if r.get("status") == "verified" and r.get("risk_level") == "高"), None)
        if _row is None:
            pytest.skip("没有高风险的 verified 补丁")
        assert ab.decision(PG.backfill(_row))["approved"] is False


class TestWiring:
    def test_80_approval_bridge_calls_backfill(self):
        from pathlib import Path
        _src = (Path(ROOT) / "nucleus" / "evolution" / "approval_bridge.py").read_text(
            encoding="utf-8", errors="replace")
        assert "_m479_backfill(_m460_patch)" in _src, "探针没接进审批链路"
        assert "_m460_patch = _m479_p" in _src, "必须用回填后的副本继续"
        # 判据不许被改：decision 里第五闸的原始判据必须还在
        assert '_put("problem_gone_true", _gone is True' in _src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
