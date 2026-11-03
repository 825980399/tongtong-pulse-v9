# -*- coding: utf-8 -*-
"""tools/pilot_439_first_patch.py —— 439 刀3：首条端到端证明（隔离副本）。

【为什么在副本上跑】
任务书要求「对 PILOT_FIRST_BATCH 的 refresh_runtime_params 生成补丁 → 批准 → apply → 回测」。
T0 实测：`PulseController.refresh_runtime_params` 与 `PulseCodeSandbox.refresh_runtime_params`
**都已经用 `except ... as e: self._log(...)` 处理异常**，文件里没有任何 bare `except: pass`
⇒ `probe_code_repro` 复现出 0 ⇒ 439 刀1 的 `baseline_positive` 闸门（合理）会拦下它。
换句话说：**生产上的这两个方法没有可修的缺陷，硬造一条补丁只能是假修**。

所以本工具做两件事，都不改生产源码：
  ① `--target prod`：按**真实文件**跑一遍闸门，证明它会正确拒绝（负向证明）；
  ② `--target sandbox`：在 `tmp/pilot439/` 的**副本**上注入同款缺陷，跑完整条链
     （baseline → approve → apply → landed → effect_verify → 健康检查 → 回滚演练），
     证明「管道本身是通的」（正向证明）。

用法：
    python tools/pilot_439_first_patch.py --target sandbox
    python tools/pilot_439_first_patch.py --target prod
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TARGET_REL = os.path.join("organs", "motor", "PulseController.py")
METHOD = "refresh_runtime_params"
SANDBOX_REL = os.path.join("tmp", "pilot439")

_DEFECT = """    def refresh_runtime_params(self):
        \"\"\"★P1: 刷新运行时参数（热加载后调用）。\"\"\"
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'search_cooldown_seconds' in _rp:
                self._search_cooldown = _rp['search_cooldown_seconds']
        except Exception:
            pass
"""

_FIXED = """    def refresh_runtime_params(self):
        \"\"\"★P1: 刷新运行时参数（热加载后调用）。\"\"\"
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'search_cooldown_seconds' in _rp:
                self._search_cooldown = _rp['search_cooldown_seconds']
        except Exception as e:
            self._log(LogLevel.ERROR, f'refresh_runtime_params 失败: {e}')
