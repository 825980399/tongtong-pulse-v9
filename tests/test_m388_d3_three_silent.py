# -*- coding: utf-8 -*-
"""388刀3：严格嫌疑 3 个（设备管理器 / 耳朵 / 全局学习器）逐个查的结论。

★查证结果：**3 个全都不是真哑**，零业务代码改动，只登记豁免声明。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.cognitive import organ_emits as oe  # noqa: E402

THREE = ("PulseDeviceManager", "PulseEars", "PulseGlobalLearner")


def test_m388_d3_all_three_have_manual_verdict():
    """★核心：3 个都登记了人工复核结论（`source=manual_388`）。"""
    _r = oe.zero_emit_report()
    for _cls in THREE:
        _d = _r["declarations"].get(_cls)
        assert _d is not None, "%s 不在声明表" % _cls
        assert _d.get("source") == "manual_388", "%s 没走人工复核" % _cls
        assert _d.get("benign") is True, "%s 未被确认非故障" % _cls
        assert "非故障" in _d["reason"], "%s 的理由没写清是非故障" % _cls


def test_m388_d3_strict_suspects_empty_after_review():
    """★核心：查完后 `suspects_strict` 应为**空** —— 没有真哑的了。"""
    _r = oe.zero_emit_report()
    assert _r["suspects_strict"] == [], \
        "还有未澄清的嫌疑: %s" % _r["suspects_strict"]


def test_m388_d3_device_manager_is_guard_clause():
    """设备管理器：早退型条件发射（`if hash == last: return`）。

    387刀3 的 `emit_guarded` **只认 if 包裹**，漏了早退型 ⇒ 本刀补上。
    """
    _src = oe.__dict__  # ★421/R1-B7：_ 前缀属 ruff dummy 名，F841 本就不报
    assert oe.emit_guarded("organs/core/PulseDeviceManager.py") is True


def test_m388_d3_ears_and_global_learner():
    """耳朵 / 全局学习器：发射点在方法里但触发条件在**调用方**，
    静态扫不到 ⇒ 靠人工登记（`benign=True`）。"""
    _r = oe.zero_emit_report()
    for _cls in ("PulseEars", "PulseGlobalLearner"):
        _d = _r["declarations"][_cls]
        assert _d["benign"] is True
        # 理由里要点明真因关键词，便于后人核对
        assert any(_k in _d["reason"] for _k in ("语音", "漂移")), _cls


def test_m388_d3_benign_not_counted_as_suspect():
    """★互斥：已确认非故障的不许再进嫌疑清单（否则等于没查）。"""
    _r = oe.zero_emit_report()
    _b = set(_r["benign_confirmed"])
    _s = set(_r["suspects_strict"])
    assert _b & _s == set(), "benign 与 suspect 重叠: %s" % (_b & _s)


def test_m388_d3_manual_notes_is_explicit_table():
    """人工结论必须是**显式表**（可审计），不能散在代码里。"""
    assert isinstance(oe.MANUAL_NOTES_388, dict)
    for _cls in THREE:
        assert _cls in oe.MANUAL_NOTES_388, "%s 缺人工结论" % _cls
        assert len(oe.MANUAL_NOTES_388[_cls]) >= 30, "结论太短，说不清原因"


def test_m388_d3_no_business_code_changed():
    """★声明性验证：本刀**没改**这 3 个器官的业务文件（只加声明）。

    判据：这 3 个文件里不应出现 388 刀的标记 —— 真要修触发条件得单独成批。
    """
    import io
    for _f in ("organs/core/PulseDeviceManager.py",
               "organs/senses/PulseEars.py",
               "organs/core/PulseGlobalLearner.py"):
        _s = io.open(os.path.join(ROOT, _f), encoding="utf-8", errors="ignore").read()
        assert "★388刀" not in _s, "%s 被改了（本刀声明零业务改动）" % _f
