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


def _event_const_table() -> dict:
    """从 `nucleus/const.py` 抽出 `ClassName.ATTR → "事件名"` 映射（供输入源解析）。

    只读一次、进程内缓存；解析失败 ⇒ 空表（调用方会标 unknown，不编值）。
    """
    _c = getattr(_event_const_table, "_cache", None)
    if _c is not None:
        return _c
    _out: dict = {}
    _p = os.path.join(_ROOT, "nucleus", "const.py")
    try:
        _src = io.open(_p, encoding="utf-8", errors="ignore").read()
        _cls = ""
        for _l in _src.split("\n"):
            _m = re.match(r"\s*class\s+(\w+)", _l)
            if _m:
                _cls = _m.group(1)
                continue
            _m2 = re.match(r"\s{4,}(\w+)\s*=\s*[\"']([\w.*\-]+)[\"']", _l)
            if _m2 and _cls:
                _out["%s.%s" % (_cls, _m2.group(1))] = _m2.group(2)
    except Exception as _e:
        silent_exc(_e, where="organ_emits._event_const_table", level="debug")
    _event_const_table._cache = _out      # 函数属性缓存（与 385 dl3 shadow 同范式）
    return _out


def emit_guarded(file_path: str) -> bool:
    """★387刀3：发射器是否**被条件包裹**（`if` / `for` / `elif` 之类）。

    为什么要有这一层（本批最重要的教训）：386刀4 的判据是
    「有发射器却零发射 ⇒ 真哑了」。但**血管**的发射器全部在
    「发现沉默器官才告警」的分支里 —— 它零发射恰恰说明**没有器官失联
    ＝系统健康**，不是故障。同理风险感知/设备管理器/全局学习器多是条件发射。

    ⇒ 没有这一层，"零发射"清单会把一大批**正常待命**的器官报成"哑了"。
    """
    if not file_path or not os.path.isfile(file_path):
        return False
    try:
        _src = io.open(file_path, encoding="utf-8", errors="ignore").read()
    except Exception as _e:
        silent_exc(_e, where="organ_emits.emit_guarded", level="debug")
        return False
    _lines = _src.split("\n")
    _guard = re.compile(r"^\s*(if|elif|for|while|with)\b")
    for _i, _l in enumerate(_lines):
        if not any(_p.search(_l) for _p in _EMITTER_PATTERNS):
            continue
        # ① 直接被 if/for 包裹
        for _j in range(_i - 1, max(-1, _i - 12), -1):
            _prev = _lines[_j].strip()
            if not _prev or _prev.startswith("#") or _prev.startswith("@"):
                continue
            if _guard.match(_prev) and _prev.rstrip().endswith(":"):
                return True
            break
        # ② ★387刀3 漏掉的**早退型**（guard clause）：`if <条件>: return`
        #    —— 设备管理器就是这种：`if current_hash == last: return`（能力没变化
        #    就不广播），emit 没有被 if 包住，却在早退之后。
        _ind = len(_l) - len(_l.lstrip())
        for _j in range(max(0, _i - 40), _i):
            _s = _lines[_j]
            _s_strip = _s.strip()
            if (len(_s) - len(_s.lstrip())) != _ind and _s_strip:
                continue
            if not _guard.match(_s_strip) or not _s_strip.rstrip().endswith(":"):
                continue
            # if 之后的下一行（或跳过注释）是不是 return
            for _k in range(_j + 1, min(len(_lines), _j + 4)):
                _nxt = _lines[_k].strip()
                if not _nxt or _nxt.startswith("#"):
                    continue
                if _nxt.startswith("return"):
                    return True
                break
    return False


