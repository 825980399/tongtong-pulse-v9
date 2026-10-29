# -*- coding: utf-8 -*-
"""386刀4：零发射器官的**发射声明**门禁用例。

核心：零发射器官**必须**有声明（emits + reason），否则"关着"和"哑了"
永远分不清，下次还得重新翻一遍代码。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.cognitive import organ_emits as oe  # noqa: E402


def _mk_pairs(tmp_path, sources):
    """合成 pulse_pairs：只让给定器官"有发射"。"""
    _p = str(tmp_path / "pulse_pairs_test.jsonl")
    with io.open(_p, "w", encoding="utf-8", newline="\n") as _f:
        for _s in sources:
            _f.write(json.dumps({"type": "emit", "event_type": "x.y",
                                 "source": _s}, ensure_ascii=False) + "\n")
    return _p


def test_m386_d4_all_zero_emit_organs_declared(tmp_path):
    """★核心（票面验收）：零发射器官**一个不落**都有声明。"""
    _p = _mk_pairs(tmp_path, ["眼睛", "心脏"])
    _r = oe.zero_emit_report(_p)
    assert _r["zero_emit_count"] > 0
    assert _r["undeclared"] == [], "未声明的零发射器官: %s" % _r["undeclared"]
    assert _r["declared_count"] == _r["zero_emit_count"]
    assert oe.assert_all_declared(_r) is True


def test_m386_d4_declaration_has_emits_and_reason(tmp_path):
    """声明必须**两项齐全**：`emits`（True/False）+ `reason`（非空）。

    缺 reason 的声明等于没声明 —— 后人只知道"它不发"，不知道"为什么"。
    """
    _p = _mk_pairs(tmp_path, ["眼睛"])
    _r = oe.zero_emit_report(_p)
    for _cls, _d in _r["declarations"].items():
        assert isinstance(_d["emits"], bool), "%s 的 emits 不是布尔" % _cls
        assert _d.get("reason"), "%s 缺 reason" % _cls
        assert len(_d["reason"]) >= 10, "%s 的 reason 太短" % _cls


def test_m386_d4_missing_declaration_is_caught(tmp_path):
    """★反向：故意抽掉一条声明 ⇒ 门禁必须**红**（否则这个判据形同虚设）。"""
    _p = _mk_pairs(tmp_path, ["眼睛"])
    _r = oe.zero_emit_report(_p)
    assert oe.assert_all_declared(_r) is True
    _r["undeclared"] = [_r["declarations"] and list(_r["declarations"])[0]]
    assert oe.assert_all_declared(_r) is False


def test_m386_d4_emitter_counter_is_objective():
    """发射器计数是**源码事实**：前额叶确实有发射器，宪法守护确实没有。"""
    assert oe.count_emitters("organs/brain/PulseReflection.py") > 0, "前额叶应有发射器"
    assert oe.count_emitters("organs/identity/PulseSpiritConstitution.py") == 0
    assert oe.count_emitters("") == 0
    assert oe.count_emitters("no/such/file.py") == 0


def test_m386_d4_silent_vs_by_design_partition(tmp_path):
    """★分类必须**互斥且完备**：每个零发射器官非"真哑"即"设计不发"。

    两者之和 == 声明总数，且交集为空 —— 有重叠说明判据分叉了。
    """
    _p = _mk_pairs(tmp_path, ["眼睛"])
    _r = oe.zero_emit_report(_p)
    _a = set(_r["silent_suspects"])
    _b = set(_r["by_design"])
    assert _a & _b == set(), "两类重叠: %s" % (_a & _b)
    assert len(_a | _b) == len(_r["declarations"])


def test_m386_d4_reason_states_boundary():
    """★边界写进声明本身（铁律196）：`emits=True` 只说明"本该发却没发"，
    **不**说明为什么没发 —— 声明里必须显式写出来，否则读者会过度解读。"""
    _r = oe.zero_emit_report()
    for _cls in _r["silent_suspects"]:
        _t = _r["declarations"][_cls]["reason"]
        # ★387刀3 / 388刀3 两次同步（这个断言已被措辞变化打断两次）：
        #   别再钉**具体措辞**，改钉**语义** —— 理由必须说清"这是不是故障"。
        #   措辞演进：真哑待查 → 无输入 → 条件待命 → 人工确认非故障。
        assert len(_t) >= 20, "%s 的理由太短，说不清边界" % _cls
        assert any(_k in _t for _k in ("待查", "无输入", "非故障", "不是故障")), \
            "%s 的声明没说清是不是故障: %s" % (_cls, _t)


def test_m386_d4_schema_and_counts_plausible():
    """口径留证：schema 稳定；零发射数在合理区间（不是 0、不是全部）。"""
    _r = oe.zero_emit_report()
    assert _r["schema"] == "organ_emits/386.v1"
    assert 0 < _r["zero_emit_count"] < 57
    # 20261029 实测：27 个零发射，其中 10 个有发射器（真哑嫌疑）
    assert _r["silent_suspects"], "应有真哑嫌疑项（否则说明发射器扫描失效）"
