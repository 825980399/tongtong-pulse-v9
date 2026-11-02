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

import ast
import math
import os
import shutil
import tempfile
import textwrap
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PerfDeltaProbe")

#: 形如 `412.3+428.1`：**前缀是 412 的契约版本**（既有单测钉死 startswith("412.")），
#: `+428.1` 是 428 刀3 的扩展标记（A/B 同构 + P95 + 内存）。
_M412_PP_VERSION = "412.3+428.1"

# ===== ★428刀3：A/B 两侧必须"同构" =====
# 【实测】412 首版把 after 侧写成"整文件"、before 侧写成 `original_code` 原文。
#   生产里 original_code 是**方法体片段**（带 8 空格缩进、常以 `try:` 开头），
#   写成 .py 后 import 必失败 ⇒ before 侧永远 `no_sample`。
#   复跑 87 条真实补丁记录，**全部**报
#   `measure_failed(before=no_sample（沙箱没跑出可用读数） after=ok)`。
#   这就是 41 条 verdict 里 40 条 `perf_ok=null` 的真因（不是"没接线"）。
#
# 两个模式（**只加不改**，保住 412 的 15 条契约测试）：
#   * `standalone`  —— original_code 自己就是个完整模块（412 测试的形态）⇒ 原路径；
#   * `revert_in_file` —— original_code 是片段（生产真实形态）⇒ 把**目标文件全文**
#     里的 modified_code 换回 original_code，得到"改前"的完整文件。
#     两侧都是完整模块 ⇒ 才可比。
#   两种都做不到 ⇒ `missing` + 明确原因，**不拿 0 冒充"没有变化"**。
_AB_MODE_STANDALONE = "standalone"
_AB_MODE_REVERT = "revert_in_file"

#: P95 采样下限：样本太少时 P95 退化成最大值，故注明实际样本数
_P95_MIN_SAMPLES = 3

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
        silent_exc(_e, where="evolution.perf_delta_probe:enabled", level="debug")
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
        # ★428刀3：内存占用（RSS，MB）。放在"M412_MS"**之后**打，
        #   保证 412 既有解析逻辑（只找 M412_MS 行）完全不受影响。
        "try:\n"
        "    import psutil as _ps\n"
        "    print('M428_MEM_MB', _ps.Process().memory_info().rss / 1048576.0,"
        " flush=True)\n"
        "except BaseException:\n"
        "    pass\n"
    )


def measure_import_ms(file_abs: str, project_root: str | None = None,
                      repeats: int = DEFAULT_REPEATS,
                      timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """测一次"沙箱里 import 这个文件的耗时"。

    Returns:
        ``{"state": ok|error, "ms": float|None, "samples": list[float], "reason": str}``
        ★取**最小值**：冷启动抖动只会让数变大，最小值最接近稳态。
    """
    _out: dict[str, Any] = {"state": "error", "ms": None, "samples": [],
                            "p95_ms": None, "mem_mb": None, "reason": ""}
    if not file_abs or not os.path.isfile(file_abs):
        _out["reason"] = "file_missing"
        return _out
    try:
        from nucleus.security.sandbox_limits import execute_code_in_subprocess
    except Exception as _e:
        silent_exc(_e, where="evolution.perf_delta_probe:import_sandbox", level="debug")
        _out["reason"] = "sandbox_unavailable"
        return _out
    _root = os.path.abspath(project_root or os.path.dirname(file_abs))
    _code = _import_code(os.path.abspath(file_abs), _root)
    _samples: list[float] = []
    _mems: list[float] = []
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
            elif _ln.startswith("M428_MEM_MB"):
                # ★428刀3：内存读数缺失不算失败（psutil 可能不可用），
                #   只让 mem_mb 保持 None —— 不因为"量不到内存"就丢掉耗时读数。
                try:
                    _mems.append(float(_ln.split()[1]))
                except (IndexError, ValueError) as _me:
                    silent_exc(_me, where="evolution.perf_delta_probe:parse_mem",
                               level="debug")
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
                 "samples": [round(x, 3) for x in _samples],
                 "p95_ms": _m428_p95_ms(_samples),
                 "mem_mb": (round(max(_mems), 3) if _mems else None)})
    return _out


def _m428_p95_ms(samples) -> float | None:
    """P95（毫秒）。样本 < `_P95_MIN_SAMPLES` 时 P95 等价于最大值 —— 如实返回，不修正。"""
    _xs = sorted(float(x) for x in (samples or []))
    if not _xs:
        return None
    _i = math.ceil(0.95 * len(_xs)) - 1
    _i = max(0, min(_i, len(_xs) - 1))
    return round(_xs[_i], 3)


def _m428_parses_standalone(text) -> bool:
    """这段文本能否**独立**当一个模块 `ast.parse` 通过（决定走哪个 A/B 模式）。"""
    _s = str(text or "")
    if not _s.strip():
        return False
    try:
        ast.parse(textwrap.dedent(_s))
        return True
    except SyntaxError:
        return False


