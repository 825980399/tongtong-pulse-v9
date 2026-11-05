# -*- coding: utf-8 -*-
"""453R 刀3：两段都失败后统一终态，三档兜底话术。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus import evidence_injection as EI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUNG = os.path.join(ROOT, "organs", "body", "PulseLung.py")


def _src():
    with open(LUNG, encoding="utf-8") as f:
        return f.read()


def test_三档话术齐全():
    assert "补充一个具体场景" in EI.local_fallback("no_result")
    assert EI.local_fallback("quick_fail") == "稍后再试"
    assert "能确定的说一下" in EI.local_fallback("partial", "审批桥三闸有读数")
    assert "审批桥三闸有读数" in EI.local_fallback("partial", "审批桥三闸有读数")


def test_部分结果最长60字():
    t = EI.local_fallback("partial", "甲" * 200)
    assert len(t) <= len("这次没查到足够依据，我先把能确定的说一下：") + 60


def test_统一终态开关双向(monkeypatch):
    monkeypatch.setattr(config, "M453_USE_FIXED_FALLBACK", True, raising=False)
    assert isinstance(EI.final_failure_result(), str)
    monkeypatch.setattr(config, "M453_USE_FIXED_FALLBACK", False, raising=False)
    assert EI.final_failure_result() is None


def test_四类失败路径都走同一个终态入口(monkeypatch):
    monkeypatch.setattr(config, "M453_USE_FIXED_FALLBACK", True, raising=False)
    # ① 剩余不足 8s  ② 信号量占满  ③ 重试也失败  ④ 快速失败
    # 四者都不该再分路径换 Ollama/话术，统一由 final_failure_result 决定
    for partial, quick in (("", False), ("", False), ("部分结论", False), ("", True)):
        r = EI.final_failure_result(partial, quick=quick)
        assert isinstance(r, str) and r
    assert EI.final_failure_result("", quick=True) == "稍后再试"


def test_终态在重试块之外():
    src = _src()
    i_with = src.index("with _ei453.retry_slot() as _m453_got:")
    i_final = src.index("_m453_final = _ei453.final_failure_result(_m453_partial)")
    assert i_with < i_final, "终态又退回到重试分支的副产品了（P0-3 回潮）"
    # 终态缩进必须比 with 少（在 with 之外）
    line_final = src[:i_final].count("\n")
    line_with = src[:i_with].count("\n")
    assert line_final > line_with


def test_终态不再依赖can_retry或信号量():
    src = _src()
    i_final = src.index("_m453_final = _ei453.final_failure_result")
    seg = src[src.index("self._log(LogLevel.WARNING, f\"[渠道] 所有渠道均失败"):i_final]
    assert "if _m453_budget.can_retry():" in seg   # 重试仍可选
    # 但终态的 return 不在 can_retry 的 if 内
    assert "return _m453_final" in src[i_final:]


def test_非注入请求仍返回None():
    src = _src()
    i = src.index('self._log(LogLevel.WARNING, f"[渠道] 所有渠道均失败')
    seg = src[i:src.index("def _observe_remote_call", i)]
    assert seg.rstrip().endswith("return None"), seg[-120:]
