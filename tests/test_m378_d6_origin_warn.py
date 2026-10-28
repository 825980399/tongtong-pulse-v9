# -*- coding: utf-8 -*-
"""★378 批盲测件 · origin 兜底新 caller 登记告警（P3）

任务书 §七：`_m40_resolve_origin` 兜底成 `system_internal` 时打一条 WARNING，
不阻塞只留痕；同一 caller 名只告警一次（否则热路径刷屏）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ===================================================================== 刀6
def test_m378_d6_unknown_caller_warns_once(caplog):
    """★核心：未登记 caller 兜底成 system_internal 时**只告警一次**（不刷屏）。"""
    from organs.body.PulseLung import PulseLung
    import logging as _lg

    with caplog.at_level(_lg.WARNING, logger="organs.body.PulseLung"):
        _a = PulseLung._m40_resolve_origin("m378_brand_new_caller", None)
        _b = PulseLung._m40_resolve_origin("m378_brand_new_caller", None)
    assert _a == _b == "system_internal"
    _hits = [r for r in caplog.records if "m378_brand_new_caller" in r.getMessage()]
    assert len(_hits) == 1, "同一 caller 名只应告警一次，实际 %d" % len(_hits)
    assert "ORIGIN_ALIASES" in _hits[0].getMessage()


def test_m378_d6_known_alias_does_not_warn(caplog):
    """负控：已登记 caller 与显式 origin 都不许告警（否则日志变成噪声源）。"""
    from organs.body.PulseLung import PulseLung
    import logging as _lg

    with caplog.at_level(_lg.WARNING, logger="organs.body.PulseLung"):
        assert PulseLung._m40_resolve_origin("user_dialog", None) == "user_query"
        assert PulseLung._m40_resolve_origin("evolution", None) == "evolution_task"
        assert PulseLung._m40_resolve_origin("whatever_unregistered", "user_query") == "user_query"
    assert not [r for r in caplog.records if "未登记的 caller" in r.getMessage()]
