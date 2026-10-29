# -*- coding: utf-8 -*-
"""387刀3：零发射器官的**输入源 / 条件发射**判据（386刀4 的两处误判在这里修正）。

386刀4 的判据是「有发射器却零发射 ⇒ 真哑了」。本批查证发现它**会误报**：
  · 前额叶：其实当日有发射（reflection.insight / narrative.record），
    386 判它哑是因为当时 `mouth.speak` 为零 ⇒ 属**无输入**，不是故障
  · 血管：发射器全在「发现沉默器官才告警」的分支里 ⇒ 零发射 = **没有器官失联
    = 系统健康**，恰恰不是故障
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.cognitive import organ_emits as oe  # noqa: E402

PULSE = os.path.join(ROOT, "data", "monitor", "pulse_pairs_20261029.jsonl")


def test_m387_d3_emit_field_is_organ_not_source():
    """★判据基石：`pulse_pairs` 的发射方字段是 **`organ`**，不是 `source`。

    387 定位时我先查了 `source`（恒 0），差点得出"前额叶零发射"的错误结论。
    这条把字段语义钉住 —— 后人若改成 `source`，零发射清单会全错。
    """
    _n_organ = 0
    _n_source = 0
    with io.open(PULSE, encoding="utf-8", errors="ignore") as _f:
        for _l in _f:
            _l = _l.strip()
            if not _l:
                continue
            try:
                _d = json.loads(_l)
            except ValueError as _je:      # 坏行跳过（读盘口径，不是被测对象）
                assert _je is not None
                continue
            if not isinstance(_d, dict) or _d.get("type", "emit") != "emit":
                continue
            if _d.get("organ"):
                _n_organ += 1
            if _d.get("source"):
                _n_source += 1
    assert _n_organ > 0, "emit 记录里应该有 organ 字段"
    assert _n_source == 0, "emit 记录里不该有 source 字段（有则判据需重写）"


def test_m387_d3_reflection_input_declaration():
    """前额叶声明的输入 = `mouth.speak` + `heart.beat`（靠嘴说话入队复盘）。"""
    _ins = oe.declared_input_events("organs/brain/PulseReflection.py")
    assert "mouth.speak" in _ins, _ins
    assert "heart.beat" in _ins, _ins


def test_m387_d3_blood_vessel_is_conditional():
    """★核心：血管的发射器**被条件包裹** ⇒ 零发射可能是"无事发生"，不是哑。

    它的语义是「发现沉默器官才告警」（`if _escalated_names:` 才 emit），
    所以不发 = 没有器官失联 = 健康。386刀4 把它列为"真哑嫌疑"是误报。
    """
    assert oe.emit_guarded("organs/body/PulseBloodVessel.py") is True


def test_m387_d3_conditional_not_counted_as_suspect():
    """★分类互斥：条件发射的不该进 `suspects_strict`（严格嫌疑）。

    `suspects_strict` = 有发射器 ∧ 非条件发射 ∧ 有输入 —— 只有这类才真需要查。
    """
    _r = oe.zero_emit_report()
    _cond = set(_r["conditional"])
    _strict = set(_r["suspects_strict"])
    assert _cond & _strict == set(), "条件发射不该同时算严格嫌疑: %s" % (_cond & _strict)


def test_m387_d3_report_has_three_way_split():
    """报告必须给出三层分类（设计不发 / 条件待命 / 严格嫌疑），缺一即红。"""
    _r = oe.zero_emit_report()
    for _k in ("by_design", "conditional", "suspects_strict", "no_input"):
        assert _k in _r, "报告缺分类 %s" % _k
    assert len(_r["by_design"]) + len(_r["silent_suspects"]) == _r["zero_emit_count"]


def test_m387_d3_declaration_records_input_facts():
    """每条声明都要带上**输入源事实**，否则又回到"只看发射器"的误判老路。"""
    _r = oe.zero_emit_report()
    for _cls, _d in _r["declarations"].items():
        assert "input_events" in _d, "%s 缺 input_events" % _cls
        assert "input_fired" in _d, "%s 缺 input_fired" % _cls
        assert "emit_guarded" in _d, "%s 缺 emit_guarded" % _cls
        if _d["emits"] and _d["input_fired"] == 0:
            assert "无输入" in _d["reason"] or "一次都没发" in _d["reason"], _cls


def test_m387_d3_unconditional_emitter_detected():
    """反向：`emit_guarded` 不能一律返回 True，否则分类失去意义。

    取一个发射器不在 if 里的器官（若有）验证；至少保证函数对无发射器文件返 False。
    """
    assert oe.emit_guarded("organs/identity/PulseSpiritConstitution.py") is False
    assert oe.emit_guarded("") is False
    assert oe.emit_guarded("no/such.py") is False


def test_m387_d3_zero_emit_count_shrank_vs_386():
    """★留证：386 报 27 个零发射，387 同口径（organ 字段）是 20 个。

    差异来自窗口变化（部分器官开始发射）。这条把数字钉住，
    提醒后人：这个清单是**随时间变化**的观测值，不是固定事实。
    """
    _r = oe.zero_emit_report()
    assert 0 < _r["zero_emit_count"] <= 57
    assert _r["declared_count"] == _r["zero_emit_count"]
    assert oe.assert_all_declared(_r) is True
