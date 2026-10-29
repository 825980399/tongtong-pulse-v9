# -*- coding: utf-8 -*-
"""397 刀1 验收：Cython 重编 + 清孤儿 pyd。

票面（任务书刀1）：
  · 6 个 `.pyd` 全部早于对应 `.pyx`（旧：5 个同秒构建于 09-05），重编后须全部晚于；
  · `_frequency_codec_cy` 缺 `_md5_int` ⇒ MD5 C 化收益为 0；
  · `nucleus/pulse/` 下 3 个没有对应 `.pyx`、也没有生产 import 指向的孤儿 `.pyd`。

★T0 核实差异（必须记录，不许按票面照抄）：
  重编**之前**实测 `_frequency_codec_cy` **已经导出 `_md5_int`**，且 5 组随机字节与
  `hashlib.md5` 逐字节一致。所以"缺 `_md5_int`"这条**不成立**——洞鉴 394 那句实锤
  在本环境无法复现。本批仍然重编（pyd 早于 pyx 是真问题：符号可能与源码不同步），
  并把"符号级一致"做成**可复跑的判据**，而不是只验一个符号在不在。

★pyd 不在版本库（`.gitignore` 第 26 行 `*.pyd`）⇒ CI/新克隆必然没有 pyd。
  本文件的处理：`pyd` 缺失 ⇒ `pytest.skip`（**不是通过**），见 `_require_pyd`。
"""
from __future__ import annotations

import glob
import hashlib
import importlib
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# (扩展名, pyx 相对根路径, 落位目录, 生产 import 路径)
_MODULES = [
    ("_frequency_codec_cy", "nucleus/pulse/_frequency_codec_cy.pyx",
     "nucleus/pulse", "nucleus.pulse._frequency_codec_cy"),
    ("_resonance_cy", "nucleus/synapsys/_resonance_cy.pyx",
     "nucleus/synapsys", "nucleus.synapsys._resonance_cy"),
    ("_oscillon_cy", "nucleus/field/_oscillon_cy.pyx",
     "nucleus/field", "nucleus.field._oscillon_cy"),
    ("_cosine_cpu_cy", "nucleus/gpu/_cosine_cpu_cy.pyx",
     "nucleus/gpu", "nucleus.gpu._cosine_cpu_cy"),
    ("_inner_world_math_cy", "nucleus/reasoning/_inner_world_math_cy.pyx",
     "nucleus/reasoning", "nucleus.reasoning._inner_world_math_cy"),
    ("_topk_retrieve_cy", "nucleus/reasoning/_topk_retrieve_cy.pyx",
     "nucleus/reasoning", "nucleus.reasoning._topk_retrieve_cy"),
]

#: ★孤儿：`nucleus/pulse/` 下这 3 个。真模块在 gpu/ field/ synapsys/。
_ORPHANS = [
    "nucleus/pulse/_cosine_cpu_cy.cp312-win_amd64.pyd",
    "nucleus/pulse/_oscillon_cy.cp312-win_amd64.pyd",
    "nucleus/pulse/_resonance_cy.cp312-win_amd64.pyd",
]

_EXT = ".pyd" if sys.platform == "win32" else ".so"


def _pyd_path(dest: str, ext: str) -> str:
    _g = glob.glob(os.path.join(_ROOT, dest, f"{ext}*{_EXT}"))
    return _g[0] if _g else ""


def _require_pyd(dest: str, ext: str) -> str:
    """取 pyd 路径；不存在就 skip（**不通过**——没采到不等于合格，铁律 276）。"""
    _p = _pyd_path(dest, ext)
    if not _p:
        pytest.skip(f"本机没有 {dest}/{ext}*{_EXT}（.gitignore 忽略 *.pyd，"
                    f"新克隆须先跑 tools/build_cython_all.py）")
    return _p


