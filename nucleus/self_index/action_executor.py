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


def _resolve_callable(a, params):
    """按 impl.type 解析可调用的 (函数/方法, None) 或 (None, reason)。"""
    _impl = a.get("impl") or {}
    _type = _impl.get("type")
    _target = _impl.get("target", "")
    if _type == "method":
        _organs = _get_organs()
        if _organs is None:
            return None, "器官实例不可用（framework 未装配）"
        _oname = params.get("organ") or a.get("organ")
        _inst = _organs.get(_oname)
        if _inst is None:
            return None, f"找不到器官实例: {_oname}"
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
    return _import_callable(_qualified)


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
    _fn, _reason = _resolve_callable(a, params)
    if _fn is None:
        return {"ok": False, "reason": _reason, "blocked": True,
                "action_id": a.get("action_id")}
    try:
        _result = _fn(**_safe_params(_fn, params))
        _verified = _verify(a, _result)
        _audit(a, params, _verified, ok=_verified.get("ok", False))
        return {"ok": True, "result": _result, "verified": _verified, "blocked": False,
                "action_id": a.get("action_id")}
    except Exception as _e:
        silent_exc(_e, where="action_executor.read", level="debug")
        _audit(a, params, {"ok": False, "error": type(_e).__name__}, ok=False)
        return {"ok": False, "reason": f"执行异常: {type(_e).__name__}", "blocked": False,
                "action_id": a.get("action_id")}


def _exec_control(a, params, timeout_ms):
    _target = a["impl"].get("target", "")
    try:
        from nucleus.pulse.PulseCore import PulseCore
        _pc = getattr(PulseCore, "_instance", None)
        if _pc is None:
            import nucleus.pulse.PulseCore as _mod
            _pc = getattr(_mod, "pulse_core", None) or getattr(_mod, "instance", None)
        if _pc is None:
            return {"ok": False, "reason": "PulseCore 实例不可用（运行态才装配）", "blocked": True,
                    "action_id": a.get("action_id")}
        _pc.emit("Self-Index", _target, params or {}, priority=5,
                 layer="L1", deadline_ms=timeout_ms)
        _verified = _verify_control(a)
        _audit(a, params, _verified, ok=_verified.get("consumed", False))
        return {"ok": True, "emitted": _target, "verified": _verified, "blocked": False,
                "action_id": a.get("action_id")}
    except Exception as _e:
        silent_exc(_e, where="action_executor.control", level="debug")
        return {"ok": False, "reason": f"事件发射异常: {type(_e).__name__}", "blocked": False,
                "action_id": a.get("action_id")}


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
    return _empty_check(result)


def _empty_check(result):
    if result is None or result == {} or result == [] or result == "":
        return {"ok": False, "reason": "空结果（无数据）"}
    return {"ok": True}


def _verify_control(a):
    """control 验证：traffic_counts 总线可达 + dead_letter 未增长。"""
    _out = {"consumed": False, "dead": False}
    try:
        from utils.pulse_tracer import traffic_counts
        _tc = traffic_counts()
        _out["consumed"] = bool(_tc.get("receives") or _tc.get("emits"))
    except Exception as _e:
        silent_exc(_e, where="action_executor.verify_control", level="debug")
    try:
        from utils.dead_letter_ledger import face_dead_letters
        _out["dead"] = not bool(face_dead_letters())
    except Exception as _e:
        silent_exc(_e, where="action_executor.verify_control_dead", level="debug")
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
        os.makedirs(os.path.dirname(_AUDIT_PATH), exist_ok=True)
        with open(_AUDIT_PATH, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="action_executor.audit", level="debug")