def declared_input_events(file_path: str) -> list:
    """从 `get_resonance_conditions` 里提取该器官**声明订阅**的事件名。

    ★为什么需要这一层（387刀3 教训）：386刀4 只判「有没有发射器」，把
      「有发射器却零发射」一律标成**真哑了**。但前额叶的真因是
      **复盘队列为空**（它靠 `mouth.speak` 入队，而嘴今日只发了 1 次），
      不是代码坏了 —— 那是**无输入**，与"哑"的处置完全不同。
    """
    if not file_path or not os.path.isfile(file_path):
        return []
    try:
        _src = io.open(file_path, encoding="utf-8", errors="ignore").read()
    except Exception as _e:
        silent_exc(_e, where="organ_emits.declared_input_events", level="debug")
        return []
    _i = _src.find("def get_resonance_conditions")
    if _i < 0:
        return []
    _body = _src[_i:_i + 2000]
    _tbl = _event_const_table()
    _out: list = []
    for _m in re.finditer(r"(\w+Event)\.(\w+)", _body):
        _k = "%s.%s" % (_m.group(1), _m.group(2))
        _v = _tbl.get(_k)
        if _v and _v not in _out:
            _out.append(_v)
    if '"*"' in _body or "'*'" in _body:
        _out.append("*")          # 万能观测者（订阅全部事件）
    return _out


def event_counts(pulse_pairs_path: str | None = None) -> dict:
    """当日各 event_type 的发射次数（客观事实，供输入源比对）。"""
    import glob
    import json

    _path = pulse_pairs_path
    if not _path:
        _cand = sorted(glob.glob(os.path.join(_ROOT, "data", "monitor",
                                              "pulse_pairs_*.jsonl")))
        _path = _cand[-1] if _cand else None
    _out: dict = {}
    if not _path or not os.path.isfile(_path):
        return _out
    try:
        with io.open(_path, encoding="utf-8", errors="ignore") as _f:
            for _l in _f:
                _l = _l.strip()
                if not _l:
                    continue
                try:
                    _d = json.loads(_l)
                except Exception as _je2:
                    silent_exc(_je2, where="organ_emits.event_counts json",
                               level="debug")
                    continue
                if not isinstance(_d, dict):
                    continue
                if _d.get("type", "emit") != "emit":
                    continue
                _e = _d.get("event_type") or "?"
                _out[_e] = _out.get(_e, 0) + 1
    except Exception as _e:
        silent_exc(_e, where="organ_emits.event_counts", level="debug")
    return _out


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


def _auto_reason(has_emitter: bool, hits: int, input_fired: int,
                 input_events: list) -> str:
    """★387刀3：理由必须带上**输入源事实** —— 386刀4 缺这一层，
    把「有发射器但没输入」一律写成"真哑了"，那是两种不同的处置。
    """
    _ins = ", ".join(input_events[:4]) or "（未声明）"
    if not has_emitter:
        return ("源码无任何发射器调用 ⇒ **设计不发**（闸门型或纯被调用器官），"
                "属预期行为，登记豁免")
    if input_fired == 0:
        return ("源码有 %d 处发射器、订阅输入[%s]但**当日这些输入一次都没发** ⇒ "
                "零发射属**无输入**，不是器官故障；验证需先制造对应输入"
                % (hits, _ins))
    return ("源码有 %d 处发射器、订阅输入[%s]当日共 %d 次 ⇒ **有输入仍零发射**，"
            "属真哑嫌疑。真因待查（触发条件未满足 / 队列为空 / 订阅未注册），"
            "本声明**不**代替该调查" % (hits, _ins, input_fired))


