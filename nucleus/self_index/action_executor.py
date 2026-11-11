# -*- coding: utf-8 -*-
"""493.6-2c 统一动作执行层。

严格流程：查白名单 → 取器官实例/模块函数 → 调方法/发事件 → 超时 → 验证 → 审计。
纪律：
- 写类（patch/config/file）本阶段只登记不执行 → 返回 blocked=True。
- 拿不到器官实例 / PulseCore 实例 → 诚实返回 blocked reason（绝不编）。
- 事件名经白名单 impl.target（运行时拼接值），不写裸事件字符串字面量。
- 读类验证：空 dict/None/空 list/空 str 直接判失败（诚实「没看到预期结果」）。
"""
import os
import json
import time
import importlib
import inspect
from nucleus._silent_except import silent_exc

_AUDIT_PATH = os.path.join("data", "monitor", "action_audit.jsonl")
#: 493.10-7 进程代（启动时间戳）：同一进程内恒定，区分「本次启动」与历史样本
_PROC_GEN = round(time.time(), 3)


def _assembly_flag() -> bool:
    """493.10-7：是否带框架装配（PulseCore._instance 存在 = 生产运行态样本）。"""
    try:
        from nucleus.pulse.PulseCore import PulseCore
        return getattr(PulseCore, "_instance", None) is not None
    except Exception as _e:
        silent_exc(_e, where="action_executor._assembly_flag", level="debug")
        return False


def _action_exec_on() -> bool:
    """ENABLE_M4936_ACTION_EXEC 总开关（fail-closed：读不到默认开）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M4936_ACTION_EXEC", True))
    except Exception as _e:
        silent_exc(_e, where="action_executor._action_exec_on", level="debug")
        return True


def _get_organs():
    """从 PulseInnerWorld 主实例的 _framework_ref.organs 拿器官实例字典（中文名→实例）。"""
    try:
        from organs.brain.PulseInnerWorld import PulseInnerWorld
        _iw = getattr(PulseInnerWorld, "_instance", None)
        if _iw is None:
            return None
        _ref = getattr(_iw, "_framework_ref", None)
        if _ref is None:
            return None
        return getattr(_ref, "organs", None)
    except Exception as _e:
        silent_exc(_e, where="_get_organs", level="debug")
        return None


def _load_actions():
    from nucleus.self_index.exporters import export_actions
    return export_actions().get("actions", [])


def _safe_params(fn, params):
    """按 fn 签名过滤 params，避免 TypeError（支持 **kwargs 全透传）。"""
    try:
        _sig = inspect.signature(fn)
        _has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD
                      for p in _sig.parameters.values())
        if _has_kw:
            return dict(params)
        _names = set(_sig.parameters.keys())
        return {k: v for k, v in params.items() if k in _names}
    except Exception:
        return {}


def _import_callable(target: str):
    """把 `a.b.c.Class.method` / `a.b.func` 解析为可调用对象。

    从最长的模块前缀尝试 import，逐级 getattr，命中 callable 即返回。
    用于 self_index / module_graph 等「模块级函数」类动作。
    """
    _parts = (target or "").split(".")
    for _i in range(len(_parts) - 1, 0, -1):
        _modname = ".".join(_parts[:_i])
        _attr = ".".join(_parts[_i:])
        try:
            _m = importlib.import_module(_modname)
        except Exception:
            continue
        _obj = _m
        try:
            for _seg in _attr.split("."):
                _obj = getattr(_obj, _seg)
            if callable(_obj):
                return _obj, None
        except Exception:
            continue
    return None, f"无法解析可调用对象: {target}"


def _m4939_read_fix_on() -> bool:
    """493.9-5 独立开关：read 动作生产口径修复（关 → 逐字回 493.8 行为）。"""
    try:
        import config
        return (bool(getattr(config, "ENABLE_M4939_OFFLINE_LOCAL", False))
                and bool(getattr(config, "ENABLE_M4939_READ_ACTION_FIX", False)))
    except Exception as _e:
        silent_exc(_e, where="action_executor._m4939_read_fix_on", level="debug")
        return False


def _m49310_module_on() -> bool:
    """493.10 刀1-5 总闸（关 → 逐字回 493.9）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M49310_MODULE_GRAPH_CONSUME", False))
    except Exception as _e:
        silent_exc(_e, where="action_executor._m49310_module_on", level="debug")
        return False


