# -*- coding: utf-8 -*-
"""453 刀7：28s 三段式 + deadline 进渠道循环 + 重试限流。"""
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


def test_预算默认三段数值():
    b = EI.Budget(now=1000.0)
    # ★453R刀1 规格变更：28/18 → 34/24（覆盖 user_query p90=24.26s）
    assert b.total == 34.0 and b.first == 24.0 and b.retry == 8.0
    assert b.fallback == 0.2
    assert b.deadline == 1034.0 and b.stage1_deadline == 1024.0


def test_首试渠道超时被夹到18s():
    b = EI.Budget(now=1000.0)
    assert b.channel_timeout(30.0, now=1000.0, stage=1) == 24.0
    assert b.channel_timeout(5.0, now=1000.0, stage=1) == 5.0


def test_重试段被夹到8s而非吃掉剩余27s():
    b = EI.Budget(now=1000.0)
    assert b.channel_timeout(30.0, now=1001.0, stage=2) == 8.0
    assert b.channel_timeout(30.0, now=1025.0, stage=2) == 8.0


def test_should_try在阶段deadline后为False():
    b = EI.Budget(now=1000.0)
    assert b.should_try(now=1000.0, stage=1) is True
    assert b.should_try(now=1023.8, stage=1) is False
    assert b.should_try(now=1033.8, stage=2) is False


def test_can_retry剩余不足8s不允许():
    b = EI.Budget(now=1000.0)
    assert b.can_retry(now=1024.0) is True
    assert b.can_retry(now=1027.0) is False
    assert b.exhausted(now=1034.0) is True


def test_三段式端到端时间线不超28s():
    b = EI.Budget(now=1000.0)
    # ① 首试用满 18s 超时
    t1 = b.channel_timeout(30.0, now=1000.0, stage=1)
    assert t1 == 24.0
    # ② 还有 10s ⇒ 允许剔除证据重试
    assert b.can_retry(now=1024.0) is True
    t2 = b.channel_timeout(30.0, now=1024.0, stage=2)
    assert t2 == 8.0
    # ③ 总耗时 24+8=32s ≤ 34s
    assert 1000.0 + t1 + t2 <= b.deadline
    assert b.remaining(now=1032.0) == 2.0


def test_全局重试信号量上限2():
    got = []
    for _ in range(3):
        if EI.try_acquire_retry():
            got.append(1)
    assert len(got) == 2, got
    for _ in got:
        EI.release_retry()
    assert EI.try_acquire_retry() is True
    EI.release_retry()


def test_本地兜底话术固定不编造():
    # ★453R刀3 规格变更：单话术 → 三档（部分结果 / 无结果 / 快速失败）
    assert "补充一个具体场景" in EI.local_fallback()
    assert EI.local_fallback("quick_fail") == "稍后再试"
    assert "能确定的说一下" in EI.local_fallback("partial", "审批桥三闸有读数")
    assert EI.final_failure_result() == EI.local_fallback("no_result")
    assert EI.fixed_fallback_enabled() is True
    assert config.M453_USE_FIXED_FALLBACK is True


def test_每请求最多一次重试的标记():
    src = _src()
    # ★453R刀2/3：重试标记进 ContextVar，终态统一在重试块之后
    assert "_ei453.is_retry_in_flight()" in src
    assert "update_request_state(retrying=True, stage=2)" in src
    assert "_ei453.final_failure_result(_m453_partial)" in src


def test_deadline确实传进两层循环():
    src = _src()
    i_round = src.index("for _round in range(2):")
    i_ch = src.index("for _ch in _candidates:")
    i_check1 = src.index("not _m453_budget.should_try(stage=_m453_stage)", i_round)
    i_check2 = src.index("not _m453_budget.should_try(stage=_m453_stage)", i_ch)
    assert i_round < i_check1 < i_ch < i_check2, "两处预算检查顺序不对"


def test_单次渠道超时也夹到本阶段预算():
    src = _src()
    i_call = src.index("def _call_channel(")
    i_clamp = src.index("_m453_clamp(", i_call)
    assert i_clamp > i_call
    assert "_timeout = _cand453" in src


def test_鸭子类型守卫防测试替身误伤():
    # MagicMock 之类的假件返回值不是数值 ⇒ 不采纳 clamp
    src = _src()
    assert "isinstance(_cand453, (int, float))" in src


def test_剔除证据重试与第三段接线齐全():
    src = _src()
    # ★453R刀8：contextmanager 包 acquire/release，异常路径也归还
    for token in ("with _ei453.retry_slot() as _m453_got",
                  "_ei453.detach(_m453_orig_prompt)",
                  "_ei453.final_failure_result(_m453_partial)",
                  "_ei453.end_request_state()"):
        assert token in src, token


def test_非user_query不享受降级():
    src = _src()
    assert "if str(_m40_origin) == \"user_query\" and not _m453_retrying:" in src
    # 预算对象只在上述条件内构造
    i_gate = src.index("if str(_m40_origin) == \"user_query\" and not _m453_retrying:")
    i_budget = src.index("_m453_budget = _ei453.Budget(")
    assert i_gate < i_budget
