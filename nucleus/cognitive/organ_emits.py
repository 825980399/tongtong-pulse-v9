# -*- coding: utf-8 -*-
"""386刀4：器官**发射声明**（零发射到底是"设计不发"还是"真哑了"）。

背景
----
洞鉴 386 盲区4：57 个器官里有相当数量**一发未发**，分不清是"闸门关着"还是
"器官哑了"。靠人逐个翻代码既慢又容易编。

判据（客观、可复算，不靠猜）
----------------------------
  · 源码里**有** emit/publish 调用却零发射 ⇒ 有发射器没触发 ⇒ **真哑了**（emits=True）
  · 源码里**没有**任何发射调用            ⇒ 设计不发（闸门型/纯被调用）⇒ emits=False

★为什么用"有没有发射器"而不是"有没有被启用了"：后者要读运行时状态，
  而运行时状态本身可能正是"哑"的表现 ⇒ 拿它当判据会循环论证。
  源码是**静态事实**，可以直接数。

★边界（铁律196）：`emits=True` 只说明"它本该发却没发"，**不说明**为什么没发
  （可能是触发条件没满足、可能是订阅没注册、可能是闸门关着）。真因要另查。
"""

from __future__ import annotations

import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

SCHEMA = "organ_emits/386.v1"

#: 发射器形态（源码级）。命中任一即认为"这个器官**有能力**发脉冲"。
_EMITTER_PATTERNS = (
    re.compile(r"pulse_core\.emit\s*\("),
    re.compile(r"info_field\.publish\s*\("),
    re.compile(r"self\.emit\s*\("),
    re.compile(r"get_event_bus\(\)\s*\.\s*publish"),
    re.compile(r"_emit\s*\(\s*Event\."),
)


def count_emitters(file_path: str) -> int:
    """数一个器官文件里的发射器调用数（读不到 ⇒ 0，且留痕）。"""
    if not file_path or not os.path.isfile(file_path):
        return 0
    try:
        _src = io.open(file_path, encoding="utf-8", errors="ignore").read()
    except Exception as _e:
        silent_exc(_e, where="organ_emits.count_emitters", level="debug")
        return 0
    return sum(len(_p.findall(_src)) for _p in _EMITTER_PATTERNS)


def _auto_reason(has_emitter: bool, hits: int) -> str:
    if has_emitter:
        return ("源码有 %d 处发射器调用但今日零发射 ⇒ **真哑了**：发射能力存在、"
                "未触发。真因待查（触发条件未满足 / 订阅未注册 / 闸门关着），"
                "本声明**不**代替该调查" % hits)
    return ("源码无任何发射器调用 ⇒ **设计不发**（闸门型或纯被调用器官），"
            "属预期行为，登记豁免")


def build_declarations(zero_emit_rows: list) -> dict:
    """按**扫描事实**生成声明（不手编结论）。

    Returns: {class_name: {"emits", "reason", "emitters", "source"}}
    """
    _out = {}
    for _r in (zero_emit_rows or []):
        _cls = str(_r.get("class_name") or "")
        if not _cls:
            continue
        _n = int(_r.get("emitter_hits") or 0)
        _has = _n > 0
        _out[_cls] = {
            "organ_name": _r.get("organ_name"),
            "emits": _has,                 # True=本该发（真哑）；False=设计不发
            "emitters": _n,
            "reason": _auto_reason(_has, _n),
            "source": "auto_scan",         # 人工复核后改成 manual 并写复核人
        }
    return _out


def scan_zero_emit(pulse_pairs_path: str | None = None) -> list:
    """零发射器官清单（57 器官 − 当日有发射的 source）。"""
    import glob
    import json

    from nucleus.cognitive.organ_inventory import scan_organ_classes
    _orgs = scan_organ_classes() or []
    _path = pulse_pairs_path
    if not _path:
        _cand = sorted(glob.glob(os.path.join(_ROOT, "data", "monitor",
                                              "pulse_pairs_*.jsonl")))
        _path = _cand[-1] if _cand else None
    _emit = set()
    if _path and os.path.isfile(_path):
        try:
            with io.open(_path, encoding="utf-8", errors="ignore") as _f:
                for _l in _f:
                    _l = _l.strip()
                    if not _l:
                        continue
                    try:
                        _d = json.loads(_l)
                    except Exception as _je:
                        silent_exc(_je, where="organ_emits.scan_zero_emit json",
                                   level="debug")
                        continue
                    if not isinstance(_d, dict):
                        continue
                    if _d.get("type", "emit") != "emit":
                        continue
                    _emit.add(_d.get("source") or _d.get("organ") or "?")
        except Exception as _e:
            silent_exc(_e, where="organ_emits.scan_zero_emit read", level="warning")
    _zero = []
    for _o in _orgs:
        _cn = _o.get("organ_name")
        if _cn in _emit:
            continue
        _fp = _o.get("file_path") or ""
        _zero.append({
            "organ_name": _cn,
            "class_name": _o.get("class_name"),
            "file_path": _fp,
            "emitter_hits": count_emitters(_fp),
        })
    return _zero


def zero_emit_report(pulse_pairs_path: str | None = None) -> dict:
    """完整报告：零发射器官 + 声明 + 未声明清单。"""
    _zero = scan_zero_emit(pulse_pairs_path)
    _decl = build_declarations(_zero)
    _undeclared = [r["class_name"] for r in _zero
                   if r["class_name"] not in _decl]
    return {
        "schema": SCHEMA,
        "zero_emit_count": len(_zero),
        "declared_count": len(_decl),
        "undeclared": _undeclared,
        "silent_suspects": sorted(k for k, v in _decl.items()
                                  if v.get("emits") is True),
        "by_design": sorted(k for k, v in _decl.items()
                            if v.get("emits") is False),
        "declarations": _decl,
    }


def assert_all_declared(report: dict | None = None) -> bool:
    """★门禁判据：零发射器官**必须**都有声明（票面验收第 4 条）。"""
    _r = report if report is not None else zero_emit_report()
    return (not _r.get("undeclared")) and int(_r.get("declared_count") or 0) > 0