def _pyx_def_names(pyx: str) -> set[str]:
    """pyx 里会被导出到 Python 层的 `def` / `cpdef` 名（`cdef` 不算，dunder 不算）。"""
    _re = re.compile(r"(?<![A-Za-z_])(?:cpdef|def)\s+(?:.*?\s)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                     re.M)
    with open(pyx, encoding="utf-8") as _f:
        _src = _f.read()
    return {m for m in _re.findall(_src) if not m.startswith("__")}


def _exported(mod_path: str) -> set[str]:
    import types

    _m = importlib.import_module(mod_path)
    return {_n for _n in dir(_m)
            if not _n.startswith("__") and not isinstance(getattr(_m, _n, None), types.ModuleType)}


# ---------------------------------------------------------------- ① 时间戳
@pytest.mark.parametrize("ext,pyx_rel,dest,mod", _MODULES)
def test_01_pyd_newer_than_pyx(ext, pyx_rel, dest, mod):
    """重编后 `.pyd` 必须**晚于**对应 `.pyx`（票面验收标准第 1 条）。"""
    _p = _require_pyd(dest, ext)
    _pyx = os.path.join(_ROOT, pyx_rel)
    assert os.path.exists(_pyx), f"pyx 缺失: {pyx_rel}"
    assert os.path.getmtime(_p) > os.path.getmtime(_pyx), (
        f"{ext}: pyd({os.path.getmtime(_p):.0f}) 不晚于 pyx({os.path.getmtime(_pyx):.0f})"
        f" ⇒ 产物与源码不同步")


# ---------------------------------------------------------------- ② 符号级一致
@pytest.mark.parametrize("ext,pyx_rel,dest,mod", _MODULES)
def test_02_symbols_match_pyx(ext, pyx_rel, dest, mod):
    """★比"一个符号在不在"更严：`.pyx` 里每个 `def`/`cpdef` 都必须出现在导出里。

    只验 `_md5_int` 会漏掉"别的函数也被旧产物吃掉"这类同类缺口。
    """
    _require_pyd(dest, ext)
    _want = _pyx_def_names(os.path.join(_ROOT, pyx_rel))
    assert _want, f"{pyx_rel} 没抽到任何 def —— 正则失效会让本条恒绿，必须查"
    _got = _exported(mod)
    _missing = sorted(_want - _got)
    assert not _missing, f"{mod} 缺导出: {_missing}（pyd 与 pyx 不同步）"


def test_03_md5_int_present_and_correct():
    """票面验收标准第 2 条：`_md5_int` 且与 `hashlib.md5` 逐字节一致。"""
    _require_pyd("nucleus/pulse", "_frequency_codec_cy")
    _m = importlib.import_module("nucleus.pulse._frequency_codec_cy")
    assert hasattr(_m, "_md5_int"), "缺 _md5_int（票面验收项）"
    for _s in (b"", b"a", b"hello world", "曈曈".encode(), b"x" * 1000):
        assert _m._md5_int(_s) == int(hashlib.md5(_s).hexdigest(), 16), repr(_s[:16])


# ---------------------------------------------------------------- ③ 孤儿已清
@pytest.mark.parametrize("rel", _ORPHANS)
def test_04_orphan_pyd_removed(rel):
    assert not os.path.exists(os.path.join(_ROOT, rel)), f"孤儿 pyd 仍在: {rel}"


def test_05_real_modules_stay_in_their_own_package():
    """清孤儿不能把真模块一起清掉（反过来更致命）。"""
    for _ext, _pyx, _dest, _mod in _MODULES:
        assert _pyd_path(_dest, _ext), f"真模块产物被误删: {_dest}/{_ext}"


def test_06_no_production_import_points_at_orphan_path():
    """★删之前要断言"确实没人 import 它"——否则删了才知道有人用。

    扫生产面（nucleus/ organs/ main.py base/ functions/ somatics/ utils/）里
    是否出现 `nucleus.pulse._cosine_cpu_cy` 这类**孤儿路径**字面量。
    """
    _orphan_tokens = ("nucleus.pulse._cosine_cpu_cy", "nucleus.pulse._oscillon_cy",
                      "nucleus.pulse._resonance_cy")
    _scanned = 0
    for _d in ("nucleus", "organs", "base", "functions", "somatics", "utils"):
        for _dp, _dns, _fns in os.walk(os.path.join(_ROOT, _d)):
            _dns[:] = [d for d in _dns if not d.startswith(".bak") and d != "__pycache__"]
            for _f in _fns:
                if not _f.endswith(".py"):
                    continue
                _scanned += 1
                with open(os.path.join(_dp, _f), encoding="utf-8", errors="replace") as _fh:
                    _src = _fh.read()
                for _t in _orphan_tokens:
                    assert _t not in _src, f"{os.path.join(_dp, _f)} 引用了孤儿路径 {_t}"
    for _f in ("main.py",):
        with open(os.path.join(_ROOT, _f), encoding="utf-8", errors="replace") as _fh:
            _src = _fh.read()
        _scanned += 1
        for _t in _orphan_tokens:
            assert _t not in _src, f"{_f} 引用了孤儿路径 {_t}"
    assert _scanned > 50, f"扫描面太小（{_scanned} 个文件），判据不可信"


# ---------------------------------------------------------------- ④ 冒烟（真调用）
@pytest.mark.parametrize("ext,pyx_rel,dest,mod", _MODULES)
def test_07_smoke_call_works(ext, pyx_rel, dest, mod):
    """功能冒烟：真调用一次，不是 hasattr 就算过。"""
    _require_pyd(dest, ext)
    _m = importlib.import_module(mod)
    _samples = {
        "_frequency_codec_cy": {"encode_cy": ("测试关键词",)},
        "_resonance_cy": {"calc_memory_dim_cy": (1.0, 1.0, 0.5, 3)},
        "_oscillon_cy": {"calculate_resonance_cy": (1.0, 1.0, 0.5)},
        "_cosine_cpu_cy": {"batch_cosine_cy": ([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]])},
        "_inner_world_math_cy": {"cosine_similarity_cy": ([1.0, 2.0], [1.0, 2.0])},
        "_topk_retrieve_cy": {"topk_retrieve_cy": ([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]], 1)},
    }.get(ext, {})
    assert _samples, f"{ext} 没有冒烟样例 —— 空样例会让本条恒绿"
    for _fn, _args in _samples.items():
        _v = getattr(_m, _fn)(*_args)
        assert _v is not None, f"{mod}.{_fn} 返回 None"


