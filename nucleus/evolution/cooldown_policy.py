# -*- coding: utf-8 -*-
"""cooldown_policy.py —— 僵尸冷却判据分级（第463批）

## 修的是什么（实盘取证，不是推测）

10:54 第 1 轮：``[P1-3 冷却隔离] 候选15个 → 放行1个（冷却跳过14个／到期解冻0个）``
→ ``[修复蒸馏] 共1个问题`` → ``[自主进化] 修复完成: 0/15个，处理1个``。

根因在 ``SafeEvolutionExecutor._cooldown_classify()``：**只看 type 是否在 15 类
本地规则白名单里**，不在就一律判「不可修复·本地无规则转LLM」并冷却 21600s。
而 `periodic_task_no_reentry` / `non_atomic_write` 这类问题，446 刀1 的检测器
早已覆盖、462 的证据链能给出 8/8 —— 它们恰恰是**能产出补丁**的，却被关在门外。
更糟的是登记发生在循环结束后对**所有 processed 问题**（不管本轮是否真的尝试过
修复），每轮重登记都把 deadline 推到 ``now+TTL`` ⇒ 永不过期（实盘「解冻 0 个」）。

## 本模块的契约

* 判据从「type 在不在白名单」改成「**这条问题有没有可复现的修复路径**」：
  本地规则可修、446 检测器可复现的代码类 ⇒ `skip_cool`（继续参与生成）；
  高危安全拦截 / 无代码定位的日志类 ⇒ `cool`；其余代码类 ⇒ `cool_short`。
* 纯函数、无副作用，便于单测与 A/B；调用方仍负责不刷新已有冷却与强制复检。
* 未知类型不猜：默认 `cool_short`（短冷却），绝不 `skip_cool`。
"""
from __future__ import annotations

from typing import Any

from nucleus._silent_except import silent_exc

SCHEMA = "cooldown_policy/463.v1"

A_SKIP = "skip_cool"
A_COOL = "cool"
A_COOL_SHORT = "cool_short"

#: ★446 刀1 检测器注册表覆盖的六类（对**代码片段**可给出 true_pass / baseline>0）
REPRO_COVERED: frozenset[str] = frozenset({
    "silent_exception", "print_instead_of_log", "code_optimization",
    "periodic_task_no_reentry", "non_atomic_write",
    "cross_module_singleton_call",
})


def _t(issue: dict) -> str:
    return str(issue.get("type") or issue.get("issue_type") or "").strip()


def _is_code_issue(issue: dict) -> bool:
    return bool(str(issue.get("file") or "").strip()
                and str(issue.get("method") or "").strip())


def classify(issue: dict, *, local_fixable_types: Any = None,
             high_risk_types: Any = None) -> dict:
    """返回 ``{"action", "why", "is_code_issue", "type", "schema"}``。

    Args:
        local_fixable_types: 调用方的本地高置信度规则白名单（集合）。
        high_risk_types: 安全边界故意不修的类型集合（集合）。
    """
    _local = set(local_fixable_types or ())
    _high = set(high_risk_types or ())
    _type = _t(issue)
    _code = _is_code_issue(issue)
    _out = {"action": A_COOL_SHORT, "why": "本地无规则·转LLM",
            "is_code_issue": _code, "type": _type, "schema": SCHEMA}

    if _type in _high:
        _out["action"] = A_COOL
        _out["why"] = "高危·安全拦截"
        return _out
    if _type in _local:
        _out["action"] = A_SKIP
        _out["why"] = "本地规则可修"
        return _out
    if _code and _type in REPRO_COVERED:
        _out["action"] = A_SKIP
        _out["why"] = "446检测器可复现·应继续生成"
        return _out
    if not _code:
        _out["action"] = A_COOL
        _out["why"] = "日志类·无代码定位"
        return _out
    return _out


