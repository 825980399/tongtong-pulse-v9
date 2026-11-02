#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""397 刀1：Cython 全量重编 + 符号级比对（6 个模块，一次跑完）。

为什么要有这个脚本
------------------
`nucleus/pulse/setup_cython.py` 只覆盖 4 个模块，且产物移动逻辑会把中间文件
散落在 `nucleus/pulse/`（395/394 实测 3 个孤儿 .pyd 就是这么来的）。本脚本：

  * 覆盖**全部 6 个** `.pyx`（新增 reasoning 下的 `_inner_world_math_cy` / `_topk_retrieve_cy`）；
  * 每个模块单独编译、单独落位到它自己的包目录，**不留** pulse/ 下的同名残留；
  * 编完立即做**符号级比对**：`.pyx` 里 `def`/`cpdef` 的公开名 vs 实际 import 后的 `dir()`，
    缺一个就报 `MISSING`，不会被"编成功了"骗过去（pyd 早于 pyx 时符号可能就是旧的）。

编译器
------
MSVC 优先（`cl` 在 PATH）。本机实测 MSVC 不可用（VS 目录为空壳、无 cl.exe/vcvarsall），
自动回退 **MinGW**（`--compiler=mingw32`）。MinGW 产物已用探针验证可 import 且调用正确
（`tmp/cython_probe_mingw.py`），但 ABI 与 MSVC 产物不同 ⇒ 落位前必须冒烟（见 `--smoke`）。

用法
----
    python tools/build_cython_all.py              # 全量重编 + 符号比对
    python tools/build_cython_all.py --smoke      # 额外跑功能冒烟
    python tools/build_cython_all.py --dry-run    # 只打印计划，不编译
    python tools/build_cython_all.py --only _frequency_codec_cy
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

#: (扩展名, pyx 相对根的路径, 落位目录相对根的路径)
_MODULES: list[tuple[str, str, str]] = [
    ("_frequency_codec_cy", "nucleus/pulse/_frequency_codec_cy.pyx", "nucleus/pulse"),
    ("_resonance_cy", "nucleus/synapsys/_resonance_cy.pyx", "nucleus/synapsys"),
    ("_oscillon_cy", "nucleus/field/_oscillon_cy.pyx", "nucleus/field"),
    ("_cosine_cpu_cy", "nucleus/gpu/_cosine_cpu_cy.pyx", "nucleus/gpu"),
    ("_inner_world_math_cy", "nucleus/reasoning/_inner_world_math_cy.pyx", "nucleus/reasoning"),
    ("_topk_retrieve_cy", "nucleus/reasoning/_topk_retrieve_cy.pyx", "nucleus/reasoning"),
]

#: MinGW 候选路径（本机实测落点）
_MINGW_CANDIDATES = (r"D:\mingw64\bin", r"C:\mingw64\bin")
_EXT = ".pyd" if sys.platform == "win32" else ".so"
_ABI = "cp312-win_amd64" if sys.platform == "win32" else ""


def _pick_compiler() -> tuple[str, str | None]:
    """返回 (compiler_arg_or_empty, mingw_bin_or_None)。

    MSVC 可用 ⇒ 用默认编译器；不可用 ⇒ 回退 mingw32 并把 gcc 所在目录加进 PATH。
    """
    if shutil.which("cl"):
        return "", None
    if sys.platform == "win32":
        for _d in _MINGW_CANDIDATES:
            if os.path.exists(os.path.join(_d, "gcc.exe")):
                return "mingw32", _d
    return "", None