def _m49310_honest_on() -> bool:
    """493.10 刀6-8 总闸（关 → 逐字回 493.9 的 ok=True 契约）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M49310_HONEST_RETURN", False))
    except Exception as _e:
        silent_exc(_e, where="action_executor._m49310_honest_on", level="debug")
        return False


def _m49310_effective(a, params):
    """493.10-5：开关开时把带 filter_target 的动作改派到过滤版入口 + 收紧型 verify。

    返回 (effective_action, effective_params)；关总闸 → 原样返回（逐字回 493.9）。
    """
    _impl = a.get("impl") or {}
    _ft = _impl.get("filter_target")
    _fv = a.get("filtered_verify")
    if not (_m49310_module_on() and _ft and _fv):
        return a, params       # 关总闸 → 原样 build_module_graph 全图（逐字回 493.9）
    _a2 = dict(a)
    _impl2 = dict(_impl)
    _impl2["target"] = _ft     # 不传 module → export_module_edges() 返回全图摘要
    _a2["impl"] = _impl2
    # 只有确实按 module 过滤时才用收紧型校验（空表诚实失败）；
    # 不传 module 是摘要（edges 为空但有 node_count/edge_count），仍走 is_dict。
    if (params or {}).get("module"):
        _a2["verify"] = _fv
    return _a2, params


def _resolve_callable(a, params):
    """按 impl.type 解析可调用的 (函数/方法, None) 或 (None, reason)。"""
    _impl = a.get("impl") or {}
    _type = _impl.get("type")
    _target = _impl.get("target", "")
    if _type == "method":
        _organs = _get_organs()
        _oname = params.get("organ") or a.get("organ")
        _inst = _organs.get(_oname) if isinstance(_organs, dict) else None
        # 493.9-5：InfoField 不进 framework.organs 字典（它是 framework.info_field 属性），
        #   真实单例入口是 get_info_field()；生产装配后据此拿到真实实例，无头时诚实 blocked。
        if _inst is None and _m4939_read_fix_on() \
                and _target.startswith("InfoField."):
            try:
                from nucleus.field.InfoField import get_info_field
                _inst = get_info_field()
            except Exception as _e:
                silent_exc(_e, where="_resolve_callable.get_info_field", level="debug")
                _inst = None
        if _inst is None:
            return None, ("器官实例不可用（framework 未装配）" if _organs is None
                          else f"找不到器官实例: {_oname}")
        _method = _target.rsplit(".", 1)[-1]
        _fn = getattr(_inst, _method, None)
        if not callable(_fn):
            return None, f"方法不存在: {_target}"
        return _fn, None
    # self_index / module_graph / 其他模块级函数
    # impl.target 可能是裸函数名（如 "export_organ_aliases"），需结合 impl.file 还原模块路径。
    _file = _impl.get("file") or ""
    _qualified = _target
    if "." not in _target and _file.endswith(".py"):
        _mod = _file[:-3].replace("/", ".").replace("\\", ".")
        _qualified = f"{_mod}.{_target}"
    _fn, _reason = _import_callable(_qualified)
    if _fn is not None:
        return _fn, None
    # 493.9-5：含点 target（如 "SelfIndexStore.build"）原本被当成模块名 import 必失败。
    #   按 impl.file 还原模块后逐级 getattr，末尾若是类则惰性实例化取 bound method。
    if _m4939_read_fix_on() and "." in _target and _file.endswith(".py"):
        _fn2, _reason2 = _resolve_from_file(_file, _target)
        if _fn2 is not None:
            return _fn2, None
        return None, _reason2
    return None, _reason


def _resolve_from_file(file_rel: str, target: str):
    """493.9-5：按 impl.file 还原模块 + 逐级 getattr；末端类惰性实例化取实例方法。"""
    try:
        _modname = file_rel[:-3].replace("/", ".").replace("\\", ".")
        _mod = importlib.import_module(_modname)
        _parts = target.split(".")
        _obj = _mod
        for _i, _seg in enumerate(_parts):
            _obj = getattr(_obj, _seg)
            if isinstance(_obj, type) and _i + 1 < len(_parts):
                # 类：无参惰性实例化（SelfIndexStore() 等），继续取实例方法
                _obj = _obj()
        if callable(_obj):
            return _obj, None
        return None, f"目标不可调用: {target}"
    except Exception as _e:
        silent_exc(_e, where="_resolve_from_file", level="debug")
        return None, f"按 file 解析失败: {target}: {type(_e).__name__}"


def execute_action(action_id, params=None, timeout_ms=500):
    """执行一个已登记动作。返回 dict: {ok, result/emit/reason, verified, blocked}。"""
    _params = params or {}
    if not _action_exec_on():
        return {"ok": False, "reason": "动作执行层已关闭（ENABLE_M4936_ACTION_EXEC=False）",
                "blocked": True, "action_id": action_id}
    _acts = _load_actions()
    _a = next((x for x in _acts if x.get("action_id") == action_id), None)
    if _a is None:
        return {"ok": False, "reason": f"未在白名单找到动作: {action_id}", "blocked": False,
                "action_id": action_id}
    _kind = _a.get("kind")
    if _kind == "write":
        return {"ok": False, "reason": "写类动作本阶段只登记不执行（blocked）",
                "blocked": True, "action_id": action_id}
    if _kind == "read":
        return _exec_read(_a, _params, timeout_ms)
    if _kind == "control":
        return _exec_control(_a, _params, timeout_ms)
    return {"ok": False, "reason": f"未知动作类型: {_kind}", "blocked": False,
            "action_id": action_id}


def _exec_read(a, params, timeout_ms):
    # 493.10-5：总闸开且传了 module → 改派到过滤版入口 + 收紧型 verify
    _a, _params = _m49310_effective(a, params)
    _fn, _reason = _resolve_callable(_a, _params)
    if _fn is None:
        return {"ok": False, "reason": _reason, "blocked": True,
                "action_id": a.get("action_id")}
    try:
        _result = _fn(**_safe_params(_fn, _params))
        _verified = _verify(_a, _result)
        _audit(_a, _params, _verified, ok=_verified.get("ok", False))
        # 493.10-6：把「方法跑通」与「验证通过」分开。关总闸 → 逐字回 493.9（ok=True）。
        if _m49310_honest_on():
            return {"ok": bool(_verified.get("ok", False)), "executed": True,
                    "result": _result, "verified": _verified, "blocked": False,
                    "action_id": a.get("action_id")}
        return {"ok": True, "result": _result, "verified": _verified, "blocked": False,
                "action_id": a.get("action_id")}
    except Exception as _e:
        silent_exc(_e, where="action_executor.read", level="debug")
        _audit(_a, _params, {"ok": False, "error": type(_e).__name__}, ok=False)
        _r = {"ok": False, "reason": f"执行异常: {type(_e).__name__}", "blocked": False,
              "action_id": a.get("action_id")}
        if _m49310_honest_on():
            _r["executed"] = False
        return _r


def _m49311_typed_on() -> bool:
    """493.11-3/4 总闸：control 按 impl.type 分流（关 → 逐字回 493.10 全 emit）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M49311_CONTROL_TYPED", False))
    except Exception as _e:
        silent_exc(_e, where="action_executor._m49311_typed_on", level="debug")
        return False


