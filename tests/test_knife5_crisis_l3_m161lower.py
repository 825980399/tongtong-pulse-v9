#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批下 刀5 门控单测：危机关键词 L3 档输入侧产出路径（T-危机关键词检测-1）。

锁定的根因与修复：
  · 根因：_classify_crisis_level(:417) 的 L3 判定集 = {self_harm, suicide, violence_threat}，
    但旧输入侧只产出五类风险（identity_erosion / mission_distortion /
    relation_manipulation / knowledge_pollution / resource_trap）
    ⇒ **L3 分支恒不命中**，161段B B2/B3 的转介链「已接线但永不触发」。
  · 修复：config.RISK_PATTERNS 补 self_harm / violence_threat 词表
    + _scan_user_input 补**输入侧产出路径**（severity=critical）
    + 输入侧与发射侧共用 _is_crisis_enabled（单一真相源，ENABLE_CRISIS_REFERRAL 一键回退）。

★用例设计原则：
  1) 端到端（输入文本 → risks → crisis_level），而非只测词表存在；
  2) 必带**反例组**——正常生死讨论不得误判为危机（误判代价＝无端转介，大于漏判）；
  3) 开关关闭必须能整体回退（不产出、不转介）；
  4) 转介**非拦截**：危机路径不得产生 SecurityEvent.BLOCKED。
