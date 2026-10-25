# -*- coding: utf-8 -*-
"""367 刀2：executed＋applied 双字段回写口径对齐（T-executed回写缺口-1 · P1）。

铁律边界（本刀最要紧的一条）：
  沙箱执行成功 **≠** 源码落地 ⇒ `_write_back` 只补 `executed`，
  **绝不**写顶层 `applied=True`。`applied` 只归三处真实落地通道。
  写错这一笔会把"沙箱跑通"误标成"源码落地"，并污染 324 选择器/watcher/生命周期表。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus.evolution import patch_execution_dispatch as ped

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestWriteBack:
    def test_01_success_sets_executed(self):
        """沙箱成功 ⇒ 顶层 executed=1，且 executed_at 与 runtime_verify_result **同源**。

        刻意**不传** `execution_source`（走缺省分支）—— 同时验证刀2 附带修的那个
        `MotorEvent.EXECUTE.value` 崩溃（`MotorEvent.EXECUTE` 是字符串，无 `.value`）。
        """
        p = {"id": "k2_ok"}
        ped._write_back(p, {"success": True})
        assert p["executed"] == 1
        assert p["runtime_verified"] is True
        assert p["executed_at"] == p["runtime_verify_result"]["executed_at"], \
            "两个时间戳必须同源（否则读数可被误当两次不同执行）"

    def test_02_failure_sets_executed_but_no_applied(self):
        """失败也计 executed（执行过），但**不写 applied**（连 False 占位都不写）。"""
        p = {"id": "k2_fail"}
        ped._write_back(p, {"success": False, "error": "boom"})
        assert p["executed"] == 1, "失败也是执行过"
        assert "applied" not in p, "缺省语义必须保留：不写 applied（含 False 占位）"
        assert p["runtime_verified"] is False

    def test_03_ast_delete_mode_also_executed(self):
        """ast_delete 分支同口径补 executed（删除动作的校验来源不是 motor.execute）。"""
        p = {"id": "k2_ast"}
        ped._write_back(p, {"success": True, "execution_source": "ast_delete",
                            "def_line_actual": 42})
        assert p["executed"] == 1
        assert p["runtime_verify_mode"] == "ast_delete"
        assert p.get("o3_def_line_actual") == 42

    def test_04_success_path_never_writes_applied(self):
        """★核心边界：成功路径也**不得**写 applied（沙箱≠落地）。"""
        p = {"id": "k2_guard"}
        ped._write_back(p, {"success": True})
        assert "applied" not in p
        assert p.get("applied") is not True


class TestJudgeIntegration:
    def test_05_executed_enters_candidate_and_half_through(self):
        """补 executed 后：静态扫描能读成候选，缺 applied ⇒ half_through（不再被跳过）。"""
        from nucleus.evolution import zero_break_judge as zbj
        hist = [{"id": "k2_half", "executed": 1, "file": "nucleus/evolution/x_demo.py"}]
        r = zbj.scan_zero_break(hist, project_root=_ROOT)
        assert r["candidates_scanned"] == 1, "有 executed 必须进候选（原实现会 continue 跳过）"
        assert not r["any_broken"], "缺 applied 不能判破零"
        _ids = [x["id"] for x in r["half_ids"]]
        assert "k2_half" in _ids, "缺 applied 应判 half_through 而不是消失"
        _miss = next(x["missing"] for x in r["half_ids"] if x["id"] == "k2_half")
        assert any("applied" in m for m in _miss), _miss

    def test_06_applied_still_excluded_by_324(self):
        """324 选择器只看 `applied`，所以本刀补 executed **不会**把已落地条目重新放回池。"""
        _src = io.open(ped.__file__, encoding="utf-8").read()
        assert _src.count('if _p.get("applied") is True:') >= 2, \
            "324 的两处剔除口径必须仍在（1875 / 1981）"


class TestApplyWritePoints:
    def test_07_three_apply_owners_intact(self):
        """applied 三写入点归属复核（只读对照，本刀不改它们）。"""
        _expect = {
            "nucleus/reasoning/PatchManager.py": 1,
            "nucleus/evolution/patch_execution_dispatch.py": 1,
            "nucleus/evolution/ParamPatchManager.py": 2,
        }
        for _rel, _min in _expect.items():
            _src = io.open(os.path.join(_ROOT, _rel), encoding="utf-8").read()
            _n = _src.count('["applied"] = True')
            assert _n >= _min, "%s 的 applied=True 写入点少了：%d < %d" % (_rel, _n, _min)
        # dispatch 里那一处必须**不在** _write_back 内（本刀不往沙箱通道写 applied）
        _d = io.open(os.path.join(_ROOT, "nucleus/evolution/patch_execution_dispatch.py"),
                     encoding="utf-8").read()
        _wb = _d[_d.index("def _write_back"): _d.index("#: ★365刀A3")]
        assert '["applied"] = True' not in _wb, "_write_back 里出现了 applied=True"

    def test_08_write_back_only_adds_two_fields(self):
        """口径收敛：本刀只新增 executed / executed_at 两个键，不加别的。"""
        p = {"id": "k2_keys", "status": "pending"}
        _before = set(p)
        ped._write_back(p, {"success": True})
        _added = set(p) - _before
        assert "executed" in _added and "executed_at" in _added
        assert "applied" not in _added


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