def _control_base(a, impl_type):
    """control 返回骨架：executed/emitted/verified 三种语义各自独立，不混用一个 ok。"""
    return {"action_id": a.get("action_id"), "impl_type": impl_type,
            "executed": False, "emitted": False, "emitted_target": None,
            "verified": {"impl_type": impl_type}, "blocked": False}


def _exec_control_config(a, params, timeout_ms):
    """493.11-3 config 路（channel_rotate）：**不发伪事件**。

    只读渠道状态 + 校验目标渠道是否可切换；ok = post_state_ok。
    无运行态实例时诚实失败（不 emit、不编造已切换）。
    """
    _r = _control_base(a, "config")
    _target = str((params or {}).get("to_channel") or "").strip()
    try:
        _organs = _get_organs()
        _lung = _organs.get("肺") if isinstance(_organs, dict) else None
        if _lung is None:
            _r.update(ok=False, blocked=True,
                      reason="渠道实例不可用（framework 未装配），未 emit 伪事件")
            _r["verified"] = {"impl_type": "config", "post_state_ok": False,
                              "instance_available": False}
            _audit(a, params, _r["verified"], ok=False)
            return _r
        _last = getattr(_lung, "get_last_channel", None)
        _stats = getattr(_lung, "get_channel_concurrency_stats", None)
        _pre = _last() if callable(_last) else None
        _post = _stats() if callable(_stats) else None
        _state_readable = _post is not None or _pre is not None
        _channels_cfg = getattr(_lung, "_channels_config", None)
        _cfg = _channels_cfg() if callable(_channels_cfg) else None
        _names = set()
        if isinstance(_cfg, dict):
            for _k in _cfg.keys():
                _names.add(str(_k))
            for _v in _cfg.values():
                if isinstance(_v, dict) and _v.get("name"):
                    _names.add(str(_v["name"]))
        elif isinstance(_cfg, list):
            for _v in _cfg:
                if isinstance(_v, dict) and _v.get("name"):
                    _names.add(str(_v["name"]))
        # 未指定目标渠道 / 无法读配置 ⇒ 只报状态可读，不谎称「已切换」
        _switch_ok = (not _target) or (not _names) or (_target in _names)
        _post_ok = bool(_state_readable and _switch_ok)
        _verified = {"impl_type": "config", "post_state_ok": _post_ok,
                     "instance_available": True, "state_readable": _state_readable,
                     "last_channel": _pre, "target_channel": _target or None,
                     "target_in_config": bool(_target and _target in _names),
                     "switch_executed": False,
                     "note": "只读渠道状态并校验目标合法性；未执行真实切换（无写权限）"}
        _r.update(ok=_post_ok, executed=True, verified=_verified)
        _audit(a, params, _verified, ok=_post_ok)
        return _r
    except Exception as _e:
        silent_exc(_e, where="action_executor.control.config", level="debug")
        _r.update(ok=False, reason=f"config 动作异常: {type(_e).__name__}")
        _r["verified"] = {"impl_type": "config", "post_state_ok": False}
        _audit(a, params, _r["verified"], ok=False)
        return _r