def _pyx_public_names(pyx_path: str) -> set[str]:
    """从 `.pyx` 源码抽会被导出到 Python 层的 `def` / `cpdef` 名。

    两条容易踩空的规则：
      * **`cdef` 不算**：`cdef` 是纯 C 函数，Python 层看不到，不能进"应有导出"清单
        （正则用 `(?<![A-Za-z_])` 挡住 `cdef` 里的 `def`）。
      * **下划线开头的也要**：`_md5_int` 就带下划线，但它是 `def` ⇒ 会导出。
        397 票面的验收项正是"`_frequency_codec_cy` 导出 `_md5_int`"，漏掉它等于漏掉本刀目标。
    只排除 dunder 与"嵌套 def"（这里一律按顶层抽，缩进不区分 —— 嵌套 def 不会导出，
    若被误纳入只会让比对更严，不会放过缺口）。
    """
    import re

    _names: set[str] = set()
    _re = re.compile(r"(?<![A-Za-z_])(?:cpdef|def)\s+(?:.*?\s)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                     re.M)
    try:
        with open(pyx_path, encoding="utf-8") as _f:
            _src = _f.read()
    except Exception as _e:
        silent_exc(_e, where="build_cython_all::_pyx_public_names read", level="debug")
        return _names
    for _m in _re.finditer(_src):
        _n = _m.group(1)
        if not _n.startswith("__"):
            _names.add(_n)
    return _names


def _import_public_names(mod_path: str) -> set[str]:
    """import 编出的扩展模块，取公开符号（排除 dunder 与被 import 进来的模块名）。"""
    import importlib
    import types

    try:
        _m = importlib.import_module(mod_path)
    except Exception as _e:
        silent_exc(_e, where=f"build_cython_all::_import_public_names {mod_path}", level="debug")
        return set()
    _out: set[str] = set()
    for _n in dir(_m):
        if _n.startswith("__"):
            continue
        _v = getattr(_m, _n, None)
        if isinstance(_v, types.ModuleType):
            continue          # 被 import 进来的模块（如 math），不是本模块导出
        _out.add(_n)
    return _out


def build_one(ext_name: str, pyx_rel: str, dest_rel: str,
              compiler: str, mingw_bin: str | None, dry: bool = False) -> dict[str, Any]:
    """编译单个模块并落位。返回 {'ok','moved','missing','detail'}。"""
    _pyx = os.path.join(_ROOT, pyx_rel)
    _dest = os.path.join(_ROOT, dest_rel)
    _res: dict[str, Any] = {"ext": ext_name, "ok": False, "moved": None,
                            "missing": [], "detail": ""}
    if not os.path.exists(_pyx):
        _res["detail"] = f"pyx 不存在: {pyx_rel}"
        return _res
    if dry:
        _res["ok"] = True
        _res["detail"] = "dry-run"
        return _res

    _tmp = os.path.join(_ROOT, "tmp", "_cython_build", ext_name)
    os.makedirs(_tmp, exist_ok=True)

    _builder = os.path.join(_tmp, "_build.py")
    with open(_builder, "w", encoding="utf-8") as _f:
        _f.write(
            "import os, sys\n"
            "from Cython.Build import cythonize\n"
            "from setuptools import Extension, setup\n"
            f"_HERE = {_tmp!r}\n"
            f"setup(ext_modules=cythonize([Extension({ext_name!r}, [{_pyx!r}])], language_level=3),\n"
            "      script_args=['build_ext', '--inplace'"
            + (f", '--compiler={compiler}'" if compiler else "")
            + ", '--build-lib', _HERE, '--build-temp', os.path.join(_HERE, 't')])\n"
        )

    _env = dict(os.environ)
    if mingw_bin:
        _env["PATH"] = mingw_bin + os.pathsep + _env.get("PATH", "")
    _r = subprocess.run([sys.executable, _builder], cwd=_tmp, env=_env,
                        capture_output=True, text=True, timeout=1800)
    if _r.returncode != 0:
        _res["detail"] = (_r.stdout or "")[-800:] + "\n" + (_r.stderr or "")[-800:]
        return _res

    _made = glob.glob(os.path.join(_tmp, f"{ext_name}*{_EXT}"))
    if not _made:
        _res["detail"] = f"编译返回 0 但没找到产物 {ext_name}*{_EXT}"
        return _res
    _src = _made[0]
    _tgt = os.path.join(_dest, os.path.basename(_src))
    shutil.move(_src, _tgt)
    _res["moved"] = os.path.relpath(_tgt, _ROOT).replace("\\", "/")
    _res["ok"] = True
    return _res


def compare(ext_name: str, pyx_rel: str, mod_path: str) -> list[str]:
    """符号级比对：pyx 公开定义 vs 实际导出，返回缺失列表。"""
    _want = _pyx_public_names(os.path.join(_ROOT, pyx_rel))
    _got = _import_public_names(mod_path)
    return sorted(_want - _got)


def smoke(mod_path: str, samples: dict[str, tuple]) -> dict[str, Any]:
    """按给定样例真调一次函数（不是 hasattr 就算过）。"""
    import importlib

    _out: dict[str, Any] = {"mod": mod_path, "ok": False, "called": [], "errors": []}
    try:
        _m = importlib.import_module(mod_path)
    except Exception as _e:
        silent_exc(_e, where=f"build_cython_all::smoke import {mod_path}", level="debug")
        _out["errors"].append(f"import: {type(_e).__name__}: {_e}")
        return _out
    for _fn, _args in samples.items():
        try:
            _v = getattr(_m, _fn)(*_args)
            _out["called"].append(f"{_fn}->{repr(_v)[:60]}")
        except Exception as _e:
            silent_exc(_e, where=f"build_cython_all::smoke {mod_path}.{_fn}", level="debug")
            _out["errors"].append(f"{_fn}: {type(_e).__name__}: {_e}")
    _out["ok"] = not _out["errors"]
    return _out


#: 冒烟样例（**逐个按 pyx 真实签名核对过**，不写"大概能调"的样例 —— 假绿比红更贵）
_SMOKE: dict[str, dict[str, tuple]] = {
    "nucleus.pulse._frequency_codec_cy": {
        "encode_cy": ("测试关键词",),
        "_md5_int": (b"abc",),                                  # ★397 验收项
        "encode_batch_cy": ([{"value": "a", "keywords": ["x"]}],),  # 收 dict 列表，不是 str 列表
    },
    "nucleus.field._oscillon_cy": {
        "calculate_resonance_cy": (1.0, 1.0, 0.5),
        "try_lock_frequency_cy": (1.0, 1.0, 0.1),
    },
    "nucleus.gpu._cosine_cpu_cy": {
        "batch_cosine_cy": ([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]]),
    },
    "nucleus.synapsys._resonance_cy": {
        "calc_memory_dim_cy": (1.0, 1.0, 0.5, 3),
        "calc_time_dim_cy": (10.0,),
        "calc_state_dim_cy": (2,),
    },
    "nucleus.reasoning._inner_world_math_cy": {
        "cosine_similarity_cy": ([1.0, 2.0], [1.0, 2.0]),
        "calc_trust_boost": (0.5, 0.1),
        "normalize_scores": ([1.0, 2.0, 3.0],),
    },
    "nucleus.reasoning._topk_retrieve_cy": {
        "topk_retrieve_cy": ([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]], 1),
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="397刀1 Cython 全量重编 + 符号比对")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--only", default="", help="只编指定扩展名")
    args = ap.parse_args(argv)

    _compiler, _mingw = _pick_compiler()
    print("=" * 74)
    print("397刀1 · Cython 全量重编")
    print(f"  根 {_ROOT}")
    print(f"  编译器 {'MSVC(cl)' if not _compiler else 'MinGW(--compiler=' + _compiler + ')'}"
          f"{' @ ' + _mingw if _mingw else ''}")
    print("=" * 74)

    _bad = 0
    for _ext, _pyx, _dest in _MODULES:
        if args.only and args.only != _ext:
            continue
        _r = build_one(_ext, _pyx, _dest, _compiler, _mingw, args.dry_run)
        _mod_path = _dest.replace("/", ".").replace("\\", ".") + "." + _ext
        _tail = f"pyx={os.path.getmtime(os.path.join(_ROOT, _pyx)):.0f}"
        if _r["ok"] and not args.dry_run:
            _r["missing"] = compare(_ext, _pyx, _mod_path)
            if _r["missing"]:
                _r["ok"] = False
        _pyd = os.path.join(_ROOT, _dest, f"{_ext}*{_EXT}")
        _pydm = ""
        _g = glob.glob(_pyd)
        if _g:
            _pydm = f"pyd={os.path.getmtime(_g[0]):.0f}"
        _tag = "OK  " if _r["ok"] else "FAIL"
        print(f"\n[{_tag}] {_ext:<24} {_tail} {_pydm}  → {_r['moved'] or '-'}")
        if _r["missing"]:
            print(f"        MISSING 符号: {_r['missing']}")
        if not _r["ok"]:
            _bad += 1
            print(f"        detail: {_r['detail'][:400]}")

    if args.smoke:
        print("\n" + "-" * 74)
        print("功能冒烟（真调用，不是 hasattr）")
        for _mod, _samples in _SMOKE.items():
            if args.only and not _mod.endswith(args.only):
                continue
            _s = smoke(_mod, _samples)
            print(f"  [{'OK  ' if _s['ok'] else 'FAIL'}] {_mod}")
            for _c in _s["called"]:
                print(f"        {_c}")
            for _e in _s["errors"]:
                print(f"        ERR {_e}")
                _bad += 1

    print("\n" + "=" * 74)
    print(f"结论：{'PASS' if _bad == 0 else f'FAIL（{_bad} 项）'}")
    return 0 if _bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
