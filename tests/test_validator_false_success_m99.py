"""T-99d 门控测试：进化验证器「假成功」口径修复。

红（修复前 / .bak_batch99 备份源码）：
  - verify_fix_from_logs 在 baseline_errors=0 且 after=0 时误判 effectiveness=1.0 + verified=True
  - patch_dedup.fix_detail_consistency 只改 detail 文案为「不可判定」，不把 effectiveness 字段同步为 None
绿（修复后）：
  - baseline=0 时 effectiveness=None、verified=False（不算修复成功，不参与平均）
  - baseline>0 且 after=0 仍正确判为成功（不影响真实有效修复）
  - fix_detail_consistency 在 baseline=0 时把 effectiveness 字段同步为 None
"""
import io
import logging
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution.patch_dedup import fix_detail_consistency  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import (  # noqa: E402
    SafeEvolutionExecutor,
)


def _read_src(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


class _FakeExec:
    """最小桩：仅提供 verify_fix_from_logs 所需的实例属性。"""
    _module_logger = logging.getLogger("t99d_fake")
    _count_errors_for_location = staticmethod(lambda *a, **k: 0)


class ValidatorFalseSuccessTest(unittest.TestCase):
    # ---- verify_fix_from_logs ----
    def test_baseline_zero_not_success(self):
        _patch = {
            "file": "organs/brain/PulseInnerWorld.py",
            "method": "_safe_eval_arithmetic",
            "fixed_at": time.time() - 400,  # elapsed > 300s
            "baseline_errors": 0,           # 无错误基线
        }
        _r = SafeEvolutionExecutor.verify_fix_from_logs(_FakeExec(), _patch)
        self.assertFalse(_r["verified"], "baseline=0 不应判为修复成功（假成功）")
        self.assertIsNone(_r["effectiveness"], "baseline=0 时 effectiveness 应为 None（无法验证）")

    def test_baseline_positive_after_zero_is_success(self):
        _patch = {
            "file": "organs/brain/PulseInnerWorld.py",
            "method": "_safe_eval_arithmetic",
            "fixed_at": time.time() - 400,
            "baseline_errors": 5,            # 真实错误基线
        }
        _r = SafeEvolutionExecutor.verify_fix_from_logs(_FakeExec(), _patch)
        self.assertTrue(_r["verified"], "baseline>0 且 after=0 应判为真实修复成功")
        self.assertEqual(_r["effectiveness"], 1.0)

    # ---- patch_dedup 字段同步 ----
    def test_dedup_sync_effectiveness_none_on_baseline_zero(self):
        _rec = {
            "runtime_verify_result": {
                "baseline": 0, "after_fix": 0,
                "effectiveness": 1.0,  # 旧口径假成功残留
                "detail": "修复前错误=0, 修复后错误=0, 效果=不可判定",
            }
        }
        _changed = fix_detail_consistency(_rec)
        self.assertTrue(_changed, "baseline=0 时应同步 effectiveness 字段")
        self.assertIsNone(_rec["runtime_verify_result"]["effectiveness"],
                          "effectiveness 字段应同步为 None（消除假成功）")

    # ---- 先红后绿：备份（修复前）源码证据 ----
    def test_red_evidence_backup_false_success(self):
        _bak = _read_src(".bak_batch99/nucleus/reasoning/SafeEvolutionExecutor.py")
        _cur = _read_src("nucleus/reasoning/SafeEvolutionExecutor.py")
        _red = "_effectiveness = 1.0 if _after == 0 else 0.5"
        self.assertIn(_red, _bak, "【红】备份版本含假成功口径 else 分支")
        self.assertNotIn(_red, _cur, "【绿】当前版本应已移除假成功口径")

    def test_red_evidence_backup_dedup_no_field_sync(self):
        _bak = _read_src(".bak_batch99/nucleus/evolution/patch_dedup.py")
        _cur = _read_src("nucleus/evolution/patch_dedup.py")
        _sync = 'if _b == 0 and _vr.get("effectiveness") is not None:'
        self.assertNotIn(_sync, _bak, "【红】备份版本 fix_detail_consistency 无字段同步")
        self.assertIn(_sync, _cur, "【绿】当前版本 fix_detail_consistency 已同步字段")


if __name__ == "__main__":
    unittest.main()