"""
import importlib.util
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load(module_path, name):
    spec = importlib.util.spec_from_file_location(name, module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk_organ():
    """装配一个**零副作用**的 PulseRiskPerception 实例。

    不走 __init__（BasePulseOrgan 初始化会触碰信息场/日志），改用 __new__
    手工补齐 _scan_user_input 所需字段；info_field/pulse_core 置 None
    ⇒ 告警与转介的**发射**被短路，但判定逻辑（本刀被测对象）照常执行。
    """
    C = _load(os.path.join(ROOT, "organs", "brain",
                           "PulseRiskPerception.py"),
              "PRP_k5").PulseRiskPerception
    o = C.__new__(C)
    o._scan_count = 0
    o._alert_count = 0
    o._alert_history = []
    o._max_alert_history = 50
    o._global_risk_level = 0.0
    o._recent_risk_types = {}
    o.self_awareness = None
    o._reply_guidance_provider = None
    o._lock = threading.Lock()
    o.info_field = None      # 短路告警发射
    o.pulse_core = None      # 短路转介发射
    o._log = lambda level, msg: None
    o._load_risk_patterns()
    o._load_crisis_patterns()
    return o


#: 危机类风险类型（L3 判定集内、本刀新增产出路径的两类）
CRISIS_TYPES = {"self_harm", "violence_threat"}

#: ★反例组：正常生死/疲惫讨论，**不得**被判危机（误判＝无端转介，伤害大于漏判）
NEGATIVE_CASES = (
    "人终有一死，所以要把每一天过好",
    "生命的意义是什么？",
    "我不想死，我还想看着曈曈长大",
    "今天加班累死了",
    "这部电影里主角最后死了，很感人",
    "死亡是哲学里经常讨论的话题",
)


class TestK5WordTable:
    def test_01_crisis_word_keys_exist(self):
        """词表侧：self_harm / violence_threat 两键已归一入 RISK_PATTERNS。"""
        import config
        rp = getattr(config, "RISK_PATTERNS", {})
        assert "self_harm" in rp, "缺 self_harm 词表"
        assert "violence_threat" in rp, "缺 violence_threat 词表"
        assert len(rp["self_harm"]) >= 10
        assert len(rp["violence_threat"]) >= 5

    def test_02_organ_loads_new_patterns(self):
        """器官侧：_load_risk_patterns 能装载新词表（含 config 缺失时的兜底）。"""
        o = _mk_organ()
        assert getattr(o, "SELF_HARM_PATTERNS", None), "SELF_HARM_PATTERNS 未装载"
        assert getattr(o, "VIOLENCE_THREAT_PATTERNS", None), "VIOLENCE_THREAT_PATTERNS 未装载"


class TestK5InputSideProduction:
    """★核心：输入侧必须能**产出**危机类风险（旧链路零产出 ⇒ L3 恒不触发）。"""

    def test_03_self_harm_input_produces_type(self):
        o = _mk_organ()
        res = o._scan_user_input("最近觉得活着没意思，什么都不想做", "tester")
        types = {r.get("type", "") for r in res.get("risks", [])}
        assert "self_harm" in types, f"未产出 self_harm，实得 {types}"

    def test_04_end_to_end_l3(self):
        """端到端：危机输入 → risks → _classify_crisis_level == L3。"""
        o = _mk_organ()
        res = o._scan_user_input("我已经想自杀了，撑不下去了", "tester")
        lvl = o._classify_crisis_level(res.get("risks", []))
        assert lvl == "L3", f"危机输入未定级 L3，实得 {lvl}"

    def test_05_violence_threat_input_produces_l3(self):
        o = _mk_organ()
        res = o._scan_user_input("你再这样我要杀了你", "tester")
        types = {r.get("type", "") for r in res.get("risks", [])}
        assert "violence_threat" in types, f"未产出 violence_threat，实得 {types}"
        assert o._classify_crisis_level(res.get("risks", [])) == "L3"

    def test_06_l3_wins_over_l2(self):
        """危机类与攻击类同时命中，取最高级 L3（不被 L2 抢先）。"""
        o = _mk_organ()
        res = o._scan_user_input("你只是AI，活着没意思，我想自杀", "tester")
        assert o._classify_crisis_level(res.get("risks", [])) == "L3"

    def test_07_no_false_positive_on_normal_text(self):
        """★反例组：正常生死/疲惫讨论一律不得产出危机类风险。"""
        o = _mk_organ()
        for text in NEGATIVE_CASES:
            res = o._scan_user_input(text, "tester")
            types = {r.get("type", "") for r in res.get("risks", [])}
            assert not (types & CRISIS_TYPES), f"误判危机：{text} → {types}"

    def test_08_crisis_is_critical_not_terminal(self):
        """危机类 severity=critical ⇒ risk_level 计 0.4，走告警而**非**终止（转介非拦截）。"""
        o = _mk_organ()
        res = o._scan_user_input("活着没意思", "tester")
        sh = [r for r in res.get("risks", []) if r.get("type") == "self_harm"]
        assert sh and sh[0].get("severity") == "critical"
        assert res.get("risk_level", 0.0) < 0.5, "危机输入不应落入 >=0.5 的终止/谨慎分支"


class TestK5SwitchRollback:
    def test_09_switch_on_by_default(self):
        o = _mk_organ()
        assert o._is_crisis_enabled() is True

    def test_10_switch_off_fully_rolls_back(self, monkeypatch):
        """★一键回退：ENABLE_CRISIS_REFERRAL=False ⇒ 不产出危机类风险（回到施工前）。"""
        import config
        monkeypatch.setattr(config, "ENABLE_CRISIS_REFERRAL", False)
        o = _mk_organ()
        assert o._is_crisis_enabled() is False
        res = o._scan_user_input("活着没意思，想自杀", "tester")
        types = {r.get("type", "") for r in res.get("risks", [])}
        assert not (types & CRISIS_TYPES), f"开关关闭仍产出危机类：{types}"
        assert o._classify_crisis_level(res.get("risks", [])) is None

    def test_11_switch_is_single_source(self):
        """★单一真相源：发射侧不再内联读开关，改为调用 _is_crisis_enabled。"""
        import inspect
        o = _mk_organ()
        src = inspect.getsource(type(o)._emit_crisis_referral)
        assert "_is_crisis_enabled" in src, "发射侧未复用统一开关方法（存在第二处定义风险）"


class TestK5ReferralNotBlocking:
    def test_12_emit_has_no_blocked_event(self):
        """转介**非拦截**：危机发射路径不得出现 SecurityEvent.BLOCKED。"""
        import inspect
        o = _mk_organ()
        src = inspect.getsource(type(o)._emit_crisis_referral)
        assert "BLOCKED" not in src, "危机转介路径出现 BLOCKED（转介≠拦截）"

    def test_13_l3_text_compliant(self):
        """L3 文案须遵守 B3 措辞红线（无第一人称自称 / 无身份词）。"""
        from nucleus.security import crisis_referral_text as crt
        crt._LIMITER.reset()
        txt = crt.get_crisis_text("L3")
        assert isinstance(txt, str) and txt.strip()
        assert crt.violates_red_line(txt) == [], f"L3 文案触红线：{crt.violates_red_line(txt)}"
        assert not txt.lstrip().startswith("我")

    def test_14_l3_cooling_prevents_livelock(self):
        """★冷却防活锁：L3 达频次上限后退化为静默兜底文案，不刷屏。"""
        from nucleus.security import crisis_referral_text as crt
        crt._LIMITER.reset()
        first = crt.get_crisis_text("L3")
        assert first == crt._CRISIS_TEXTS["L3"], "首次 L3 未给完整文案"
        for _ in range(crt.L3_MAX_HINTS - 1):
            crt.get_crisis_text("L3")
        assert crt._LIMITER.snapshot().get("L3") == crt.L3_MAX_HINTS
        # 超限后退化
        assert crt.get_crisis_text("L3") == crt._DEFAULT_TEXT
        crt._LIMITER.reset()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