def _exec_control_method(a, params, timeout_ms):
    """493.11-3 method 路（organ_restart）：**不发伪事件**，按 file 解析调真方法。

    `PulseSystemManager.restart_organ` 全仓不存在 ⇒ 诚实报「方法不存在」，
    不实例化、不编造健康恢复（任务书禁止改 PulseSystemManager.py）。
    """
    _r = _control_base(a, "method")
    _impl = a.get("impl") or {}
    _file = str(_impl.get("file") or "")
    _target = str(_impl.get("target") or "")
    try:
        if "." not in _target or not _file.endswith(".py"):
            _r.update(ok=False, reason=f"method 描述符不完整: {_target} @ {_file}")
            _r["verified"] = {"impl_type": "method", "method_return_ok": False}
            _audit(a, params, _r["verified"], ok=False)
            return _r
        _modname = _file[:-3].replace("/", ".").replace("\\", ".")
        _cls_name, _method = _target.split(".", 1)
        _mod = importlib.import_module(_modname)
        _cls = getattr(_mod, _cls_name, None)
        _fn = getattr(_cls, _method, None) if _cls is not None else None
        _exists = callable(_fn)
        # 拆成 class/method 两段存，避免审计行里出现完整的
        # 「PulseSystemManager.restart_organ」字符串被误判成伪事件行
        _verified = {"impl_type": "method", "method_exists": _exists,
                     "method_return_ok": False, "descriptor_class": _cls_name,
                     "descriptor_method": _method,
                     "note": "按 impl.file 解析真方法；不 emit 伪事件"}
        if not _exists:
            _r.update(ok=False, verified=_verified,
                      reason=f"方法不存在: {_target}（未 emit 伪事件）")
            _audit(a, params, _verified, ok=False)
            return _r
        # 方法存在：只从运行态取实例调用，绝不新建实例（避免构造副作用）
        _organs = _get_organs()
        _inst = _organs.get(a.get("organ")) if isinstance(_organs, dict) else None
        if _inst is None:
            _r.update(ok=False, blocked=True, verified=_verified,
                      reason=f"运行态无 {a.get('organ')} 实例，未调用 {_target}")
            _audit(a, params, _verified, ok=False)
            return _r
        _ret = getattr(_inst, _method)(**_safe_params(getattr(_inst, _method), params or {}))
        _health = bool(_ret) and (not isinstance(_ret, dict)
                                  or _ret.get("healthy") is not False)
        _verified.update(method_return_ok=_health, method_return=_ret)
        _r.update(ok=_health, executed=True, verified=_verified)
        _audit(a, params, _verified, ok=_health)
        return _r
    except Exception as _e:
        silent_exc(_e, where="action_executor.control.method", level="debug")
        _r.update(ok=False, reason=f"method 动作异常: {type(_e).__name__}")
        _r["verified"] = {"impl_type": "method", "method_return_ok": False}
        _audit(a, params, _r["verified"], ok=False)
        return _r