#: ★388刀3 人工复核结论 —— 自动判据**看不出调用方条件**时以这里为准。
#   387刀3 的 `emit_guarded` 只扫发射器附近的 if/早退；而这 3 个的触发条件
#   在**调用方**（谁调这个方法），静态扫不到 ⇒ 只能人工读代码后登记。
#   登记后 `source` 记为 "manual_388"，不再标成"真哑嫌疑"。
MANUAL_NOTES_388 = {
    "PulseDeviceManager": (
        "条件广播（非故障）：`_broadcast_capabilities` 先算能力哈希，"
        "`if current_hash == self._last_capability_hash: return` —— "
        "**设备能力没变化就不广播**。零发射 = 摄像头/麦克风/扬声器等能力未变。"),
    "PulseEars": (
        "无语音输入（非故障）：`_emit_voice_message` 需要 Vosk 识别出"
        "**含中文且非噪音单字**的语音。零发射 = 没人对着麦克风说话。"),
    "PulseGlobalLearner": (
        "条件告警（非故障）：唯一的发射点是漂移告警 "
        "`Event.GLOBAL_LEARNER_DRIFT_DETECTED`（PulseGlobalLearner.py:249），"
        "只在检测到模型漂移时才发。零发射 = **没有漂移 = 学习状态健康**。"),
    "PulseBloodVessel": (
        "条件告警（非故障）：4 处发射器全在「发现沉默器官才告警」的分支里，"
        "零发射 = 没有器官失联 = 系统健康。"),
}


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
            "reason": _auto_reason(_has, _n, int(_r.get("input_fired") or 0),
                                   list(_r.get("input_events") or [])),
            # ★387刀3：输入源事实（把"无输入"与"真哑"分开）
            "input_events": list(_r.get("input_events") or []),
            "input_fired": int(_r.get("input_fired") or 0),
            "no_input": bool(_has and int(_r.get("input_fired") or 0) == 0),
            "emit_guarded": bool(_r.get("emit_guarded")),
            "source": "auto_scan",
        }
        # ★人工复核结论覆盖自动判据（388刀3）
        _note = MANUAL_NOTES_388.get(_cls)
        if _note:
            _out[_cls]["reason"] = _note
            _out[_cls]["source"] = "manual_388"
            _out[_cls]["benign"] = True     # 已确认非故障，不再列为嫌疑
        else:
            _out[_cls]["benign"] = False
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
    _ec = event_counts(_path)
    for _o in _orgs:
        _cn = _o.get("organ_name")
        if _cn in _emit:
            continue
        _fp = _o.get("file_path") or ""
        _ins = declared_input_events(_fp)
        # 事件名 → 当日发射数（"*"=万能观测者 ⇒ 只要有任意事件就算有输入）
        if _ins == ["*"]:
            _fired = sum(_ec.values())
            _fired_map = {"*": _fired}
        else:
            _fired_map = {_e: int(_ec.get(_e, 0)) for _e in _ins}
            _fired = sum(_fired_map.values())
        _zero.append({
            "organ_name": _cn,
            "class_name": _o.get("class_name"),
            "file_path": _fp,
            "emitter_hits": count_emitters(_fp),
            "emit_guarded": emit_guarded(_fp),
            # ★387刀3：输入源事实（386刀4 缺这一层 ⇒ 把"无输入"误判成"真哑"）
            "input_events": _ins,
            "input_fired": _fired,
            "input_fired_map": _fired_map,
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
        # ★387刀3：把 386 的"真哑"再分一层 —— 无输入 vs 有输入仍哑
        "no_input": sorted(k for k, v in _decl.items()
                           if v.get("no_input") is True),
        "has_input_still_silent": sorted(
            k for k, v in _decl.items()
            if v.get("emits") is True and v.get("no_input") is not True),
        # ★条件发射：零发射可能是「无事发生」，不是故障（387刀3）
        "conditional": sorted(k for k, v in _decl.items()
                               if v.get("emit_guarded") is True),
        "suspects_strict": sorted(
            k for k, v in _decl.items()
            if v.get("emits") is True and v.get("emit_guarded") is not True
            and v.get("no_input") is not True and v.get("benign") is not True),
        # ★388刀3：已人工确认非故障的（不再当嫌疑）
        "benign_confirmed": sorted(k for k, v in _decl.items()
                                   if v.get("benign") is True),
        "declarations": _decl,
    }


def assert_all_declared(report: dict | None = None) -> bool:
    """★门禁判据：零发射器官**必须**都有声明（票面验收第 4 条）。"""
    _r = report if report is not None else zero_emit_report()
    return (not _r.get("undeclared")) and int(_r.get("declared_count") or 0) > 0