"""


def _method_block(src: str, name: str) -> str | None:
    """取出一个方法块（从 `def name` 到下一个同级 def / 文件尾）。"""
    _m = re.search(rf"^(\s*)def {re.escape(name)}\(", src, re.MULTILINE)
    if not _m:
        return None
    _indent = _m.group(1)
    _start = _m.start()
    _rest = src[_m.end():]
    _nxt = re.search(rf"^{_indent}def ", _rest, re.MULTILINE)
    _end = _m.end() + (_nxt.start() if _nxt else len(_rest))
    return src[_start:_end]


def _compile_ok(path: str) -> tuple[bool, str]:
    try:
        py_compile.compile(path, doraise=True)
        return True, ""
    except py_compile.PyCompileError as _e:
        print(f"[pilot439] 编译失败: {str(_e)[:200]}")
        return False, str(_e)[:300]


def _health_check(path: str, seconds: float = 0.0) -> dict:
    """健康检查：语法 + 方法仍在 + （可选）等待窗口。返回结构化结果。"""
    _ok, _why = _compile_ok(path)
    _src = ""
    try:
        with open(path, encoding="utf-8") as _f:
            _src = _f.read()
    except OSError as _e:
        print(f"[pilot439] 读回失败: {_e}")
        return {"ok": False, "why": f"读回失败: {_e}"}
    _has = f"def {METHOD}(" in _src
    if seconds > 0:
        time.sleep(seconds)
    return {"ok": bool(_ok and _has), "compile": _ok, "method_present": _has,
            "why": _why, "waited_sec": seconds}


def run_sandbox() -> dict:
    """正向证明：副本上注入缺陷 → 走完整条链。"""
    from nucleus.evolution import approval_bridge as ab
    from nucleus.evolution import baseline_probe as bp
    from nucleus.evolution.patch_active_reprobe import active_reprobe

    _box = os.path.join(_ROOT, SANDBOX_REL)
    os.makedirs(_box, exist_ok=True)
    _work = os.path.join(_box, "PulseController.py")
    _bak = _work + ".bak439"
    _out: dict = {"target": "sandbox", "stages": []}

    with open(_work, "w", encoding="utf-8") as _f:
        _f.write("import logging\n\n\nLogLevel = logging\n\n\nclass _Log:\n"
                 "    def _log(self, level, msg):\n        print(msg)\n\n\n"
                 "class PulseController(_Log):\n" + _DEFECT)
    shutil.copy2(_work, _bak)
    _out["stages"].append({"stage": "backup", "ok": os.path.isfile(_bak), "path": _bak})

    _patch = {
        "id": "pilot439_sandbox",
        "file": TARGET_REL.replace("\\", "/"),
        "method": METHOD,
        "issue_type": "silent_exception",
        "original_code": _DEFECT,
        "modified_code": _FIXED,
        "risk_level": "低",
        "trust_score": 75.0,
        "confidence": "high",
        "verified": True,
    }

    # ① 基线（主源 code_repro）
    _bp = bp.apply_to_patch(_patch, path=os.path.join(_box, "no.log"))
    _patch["problem_gone"] = (active_reprobe(_patch).get("reprobe_after_hits") == 0)
    _out["stages"].append({"stage": "baseline", "ok": _patch.get("baseline_errors", 0) > 0,
                           "applied": _bp.get("applied"),
                           "baseline_errors": _patch.get("baseline_errors"),
                           "baseline_source": _patch.get("baseline_source"),
                           "verdict": _patch.get("baseline_verdict"),
                           "problem_gone": _patch["problem_gone"]})

    # ② 闸门 + 决策落盘
    _dec = ab.decision(_patch)
    _rec = ab.record(_dec, _patch, root=_box, by="auto:bridge")
    _out["stages"].append({"stage": "decision", "ok": _dec["approved"],
                           "reason": _dec["reason"], "recorded": _rec.get("ok")})

    # ③ apply（写到副本；生产源码不动）
    with open(_work, encoding="utf-8") as _f:
        _src = _f.read()
    _new = _src.replace(_DEFECT, _FIXED)
    _applied = _new != _src
    with open(_work, "w", encoding="utf-8") as _f:
        _f.write(_new)
    _ok, _why = _compile_ok(_work)
    _out["stages"].append({"stage": "apply", "ok": bool(_applied and _ok),
                           "changed": _applied, "compile": _ok, "why": _why,
                           "landed": bool(_applied and _ok)})

    # ④ 回测（修复后再复现一次）
    _after = active_reprobe(_patch)
    _out["stages"].append({"stage": "effect_verify",
                           "ok": _after.get("reprobe_after_hits") == 0,
                           "baseline_before": _patch.get("baseline_errors"),
                           "reprobe_baseline_hits_after": _after.get("reprobe_baseline_hits"),
                           "after_hits": _after.get("reprobe_after_hits"),
                           "verdict": _after.get("reprobe_verdict")})

    # ⑤ 健康检查
    _h = _health_check(_work, seconds=3.0)
    _out["stages"].append({"stage": "health", "ok": _h["ok"], "detail": _h})

    # ⑥ 回滚演练：故意写坏 → 检查失败 → 从备份恢复 → 再检查
    with open(_work, "w", encoding="utf-8") as _f:
        _f.write(_new + "\n\ndef broken(:\n")
    _bad_ok, _ = _compile_ok(_work)
    shutil.copy2(_bak, _work)
    _rb_ok, _ = _compile_ok(_work)
    _out["stages"].append({"stage": "rollback_rehearsal",
                           "ok": bool((not _bad_ok) and _rb_ok),
                           "detected_bad": (not _bad_ok), "restored_ok": _rb_ok})

    _out["all_ok"] = all(s.get("ok") for s in _out["stages"])
    return _out


def run_prod() -> dict:
    """负向证明：对真文件跑闸门，如实记录被哪一条拦下。"""
    from nucleus.evolution import approval_bridge as ab
    from nucleus.evolution import baseline_probe as bp

    _p = os.path.join(_ROOT, TARGET_REL)
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    _block = _method_block(_src, METHOD) or ""
    _patch = {
        "id": "pilot439_prod_probe",
        "file": TARGET_REL.replace("\\", "/"),
        "method": METHOD,
        "issue_type": "silent_exception",
        "original_code": _block,
        "modified_code": _block,
        "risk_level": "低",
        "trust_score": 75.0,
        "confidence": "high",
        "verified": True,
        "problem_gone": True,
    }
    bp.apply_to_patch(_patch, path=os.path.join(_ROOT, "logs", "pulse.log"))
    return {"target": "prod", "method_block_lines": _block.count("\n"),
            "baseline_errors": _patch.get("baseline_errors"),
            "baseline_source": _patch.get("baseline_source"),
            "verdict": _patch.get("baseline_verdict"),
            "decision": ab.decision(_patch)}


def main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="439 刀3 首条端到端证明")
    _ap.add_argument("--target", choices=("sandbox", "prod"), default="sandbox")
    _ap.add_argument("--json", default="")
    _a = _ap.parse_args(argv)
    _r = run_sandbox() if _a.target == "sandbox" else run_prod()
    if _a.json:
        with open(_a.json, "w", encoding="utf-8") as _f:
            json.dump(_r, _f, ensure_ascii=False, indent=1)
    print(json.dumps(_r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