def test_08_cython_status_all_loaded():
    """生产自检口径：`probe_cython_extensions()` 必须全 loaded。

    ★钉语义不钉文案：它返回的是 dict（`total`/`loaded`/`all_loaded`），
    "6/6" 只是 `format_cython_report()` 打印出来的样子 —— 断言字符串会被格式化变更误伤。
    """
    _require_pyd("nucleus/pulse", "_frequency_codec_cy")
    from nucleus.cython_status import probe_cython_extensions

    _r = probe_cython_extensions()
    assert _r.get("total") == 6, _r
    assert _r.get("loaded") == 6, f"只加载了 {_r.get('loaded')}/6：{_r.get('items')}"
    assert _r.get("all_loaded") is True, _r
    _bad = [i for i in (_r.get("items") or []) if not i.get("available")]
    assert not _bad, f"未加载的扩展: {_bad}"


# ---------------------------------------------------------------- ⑤ 构建脚本
def test_09_build_script_covers_all_six():
    """★防回退：`tools/build_cython_all.py` 必须覆盖全部 6 个 pyx。

    旧脚本 `nucleus/pulse/setup_cython.py` 只覆盖 4 个 —— 少一个就又会漏编。
    """
    with open(os.path.join(_ROOT, "tools", "build_cython_all.py"), encoding="utf-8") as _f:
        _src = _f.read()
    for _ext, _pyx, _dest, _mod in _MODULES:
        assert _pyx in _src, f"构建脚本未覆盖 {_pyx}"
        assert f'"{_ext}"' in _src, f"构建脚本未声明扩展名 {_ext}"


def test_10_all_pyx_have_a_dest():
    """每个 .pyx 都必须有且只有一个落位目录（不留 pulse/ 同名残留的根因）。"""
    _pyx_files = sorted(glob.glob(os.path.join(_ROOT, "nucleus", "*", "_*.pyx")))
    assert len(_pyx_files) == 6, f"pyx 数量变了（{len(_pyx_files)}），本表需同步"
    _covered = {p for _e, p, _d, _m in _MODULES}
    assert _covered == {os.path.relpath(p, _ROOT).replace("\\", "/") for p in _pyx_files}, \
        "pyx 清单与构建表不一致"
