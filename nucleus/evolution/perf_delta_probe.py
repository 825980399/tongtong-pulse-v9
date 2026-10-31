# -*- coding: utf-8 -*-
"""perf_delta_ms 供给（★412 批刀3）—— 让 effect_verify 的第三维**有数可判**。

问题（411 批新发现）
--------------------
`EvolutionEffectVerifier.verify()` 判 `passed=True` 要求 `problem_gone /
function_ok / perf_ok` **三维全知**；而 `PatchManager.py:5177` 固定传
`perf_delta_ms=None` ⇒ 只知两维 ⇒ 恒落 `unverifiable`（"覆盖不足不是通过"）。

本刀补第三维的**供给**，不改判据本身。

怎么测（为什么是"A/B 沙箱导入耗时"）
------------------------------------
任务书建议"模块导入时间或函数调用耗时"。两种里：

  * **函数调用耗时**要在生产进程里真跑一次目标方法 —— 有副作用风险（尤其补丁
    刚落地、语义未稳），否决；
  * **导入耗时**可以在**沙箱子进程**里跑，而且能做成真正的 A/B：
      ``before`` = 把 `patch["original_code"]` 写成临时文件后 import；
      ``after``  = 把补丁落地后的**当前文件**复制一份后 import。
    两者差就是这次改动带来的导入耗时变化，**不碰生产进程、不 reload 任何模块**。

★T0 偏差（必须记着）：任务书写"正负 0.5ms 内算 perf_ok"，而实测
``EvolutionEffectVerifier.DEFAULT_MAX_REGRESSION_MS = 1500.0``
（可由 `config.EVOLUTION_EFFECT_VERIFY_CONFIG["max_regression_ms"]` 覆盖）——
**差 3000 倍**。本刀按实测口径供给数值，阈值一个字没动；那 0.5ms 是否要改，
留给星轨裁（见交付报告 §待裁）。

精度声明：导入耗时受磁盘缓存/系统负载影响很大，所以取**多次采样的最小值**，
并把 `samples` 全数列在返回值里 —— **不声称精确到毫秒**。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PerfDeltaProbe")

_M412_PP_VERSION = "412.3"

#: 灰度开关
SWITCH = "ENABLE_M412_PERF_DELTA"

#: 每侧采样次数（取最小值，压掉冷启动抖动）
DEFAULT_REPEATS = 2

#: 单次沙箱墙上时钟上限
DEFAULT_TIMEOUT_SEC = 20.0


def enabled() -> bool:
    """灰度开关 `ENABLE_M412_PERF_DELTA`；缺省 True（只多两次沙箱导入，不改判定）。"""
    try:
        import config as _c
        return bool(getattr(_c, SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="evolution.perf_delta_probe:enabled")
        return True


def _import_code(file_abs: str, project_root_abs: str) -> str:
    """子进程里要做的事：把项目根塞进 sys.path，然后从**文件副本**加载并计时。"""
    return (
        "import importlib.util, sys, time\n"
        f"sys.path.insert(0, {project_root_abs!r})\n"
        "_t0 = time.perf_counter()\n"
        "try:\n"
        f"    _spec = importlib.util.spec_from_file_location('m412_probe', {file_abs!r})\n"
        "    _mod = importlib.util.module_from_spec(_spec)\n"
        "    _spec.loader.exec_module(_mod)\n"
        "    print('M412_IMPORT_OK', flush=True)\n"
        "except BaseException as _e:\n"
        "    import traceback; traceback.print_exc()\n"
        "    sys.stderr.write('M412_IMPORT_FAIL: ' + repr(_e) + '\\n')\n"
        "    sys.exit(3)\n"
        "print('M412_MS', (time.perf_counter() - _t0) * 1000.0, flush=True)\n"
    )


def measure_import_ms(file_abs: str, project_root: str | None = None,
                      repeats: int = DEFAULT_REPEATS,
                      timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """测一次"沙箱里 import 这个文件的耗时"。

    Returns:
        ``{"state": ok|error, "ms": float|None, "samples": list[float], "reason": str}``
        ★取**最小值**：冷启动抖动只会让数变大，最小值最接近稳态。
    """
    _out: dict[str, Any] = {"state": "error", "ms": None, "samples": [], "reason": ""}
    if not file_abs or not os.path.isfile(file_abs):
        _out["reason"] = "file_missing"
        return _out
    try:
        from nucleus.security.sandbox_limits import execute_code_in_subprocess
    except Exception as _e:
        silent_exc(_e, where="evolution.perf_delta_probe:import_sandbox")
        _out["reason"] = "sandbox_unavailable"
        return _out
    _root = os.path.abspath(project_root or os.path.dirname(file_abs))
    _code = _import_code(os.path.abspath(file_abs), _root)
    _samples: list[float] = []
    for _ in range(max(1, int(repeats))):
        try:
            _res = execute_code_in_subprocess(
                _code, timeout_seconds=float(timeout_sec), allow_full_builtins=True,
                source_label="m412_perf_import")
        except Exception as _e:
            silent_exc(_e, where="evolution.perf_delta_probe:run", level="debug")
            continue
        if getattr(_res, "returncode", 1) != 0 or getattr(_res, "limit_hit", None):
            continue
        _line = ""
        for _ln in (_res.stdout or "").splitlines():
            if _ln.startswith("M412_MS"):
                _line = _ln
                break
        if not _line:
            continue
        try:
            _samples.append(float(_line.split()[1]))
        except (IndexError, ValueError) as _e:
            silent_exc(_e, where="evolution.perf_delta_probe:parse", level="debug")
    if not _samples:
        _out["reason"] = "no_sample（沙箱没跑出可用读数）"
        return _out
    _out.update({"state": "ok", "ms": round(min(_samples), 3),
                 "samples": [round(x, 3) for x in _samples]})
    return _out


def _write_temp(src_text: str, name: str = "target", workdir: str | None = None) -> str | None:
    """★两侧必须用**不同文件名**：同名会互相覆盖，A/B 就变成 A/A（首版实测踩到）。"""
    _dir = workdir or tempfile.mkdtemp(prefix="m412_perf_")
    _p = os.path.join(_dir, "m412_%s.py" % str(name))
    try:
        with open(_p, "w", encoding="utf-8", newline="\n") as _f:
            _f.write(str(src_text or ""))
        return _p
    except Exception as _e:
        silent_exc(_e, where="evolution.perf_delta_probe:_write_temp", level="debug")
        return None


def perf_delta_for_patch(patch: dict, project_root: str | None = None,
                         repeats: int = DEFAULT_REPEATS,
                         timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """给一条补丁算出 `perf_delta_ms`（**改后 − 改前**，正数 = 变慢）。

    Returns:
        ``{"state": ok|missing|error, "before_ms", "after_ms", "delta_ms",
           "reason", "repeats"}``

        ★`state="missing"`（拿不到原始代码）⇒ `delta_ms=None` ——
        不拿 0 冒充"没有变化"（0 会被判成 perf_ok）。
    """
    _out: dict[str, Any] = {"state": "error", "before_ms": None, "after_ms": None,
                            "delta_ms": None, "reason": "", "repeats": int(repeats)}
    if not enabled():
        _out.update({"state": "missing", "reason": "disabled"})
        return _out
    if not isinstance(patch, dict):
        _out["reason"] = "patch_not_a_dict"
        return _out
    _file = str(patch.get("file") or "")
    _orig = patch.get("original_code")
    if not _file or not os.path.isfile(_file):
        _out.update({"state": "missing", "reason": "file_missing_or_not_on_disk"})
        return _out
    if not isinstance(_orig, str) or not _orig.strip():
        _out.update({"state": "missing",
                     "reason": "no_original_code（补丁没带原文，无法做 A/B）"})
        return _out

    _work = tempfile.mkdtemp(prefix="m412_perf_ab_")
    try:
        _after_path = _write_temp(_read_text(_file), name="after", workdir=_work)
        _before_path = _write_temp(_orig, name="before", workdir=_work)
        if not _after_path or not _before_path:
            _out["reason"] = "temp_write_failed"
            return _out
        _b = measure_import_ms(_before_path, project_root=project_root,
                               repeats=repeats, timeout_sec=timeout_sec)
        _a = measure_import_ms(_after_path, project_root=project_root,
                               repeats=repeats, timeout_sec=timeout_sec)
        _out["before_ms"] = _b.get("ms")
        _out["after_ms"] = _a.get("ms")
        if _b.get("ms") is None or _a.get("ms") is None:
            _out["state"] = "error"
            _out["reason"] = "measure_failed(before=%s after=%s)" % (
                _b.get("reason") or "ok", _a.get("reason") or "ok")
            return _out
        _out["state"] = "ok"
        # ★正 = 变慢（与 verify() 的 perf_delta_ms 语义一致）
        _out["delta_ms"] = round(float(_a["ms"]) - float(_b["ms"]), 3)
        _module_logger.debug("[412刀3] perf_delta before=%.3fms after=%.3fms delta=%.3fms",
                             float(_b["ms"]), float(_a["ms"]), float(_out["delta_ms"]))
        return _out
    finally:
        try:
            shutil.rmtree(_work, ignore_errors=True)
        except Exception as _e:
            silent_exc(_e, where="evolution.perf_delta_probe:cleanup", level="debug")


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as _f:
            return _f.read()
    except Exception as _e:
        silent_exc(_e, where="evolution.perf_delta_probe:_read_text", level="debug")
        return ""


def version() -> str:
    return _M412_PP_VERSION