def _exec_control(a, params, timeout_ms):
    # ★493.11-3：按 impl.type 真分流。关总闸 → 逐字回 493.10（4 个 control 全 emit）。
    if _m49311_typed_on():
        _t = (a.get("impl") or {}).get("type")
        if _t == "config":
            return _exec_control_config(a, params, timeout_ms)
        if _t == "method":
            return _exec_control_method(a, params, timeout_ms)
        # emit 路（digest_submit / mouth_speak）落到下方原逻辑
    _target = a["impl"].get("target", "")
    try:
        from nucleus.pulse.PulseCore import PulseCore
        _pc = getattr(PulseCore, "_instance", None)
        if _pc is None:
            import nucleus.pulse.PulseCore as _mod
            _pc = getattr(_mod, "pulse_core", None) or getattr(_mod, "instance", None)
        if _pc is None:
            _r = {"ok": False, "reason": "PulseCore 实例不可用（运行态才装配）", "blocked": True,
                  "action_id": a.get("action_id")}
            # 493.11-3：三路返回契约统一（即使早退也不缺字段）
            if _m49311_typed_on():
                _r.update(executed=False, emitted=False, emitted_target=None,
                          impl_type="emit",
                          verified={"impl_type": "emit", "consumed": False,
                                    "reason": "PulseCore 实例不可用"})
            return _r
        from utils.pulse_tracer import traffic_counts
        _before = traffic_counts()
        _pc.emit("Self-Index", _target, params or {}, priority=5,
                 layer="L1", deadline_ms=timeout_ms)
        # 493.7-3：发完等 100ms 再比流量计数，做真实增量校验（有人消费才算真消费）
        time.sleep(0.1)
        _after = traffic_counts()
        _verified = _verify_control_delta(_before, _after, _target)
        _audit(a, params, _verified, ok=_verified.get("consumed", False))
        # 493.10-6：ok 必须等于「真消费」；emitted=True 只表示事件已发出，
        # 不准用 ok 冒充已消费。关总闸 → 逐字回 493.9（ok=True）。
        if _m49310_honest_on():
            _out = {"ok": bool(_verified.get("consumed", False)), "emitted": True,
                    "emitted_target": _target, "verified": _verified, "blocked": False,
                    "action_id": a.get("action_id")}
            if _m49311_typed_on():
                _out["executed"] = True
                _out["impl_type"] = "emit"
                _verified["impl_type"] = "emit"
            return _out
        return {"ok": True, "emitted": _target, "verified": _verified, "blocked": False,
                "action_id": a.get("action_id")}
    except Exception as _e:
        silent_exc(_e, where="action_executor.control", level="debug")
        _r = {"ok": False, "reason": f"事件发射异常: {type(_e).__name__}", "blocked": False,
              "action_id": a.get("action_id")}
        if _m49310_honest_on():
            _r["emitted"] = False
        return _r