def _m428_revert_in_file(full_text, modified_code, original_code) -> str | None:
    """把"已打补丁的整文件"还原成"改前"：`modified_code` → `original_code`（只换首处）。

    返回 None 表示**换不了**（补丁没落地 / 空白漂移）——此时必须报 missing，
    不能拿"没换成的文件"当 before 侧（那等于 A/A，delta 恒 0，会被判成 perf_ok）。

    容错顺序（只做机械等价变换，不改代码语义）：
      ① 原样替换；② 统一换行后替换（CRLF/LF 漂移）；
      ③ 两侧都剥掉行尾空白后替换。
    """
    _full = str(full_text or "")
    _mod = str(modified_code or "")
    _orig = str(original_code or "")
    if not _full or not _mod.strip():
        return None
    if _mod in _full:
        return _full.replace(_mod, _orig, 1)
    _full_n = _full.replace("\r\n", "\n")
    if _mod.replace("\r\n", "\n") in _full_n:
        return _full_n.replace(_mod.replace("\r\n", "\n"), _orig, 1)
    _strip = "\n".join(_ln.rstrip() for _ln in _full_n.splitlines())
    _mod_s = "\n".join(_ln.rstrip() for _ln in _mod.replace("\r\n", "\n").splitlines())
    if _mod_s and _mod_s in _strip:
        return _strip.replace(_mod_s, _orig, 1)
    return None


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
                            "delta_ms": None, "reason": "", "repeats": int(repeats),
                            # ★428刀3：三维度（任务书要的三个指标）
                            "before_p95_ms": None, "after_p95_ms": None,
                            "p95_delta_ms": None,
                            "before_mem_mb": None, "after_mem_mb": None,
                            "mem_delta_mb": None, "ab_mode": ""}
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
        _after_full = _read_text(_file)
        # ★428刀3：before 侧**不能用"能否 ast.parse"来判别** —— 方法体片段
        #   （如 `self._foo(x)\nreturn y`）在模块级完全能解析，但 import 时
        #   要**执行**它 ⇒ `self` 未定义 ⇒ NameError ⇒ 沙箱 no_sample。
        #   实测：首版用 ast.parse 判别，23 条真实补丁仍全部
        #   `ab_mode=standalone / before=no_sample`。
        #   "能否导入"只能实测，故改为**按优先级列候选、逐个实测取第一个成功者**。
        _plans: list[tuple[str, str]] = []
        _rev = _m428_revert_in_file(_after_full, patch.get("modified_code"), _orig)
        if _rev is not None:
            # ① 优先：同一文件的"改前 vs 改后"，这是语义上唯一正确的 A/B
            _plans.append((_AB_MODE_REVERT, _rev))
        # ② 兜底：412 原契约（测试里 original_code 就是整模块）
        _plans.append((_AB_MODE_STANDALONE, _orig))
        _after_path = _write_temp(_after_full, name="after", workdir=_work)
        if not _after_path:
            _out["reason"] = "temp_write_failed"
            return _out
        _a = measure_import_ms(_after_path, project_root=project_root,
                               repeats=repeats, timeout_sec=timeout_sec)
        _out["after_ms"] = _a.get("ms")
        _b = {"ms": None, "reason": "no_plan"}
        _ab_mode = ""
        _tried: list[str] = []
        for _mode, _before_text in _plans:
            _tried.append(_mode)
            _bp = _write_temp(_before_text, name="before", workdir=_work)
            if not _bp:
                continue
            _rb = measure_import_ms(_bp, project_root=project_root,
                                    repeats=repeats, timeout_sec=timeout_sec)
            if _rb.get("ms") is not None:
                _b, _ab_mode = _rb, _mode
                break
            _b = _rb
            _module_logger.debug(
                "[428刀3] A/B 模式 %s 未跑出读数（%s），尝试下一方案",
                _mode, _rb.get("reason") or "?")
        _out["ab_mode"] = _ab_mode
        _out["before_ms"] = _b.get("ms")
        if _b.get("ms") is None or _a.get("ms") is None:
            _out["state"] = "error"
            _out["reason"] = (
                f"measure_failed(tried={','.join(_tried) or '-'} "
                f"before={_b.get('reason') or 'ok'} "
                f"after={_a.get('reason') or 'ok'})")
            return _out
        _out["state"] = "ok"
        # ★正 = 变慢（与 verify() 的 perf_delta_ms 语义一致）
        _out["delta_ms"] = round(float(_a["ms"]) - float(_b["ms"]), 3)
        # ★428刀3：P95 与内存同口径（都是"改后 − 改前"）。任一侧缺读数 ⇒ None，
        #   不拿 0 顶（0 会被读成"没有变化"）。
        _out["before_p95_ms"] = _b.get("p95_ms")
        _out["after_p95_ms"] = _a.get("p95_ms")
        if _b.get("p95_ms") is not None and _a.get("p95_ms") is not None:
            _out["p95_delta_ms"] = round(float(_a["p95_ms"]) - float(_b["p95_ms"]), 3)
        _out["before_mem_mb"] = _b.get("mem_mb")
        _out["after_mem_mb"] = _a.get("mem_mb")
        if _b.get("mem_mb") is not None and _a.get("mem_mb") is not None:
            _out["mem_delta_mb"] = round(float(_a["mem_mb"]) - float(_b["mem_mb"]), 3)
        _module_logger.debug(
            "[428刀3] perf_delta mode=%s before=%.3fms after=%.3fms delta=%.3fms "
            "｜ p95 %.3f→%.3f (Δ%s) ｜ mem %s→%s MB (Δ%s)",
            _ab_mode, float(_b["ms"]), float(_a["ms"]), float(_out["delta_ms"]),
            float(_b["p95_ms"] or 0.0), float(_a["p95_ms"] or 0.0),
            _out["p95_delta_ms"], _out["before_mem_mb"], _out["after_mem_mb"],
            _out["mem_delta_mb"])
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
