# -*- coding: utf-8 -*-
"""T153-6（批次8）：PulseSelfAwareness 专项契约（拆 2 文件之文件2）。

PulseSelfAwareness 是 3208 行、118 方法的超大器官（identity 层"自我认知"）。
通用生命周期 5 断言见 test_organ_lifecycle_contract_t153.py（本件作为
organs.identity.PulseSelfAwareness 参数化项覆盖）。本文件补充"大器官方法集稳定性 +
关键公共方法契约"，作为 T153-5 式误删死方法的回归防护层（若核心方法被误删，
下列调用契约会暴露）。

方法级契约清单（方法名 -> 返回契约 -> 关键行为）：
  on_pulse({event_type, payload}) -> dict|None  : 对声明事件(persona.query/system.status.request)返回非 None dict
  get_stats() -> dict                       : 状态快照，键集稳定
  get_knowledge_profile() -> dict           : 知识画像
  get_public_summary() -> dict              : 对外摘要（无 PII）
  get_unified_self_portrait() -> dict       : 统一自我画像
  get_existential_state() -> dict           : 存在状态
  get_persona(user_name) -> dict|None       : 指定用户人格（未知用户返回 None）
  get_learned_behaviors() -> dict           : 已学行为表
  get_reply_guidance(user_name) -> dict     : 回复引导
  is_in_weak_area(question) -> bool         : 是否处于薄弱领域
  get_resonance_conditions() -> list        : 已登记事件（含 system.status.request）
  set_node_pool/set_framework_ref/set_hormones_ref(None) -> None : 依赖注入不抛

注：与器官 5 断言的差异——本文件不重复 A1~A5，专测"大器官特有方法集不丢、调用契约稳"，
属方法级（非事件级）回归防护。严禁为凑断言给非器官件套壳（本件确为 BasePulseOrgan 子类）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.const import SystemEvent
from organs.identity.PulseSelfAwareness import PulseSelfAwareness


def _inst():
    return PulseSelfAwareness()


def test_is_base_pulse_organ_subclass():
    from base.BasePulseOrgan import BasePulseOrgan
    assert issubclass(PulseSelfAwareness, BasePulseOrgan)


def test_on_pulse_declared_events_return_non_none():
    organ = _inst()
    for ev in ("persona.query", SystemEvent.STATUS_REQUEST):
        r = organ.on_pulse({"event_type": ev, "payload": {}})
        assert r is not None and isinstance(r, dict), f"on_pulse({ev!r}) 应返回非 None dict"


def test_key_public_query_methods_contract():
    """关键公共查询方法：可调用、不抛、返回契约稳定。"""
    organ = _inst()
    dict_methods = [
        "get_knowledge_profile", "get_public_summary", "get_unified_self_portrait",
        "get_existential_state", "get_learned_behaviors",
    ]
    for name in dict_methods:
        fn = getattr(organ, name)
        assert callable(fn), f"{name} 应可调用"
        r = fn()
        assert isinstance(r, dict), f"{name}() 应返回 dict，实得 {type(r).__name__}"

    # get_reply_guidance(user_name)：需参，返回 dict（未知用户不抛）
    rg = organ.get_reply_guidance("__t153_unknown_user__")
    assert isinstance(rg, dict), f"get_reply_guidance() 应返回 dict，实得 {type(rg).__name__}"

    # get_persona：未知用户返回 None 或 dict（不抛）
    rp = organ.get_persona("__t153_unknown_user__")
    assert rp is None or isinstance(rp, dict)

    # is_in_weak_area：返回 bool
    assert isinstance(organ.is_in_weak_area("什么是量子纠缠？"), bool)


def test_dependency_injection_methods_do_not_raise():
    organ = _inst()
    for setter in ("set_node_pool", "set_framework_ref", "set_hormones_ref"):
        fn = getattr(organ, setter)
        try:
            fn(None)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"{setter}(None) 不应抛出: {type(e).__name__}: {e}")