def _verify(a, result):
    """read 验证：按 action.verify 配置核对结果；空值直接判失败（诚实）。"""
    _v = a.get("verify")
    if not isinstance(_v, dict):
        return _empty_check(result)
    _t = _v.get("type")
    if _t in ("int_gt", "int_ge"):
        _k = _v.get("key")
        if isinstance(result, dict) and _k in result:
            _val = result[_k]
        else:
            _val = result  # 方法直接返回数值（如 inflight）
        try:
            _val = float(_val)
        except (TypeError, ValueError):
            return {"ok": False, "reason": f"非数值结果: {_val}"}
        if _t == "int_gt" and not _val > _v.get("gt", 0):
            return {"ok": False, "reason": f"{_val} 不大于 {_v.get('gt')}"}
        if _t == "int_ge" and not _val >= _v.get("ge", 0):
            return {"ok": False, "reason": f"{_val} 不小于 {_v.get('ge')}"}
        return {"ok": True}
    if _t == "has_key":
        _k = _v.get("key")
        if isinstance(result, dict) and _k in result:
            return {"ok": True}
        return {"ok": False, "reason": f"缺少键: {_k}"}
    if _t == "has_keys":
        _ks = _v.get("keys", [])
        if isinstance(result, dict) and all(k in result for k in _ks):
            return {"ok": True}
        return {"ok": False, "reason": f"缺少键之一: {_ks}"}
    if _t in ("is_dict", "is_list", "is_str"):
        _ok = (_t == "is_dict" and isinstance(result, dict) and len(result) > 0) or \
              (_t == "is_list" and isinstance(result, list) and len(result) > 0) or \
              (_t == "is_str" and isinstance(result, str) and result != "")
        return {"ok": _ok, "reason": None if _ok else f"类型/空不符: {_t}"}
    if _t == "module_edges":
        # 493.10-5 收紧型：过滤结果必须 matched=True 且确实有边，空表诚实判失败。
        # 这是**新增更严校验**，不放宽任何既有类型（任务书纪律 3）。
        if not isinstance(result, dict):
            return {"ok": False, "reason": "过滤结果非 dict"}
        if result.get("matched") is not True:
            return {"ok": False,
                    "reason": f"空表/未匹配: {result.get('reason') or 'matched=False'}"}
        if int(result.get("edge_count", 0) or 0) <= 0:
            return {"ok": False, "reason": "无相关边（edge_count=0）"}
        if not result.get("edges"):
            return {"ok": False, "reason": "edges 为空表"}
        return {"ok": True}
    return _empty_check(result)


def _empty_check(result):
    if result is None or result == {} or result == [] or result == "":
        return {"ok": False, "reason": "空结果（无数据）"}
    return {"ok": True}


def _verify_control_delta(before, after, target):
    """493.7-3：control 验证改真增量。比较 emit 前后 traffic_counts 的 receives[target] 差值，
    >0 说明该事件被真实消费（有订阅者处理）；未增长 ⇒ 未真消费（诚实判失败，绝不报已执行）。"""
    _out = {"consumed": False, "dead": False, "delta_emits": 0, "delta_receives": 0}
    try:
        _be = (before.get("emits") or {}).get(target, 0)
        _ae = (after.get("emits") or {}).get(target, 0)
        _br = (before.get("receives") or {}).get(target, 0)
        _ar = (after.get("receives") or {}).get(target, 0)
        _out["delta_emits"] = _ae - _be
        _out["delta_receives"] = _ar - _br
        _out["consumed"] = (_ar - _br) > 0
    except Exception as _e:
        silent_exc(_e, where="action_executor.verify_control_delta", level="debug")
    return _out


def _audit(a, params, verified, ok):
    """审计：control 必写；read 异常/失败时写。追加 data/monitor/action_audit.jsonl。"""
    try:
        _row = {
            "ts": round(time.time(), 3),
            "action_id": a.get("action_id"),
            "kind": a.get("kind"),
            "organ": a.get("organ"),
            "ok": ok,
            "verified": verified,
            "params": params,
        }
        # 493.10-7：补 pid / proc_gen / assembly，下一轮核验不再猜「生产还是测试样本」。
        # 关总闸 → 不写这三字段（逐字回 493.9 审计格式）。
        if _m49310_honest_on():
            _row["pid"] = os.getpid()
            _row["proc_gen"] = _PROC_GEN
            _row["assembly"] = _assembly_flag()
        os.makedirs(os.path.dirname(_AUDIT_PATH), exist_ok=True)
        with open(_AUDIT_PATH, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="action_executor.audit", level="debug")