def should_force_recheck(hits: int, *, need: int = 3) -> bool:
    """★463 刀2：累计被冷却跳过达 ``need`` 次 ⇒ 强制放行一次复检。

    纯函数，供冷却扫描与单测共用**同一口径**（避免内联判据漂移）。
    """
    try:
        return int(hits) >= max(1, int(need))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="cooldown_policy::should_force_recheck", level="debug")
        return False


MIGRATE_SCHEMA = "cooldown_migration/464.v1"


def issue_from_fingerprint(fp: str) -> dict:
    """从冷却指纹反解出 issue（``_cooldown_key`` 的逆运算）。

    指纹只有两种形态（见 ``SafeEvolutionExecutor._issue_identity``）：
      * 代码类 ``file|method|type``
      * 日志类 ``organ:X|type:Y|描述摘要``
    反解只服务于「按当前判据重判历史条目」，不追求与原件逐字一致。
    """
    _s = str(fp or "")
    if _s.startswith("organ:"):
        _parts = _s.split("|")
        _organ = _parts[0][len("organ:"):]
        _type = ""
        _desc = ""
        for _p in _parts[1:]:
            if _p.startswith("type:") and not _type:
                _type = _p[len("type:"):]
            elif not _desc:
                _desc = _p
        return {"organ": _organ, "type": _type, "description": _desc}
    _parts = _s.split("|")
    return {"file": _parts[0] if len(_parts) > 0 else "",
            "method": _parts[1] if len(_parts) > 1 else "",
            "type": _parts[2] if len(_parts) > 2 else ""}


def migrate_ledger(entries: dict, *, local_fixable_types: Any = None,
                   high_risk_types: Any = None,
                   short_sec: float = 300.0) -> tuple[dict, dict]:
    """★464 刀1：载入期按**当前判据**重判历史台账。

    463 的 ``classify()`` 只影响「新登记」的 TTL；已经落盘的僵尸条目
    （旧判据 21600s）原样装回，实盘 25 条里 11 条按 463 判据本就该放行、
    最长还锁 4.94h。本函数把这一步补上：

      * ``skip_cool``   ⇒ **移出**台账（它本就不该被冷却）
      * ``cool_short``  ⇒ 剩余时长**收短**到 ``short_sec``（不延长，只收短）
      * ``cool``        ⇒ 原样保留（日志类无代码定位，冷却是对的）

    纯函数：不改入参、不落盘。返回 ``(新台账, 迁移报告)``；
    台账值保持 ``[剩余秒, ...尾巴]`` 结构，尾巴（轮次/命中数）原样透传。
    """
    _out: dict = {}
    _rep = {"schema": MIGRATE_SCHEMA, "total": 0,
            "released": 0, "shortened": 0, "kept": 0, "invalid": 0}
    for _fp, _v in (entries or {}).items():
        _rep["total"] += 1
        if not isinstance(_v, (list, tuple)) or len(_v) < 1:
            _rep["invalid"] += 1
            continue
        try:
            _rem = float(_v[0])
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="cooldown_policy::migrate_ledger rem", level="debug")
            _rep["invalid"] += 1
            continue
        _c = classify(issue_from_fingerprint(_fp),
                      local_fixable_types=local_fixable_types,
                      high_risk_types=high_risk_types)
        _act = _c.get("action")
        if _act == A_SKIP:
            _rep["released"] += 1
            continue
        if _act == A_COOL_SHORT and _rem > float(short_sec):
            _rem = float(short_sec)
            _rep["shortened"] += 1
        else:
            _rep["kept"] += 1
        _out[_fp] = [_rem, *_v[1:]]
    return _out, _rep



def priority(issue: dict, *, local_fixable_types: Any = None) -> int:
    """排序键（越小越优先）：可修的代码类排在日志类之前。"""
    if not isinstance(issue, dict):
        return 99
    _local = set(local_fixable_types or ())
    _type = _t(issue)
    if _type in _local:
        return 0
    if _is_code_issue(issue) and _type in REPRO_COVERED:
        return 1
    if _is_code_issue(issue):
        return 2
    return 9
