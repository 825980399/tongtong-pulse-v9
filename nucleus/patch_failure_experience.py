# -*- coding: utf-8 -*-
"""★452刀6（P0-4）：补丁失败经验库 —— 把常见错误记下来，下次生成时提醒。

为什么要有它
------------
补丁验证失败后现在只剩一条冷却登记（`_m114a_register_verify_failure`），
"为什么失败"这件事随着日志轮转就没了。下一轮生成同一个补丁时，LLM 会**原样再犯**。
实测盘面（450刀1）：今天 17 条生成里 **17 条 `no_def=True`**（结构缺陷）——
这是最典型的"可预防重复错误"。

本模块做什么
------------
1. `record_failure()`：每次验证失败追加一行到 `data/patches/failure_experience.jsonl`
   （timestamp / error_type / error_msg / file_path / patch_snippet）；
2. `append_reminder(prompt)`：取最近 10 条里**最常见的错误类型**，拼成 ≤300 字提醒段
   追加到补丁生成 prompt 末尾。

三条纪律
--------
- **只加提醒段，不改既有 prompt 一字**（幂等：同一段不重复追加）；
- **绝不抛异常**：失败登记/拼接出问题只影响"有没有提醒"，绝不影响补丁生成；
- **测试进程不写默认生产路径**（446/447/450 同型护栏）。
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

DEFAULT_REL: str = os.path.join("data", "patches", "failure_experience.jsonl")
_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 提醒段固定头（用于幂等判断，也便于日后从 prompt 里精确剥离）
#: ★459 刀2：把「审批/收敛阶段被作废」也写进提示（原来只说"最近容易犯的错误"，
#:   生成侧看不出这些特征里有一半来自审批打回）。
REMINDER_HEAD: str = ("以下是最近容易犯的错误（含审批被打回的），"
                     "请检查你的代码是否包含以下特征：")

#: ★459 刀2：voided 来源的 error_type 统一前缀，便于在提醒段里限额（≤一半）
VOIDED_PREFIX: str = "voided_"

#: voided 台账字段 → 一句话失败特征（★绝不塞完整 patch 代码，会炸 token）
_VOIDED_FEATURES: tuple = (
    ("no_def", "voided_no_def",
     "审批作废：补丁缺函数定义，生成前须先定位 def 与缩进层级"),
    ("mismatch", "voided_original_mismatch",
     "审批作废：original_code 与仓库不一致，须按当前源码重取上下文"),
    ("o3_dead_code", "voided_dead_code",
     "审批作废：命中死代码/越权改动，不得顺手改无关方法"),
)

#: error_msg → error_type 的归类规则（顺序即优先级）
_CLASSIFY_RULES: tuple = (
    ("no_def", "no_def_missing"),
    ("结构缺陷", "structural_defect"),
    ("unindent", "indent_error"),
    ("indent", "indent_error"),
    ("语法错误", "syntax_error"),
    ("SyntaxError", "syntax_error"),
    ("original_code not in", "original_mismatch"),
    ("超时", "timeout"),
    ("Timeout", "timeout"),
    ("补丁生成失败", "generation_failed"),
    ("验证未通过", "verify_failed"),
)


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="patch_failure_experience::_cfg", level="debug")
        return default


def _on() -> bool:
    return bool(_cfg("ENABLE_PATCH_FAILURE_EXPERIENCE", True))


def _path() -> str:
    p = str(_cfg("M452_FAILURE_EXPERIENCE_PATH", DEFAULT_REL) or "").strip()
    if not p:
        return ""
    return p if os.path.isabs(p) else os.path.join(_ROOT, p)


def classify_error(msg: str) -> str:
    """把自由文本的失败原因归类成稳定的 error_type（纯函数，可单测）。"""
    s = str(msg or "")
    low = s.lower()
    for needle, kind in _CLASSIFY_RULES:
        if needle.lower() in low:
            return kind
    return "other"


def record_failure(error_msg: str, *, error_type: str = "",
                   file_path: str = "", patch_snippet: str = "") -> bool:
    """追加一行失败记录。返回是否写入成功（失败只影响提醒，绝不影响主流程）。"""
    if not _on():
        return False
    p = _path()
    if not p:
        return False
    default = os.path.join(_ROOT, DEFAULT_REL)
    if "pytest" in sys.modules and os.path.normcase(os.path.normpath(p)) == \
            os.path.normcase(os.path.normpath(default)):
        return False
    row = {
        "timestamp": time.time(),
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error_type": str(error_type or classify_error(error_msg)),
        "error_msg": str(error_msg or "")[:400],
        "file_path": str(file_path or "")[:200],
        "patch_snippet": str(patch_snippet or "")[:300],
    }
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="patch_failure_experience::record_failure", level="debug")
        return False


def voided_feature(row: dict) -> tuple:
    """★459 刀2：把一条 `converge_voided` 台账记录压成**一句话失败特征**。

    返回 ``(error_type, sentence)``。优先级：结构标志 > issue_type > 兜底。
    ★只读结构化字段，绝不把 ``reason`` / 补丁正文整段塞进来（任务书：不塞完整代码）。
    """
    r = row if isinstance(row, dict) else {}
    for _flag, _kind, _sentence in _VOIDED_FEATURES:
        if bool(r.get(_flag)):
            return _kind, _sentence
    _it = str(r.get("issue_type") or "").strip()
    if _it:
        _short = _it[:32].replace(" ", "_")
        return (VOIDED_PREFIX + _short,
                f"审批作废：issue_type={_short}，生成前须核对该类问题的准入条件")
    return ("voided_structural_defect",
            "审批作废：结构缺陷，生成前须核对方法边界与缩进")


def record_voided(row: dict) -> bool:
    """★459 刀2：一条 voided 记录 ⇒ 往经验库追加**一行特征**（不是复制台账）。

    由 `criteria_window.note_converge_voided` 在台账写成功后调用 —— 那是
    收敛/审批作废的**唯一写口**（229 条历史记录都从那里来）。
    ``file_path`` 只带 ``file|method``，``patch_snippet`` 恒空。
    """
    _kind, _sentence = voided_feature(row)
    _f = str((row or {}).get("file") or "")[:120]
    _m = str((row or {}).get("method") or "")[:60]
    _fp = f"{_f}|{_m}"
    return record_failure(_sentence, error_type=_kind, file_path=_fp,
                          patch_snippet="")


def load_recent(limit: int = 100) -> list[dict]:
    """读最近 `limit` 行（坏行跳过）。读不到返回 `[]`。"""
    p = _path()
    if not p or not os.path.exists(p):
        return []
    out: list[dict] = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError as _je:
                    silent_exc(_je, where="patch_failure_experience::load_recent 坏行",
                               level="debug")
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except Exception as _e:
        silent_exc(_e, where="patch_failure_experience::load_recent", level="debug")
        return []
    return out[-max(1, int(limit)):]


def build_reminder(*, top_n: int = 10, max_chars: int = 300) -> str:
    """取最近记录里最常见的错误类型，拼提醒段。无记录 ⇒ 返回空串。

    ★459 刀2：新接的 voided 特征**占比不超过一半**（按字符数限额），
      否则 229 条历史作废会把「代码验证失败」那类提醒整段挤掉 ——
      经验库要的是"下次别再犯"，不是"把台账抄一遍"。
    """
    if not _on():
        return ""
    rows = load_recent(200)
    if not rows:
        return ""
    c = collections.Counter(str(r.get("error_type") or "other") for r in rows)
    top = c.most_common(max(1, int(top_n)))
    lines = [REMINDER_HEAD]
    _cap = max(1, int(max_chars))
    _voided_budget = _cap // 2
    _voided_used = 0
    for kind, n in top:
        # 每种类型带一条最近的原始错误，便于 LLM 认出"长什么样"
        sample = ""
        for r in reversed(rows):
            if str(r.get("error_type") or "other") == kind:
                sample = str(r.get("error_msg") or "")[:60]
                break
        _line = f"- {kind}（近 {n} 次）{('：' + sample) if sample else ''}"
        if kind.startswith(VOIDED_PREFIX):
            if _voided_used + len(_line) > _voided_budget:
                continue
            _voided_used += len(_line)
        lines.append(_line)
    text = "\n".join(lines)
    return text[:_cap]


def append_reminder(prompt: str, *, max_chars: int = 300) -> str:
    """★452刀6：把提醒段追加到 prompt 末尾。**幂等**（已含 REMINDER_HEAD 则原样返回）。"""
    if not _on():
        return str(prompt or "")
    p = str(prompt or "")
    if REMINDER_HEAD in p:
        return p
    try:
        block = build_reminder(max_chars=max_chars)
    except Exception as _e:
        silent_exc(_e, where="patch_failure_experience::append_reminder", level="debug")
        return p
    if not block:
        return p
    return p + "\n\n" + block
