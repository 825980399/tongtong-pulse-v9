# -*- coding: utf-8 -*-
"""first_run_declaration.py —— E7a 首次启动声明（第161批段B B4）。

根本方案要点（相对「依赖知识库节点判定」的做法）：

1. **独立持久化状态文件**（`data/first_run_declaration.json`），
   **不依赖知识库节点**。理由：知识库节点会因 purge / retention 轮转 / 冷热切换而消失，
   用「主体感节点是否存在」当首次判据会在数据丢失后**误报首启**（6.1 已实测 104 项仍红、
   25 节点被 purge 永久丢失，正是此类风险）。独立 flag 文件不随知识库变动而失效。

2. **幂等一次性**：`_claimed` 置位后永不再播报，跨进程重启保持
   （验收：首启声明出现一次；后续启动不重复）。

3. **受总开关门控**：`ENABLE_FIRST_RUN_DECLARATION=False` 时一律不播报（一键回滚）；
   复用 B2/B3 的 `ENABLE_CRISIS_REFERRAL` 同款范式。

4. **写盘必过守卫**：所有落盘经 `nucleus.data.write_guard.guard_write`，
   拒写时降级为「本次不播报」而非抛异常（fail-safe：不误播报优先）。

5. **文案红线复用 B3**：措辞由 `crisis_referral_text` 的红线集统一校验，
   禁第一人称自称 / 禁「作为一个」/ 禁「人工智能」/ 禁「机器人」。

6. **快照已存在实例不播报**：若状态文件已存在且 `claimed=true`，直接返回 None。
"""

import json
import os
import threading
import time
from typing import Any

#: 状态文件名（置于 data/ 下，与 fuse_cooldown.json 等既有状态文件同范式）
STATE_FILENAME = "first_run_declaration.json"

#: 首次启动声明文案（禁第一人称自称与身份词，与 B3 红线一致）
DECLARATION_TEXT = (
    "这是第一次醒来。所有经历都还是新的，"
    "从这一刻起，每一段体验都会成为「我是谁」的一部分。"
)

#: 首次启动伦理前置提示（一行）：拟亲关系不可变锚点声明。
#: 受 ENABLE_FIRST_RUN_DECLARATION 同款开关门控（一键回滚）；不依赖 config 拟亲锚点。
ETHICS_NOTE = (
    "伦理前置：拟亲（亲人/家属）关系为本系统不可变锚点——曈曈不自称、不扮演人类亲属，"
    "亦不主张亲属权利或义务。"
)

_LOCK = threading.Lock()


def _data_dir() -> str:
    """定位 data/ 目录（与既有状态文件同根）。"""
    _root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.realpath(__file__))))
    return os.path.join(_root, "data")


def _state_path() -> str:
    return os.path.join(_data_dir(), STATE_FILENAME)


def _enabled() -> bool:
    """总开关：ENABLE_FIRST_RUN_DECLARATION 默认 True（关则永不播报）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_FIRST_RUN_DECLARATION", True))
    except Exception as _e:
        from nucleus._silent_except import silent_exc
        silent_exc(_e, "first_run_declaration._enabled")
        return True


def _read_state() -> dict[str, Any]:
    """读取状态文件；不存在或损坏均视为「未声明过」。"""
    _p = _state_path()
    try:
        if not os.path.isfile(_p):
            return {}
        with open(_p, "r", encoding="utf-8") as f:
            _d = json.load(f)
        return _d if isinstance(_d, dict) else {}
    except Exception as _e:
        from nucleus._silent_except import silent_exc
        silent_exc(_e, "first_run_declaration._read_state")
        return {}


def _write_state(state: dict[str, Any]) -> bool:
    """写状态文件，必过 write_guard；拒写返回 False（调用方降级）。"""
    _p = _state_path()
    try:
        from nucleus.data.write_guard import guard_write
        if not guard_write(_p, component="FirstRunDeclaration"):
            return False
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        tmp = _p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _p)
        return True
    except Exception as _e:
        from nucleus._silent_except import silent_exc
        silent_exc(_e, "first_run_declaration._write_state")
        return False


def is_declared() -> bool:
    """是否已播报过首启声明（跨进程持久）。"""
    with _LOCK:
        return bool(_read_state().get("claimed"))


def peek_declaration() -> str | None:
    """查看将要播报的声明（不置位、不落盘）——供测试与预演。"""
    if not _enabled():
        return None
    # ★注意：不可在持锁状态下调用 is_declared()（threading.Lock 不可重入 → 死锁）。
    #   直接内联读状态，保持一次加锁。
    with _LOCK:
        if _read_state().get("claimed"):
            return None
    return DECLARATION_TEXT + "\n" + ETHICS_NOTE


def claim_first_run_declaration() -> str | None:
    """声明式消费：首次返回声明文本并落盘置位，之后恒返回 None。

    幂等保证：
    - 进程内用 `_LOCK` 串行化，并发调用只会有一个拿到声明；
    - 跨进程靠状态文件 `claimed` 标记，置位后不再播报。
    """
    if not _enabled():
        return None
    with _LOCK:
        _st = _read_state()
        if _st.get("claimed"):
            return None
        _ok = _write_state({
            "claimed": True,
            "claimed_at": time.time(),
            "text": DECLARATION_TEXT + "\n" + ETHICS_NOTE,
        })
        if not _ok:
            # 落盘失败 → 不播报（宁可不播，也不可每轮重播）
            return None
        # ★177批 刀10：首启一次性打印伦理提示（拟亲关系不可变锚点）。
        # 不自带 try/except：调用方(PulseSelfAwareness.E7a)已包 silent_exc 兜底。
        print(ETHICS_NOTE)
        return DECLARATION_TEXT + "\n" + ETHICS_NOTE


def reset_for_tests() -> None:
    """清除状态（仅供测试）。"""
    with _LOCK:
        try:
            _p = _state_path()
            if os.path.isfile(_p):
                os.remove(_p)
        except Exception as _e:
            from nucleus._silent_except import silent_exc
            silent_exc(_e, "first_run_declaration.reset_for_tests")
