# -*- coding: utf-8 -*-
"""388刀3：严格嫌疑 3 个（设备管理器 / 耳朵 / 全局学习器）逐个查的结论。

★查证结果：**3 个全都不是真哑**，零业务代码改动，只登记豁免声明。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.cognitive import organ_emits as oe

THREE = ("PulseDeviceManager", "PulseEars", "PulseGlobalLearner")


def _emitter_hits(_cls):
    """该器官源码里的发射点个数（静态可判，不依赖当日运行数据）。"""
    from nucleus.cognitive.organ_inventory import scan_organ_classes as _soc
    for _o in _soc():
        if _o.get("class_name") == _cls:
            return oe.count_emitters(_o.get("file_path") or "")
    return 0


def test_m388_d3_all_three_have_manual_verdict():
    """★核心：3 个都有**可判定的结论**（人工复核 或 「已不是零发射」的事实）。

    ★429 修订（铁律 68 规格断言改钉）：原断言是「3 个都必须出现在 declarations」，
    但那依赖「3 个都还是零发射器官」这个**一次性事实**。实测 2026-11-02
    `scan_zero_emit()` 只返回 16 个类、PulseEars 不在其中（当日 source='耳朵'
    真实发射 1542 条）⇒ 原断言永久红，而代码是健康的。

    现改为两分支，判别力不降：
      · 仍在零发射清单 ⇒ 必须走人工复核且标 benign（原语义，一字不动）；
      · 已不在清单   ⇒ 必须**真有发射点**（`count_emitters > 0`）；
                        发射点为 0 却又不在清单 = 统计口径坏了，仍判红。
    """
    _r = oe.zero_emit_report()
    for _cls in THREE:
        _d = _r["declarations"].get(_cls)
        if _d is not None:
            assert _d.get("source") == "manual_388", f"{_cls} 没走人工复核"
            assert _d.get("benign") is True, f"{_cls} 未被确认非故障"
            assert "非故障" in _d["reason"], f"{_cls} 的理由没写清是非故障"
            continue
        # 不在声明表：必须能用「已不是零发射」解释，且证据可查
        _hits = _emitter_hits(_cls)
        assert _hits > 0, (
            f"{_cls} 既不在声明表、也没有发射点（count_emitters=0）"
            " ⇒ 要么是统计口径坏了，要么是真哑，必须人工介入")


def test_m388_d3_strict_suspects_empty_after_review():
    """★核心：查完后 `suspects_strict` 应为**空** —— 没有真哑的了。"""
    _r = oe.zero_emit_report()
    assert _r["suspects_strict"] == [], \
        f"还有未澄清的嫌疑: {_r['suspects_strict']}"


def test_m388_d3_device_manager_is_guard_clause():
    """设备管理器：早退型条件发射（`if hash == last: return`）。

    387刀3 的 `emit_guarded` **只认 if 包裹**，漏了早退型 ⇒ 本刀补上。
    """
    _src = oe.__dict__  # ★421/R1-B7：_ 前缀属 ruff dummy 名，F841 本就不报
    assert oe.emit_guarded("organs/core/PulseDeviceManager.py") is True


def test_m388_d3_ears_and_global_learner():
    """耳朵 / 全局学习器：仍是「条件发射」——有发射点，但不是每拍都发。

    ★429 修订：原断言直接 `_r["declarations"][_cls]`，对已退出零发射清单的类
    会 KeyError。现改为「在表里查理由、不在表里查发射点」，两类都必须有证据。
    """
    _r = oe.zero_emit_report()
    _notes = {"PulseEars": ("语音", "耳朵"), "PulseGlobalLearner": ("漂移",)}
    for _cls, _kws in _notes.items():
        _d = _r["declarations"].get(_cls)
        if _d is not None:
            assert _d["benign"] is True, _cls
            assert any(_k in _d["reason"] for _k in _kws), _cls
            continue
        # 已不是零发射：必须静态扫得到发射点（真哑则 count_emitters == 0）
        assert _emitter_hits(_cls) > 0, (
            f"{_cls} 已不在零发射清单却没有发射点 ⇒ 需重新查证")


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
        assert _cls in oe.MANUAL_NOTES_388, f"{_cls} 缺人工结论"
        assert len(oe.MANUAL_NOTES_388[_cls]) >= 30, "结论太短，说不清原因"


def test_m388_d3_no_business_code_changed():
    """★声明性验证：本刀**没改**这 3 个器官的业务文件（只加声明）。

    判据：这 3 个文件里不应出现 388 刀的标记 —— 真要修触发条件得单独成批。
    """
    for _f in ("organs/core/PulseDeviceManager.py",
               "organs/senses/PulseEars.py",
               "organs/core/PulseGlobalLearner.py"):
        with open(os.path.join(ROOT, _f), encoding="utf-8",
                     errors="ignore") as _fh:
            _s = _fh.read()
        assert "★388刀" not in _s, f"{_f} 被改了（本刀声明零业务改动）"
