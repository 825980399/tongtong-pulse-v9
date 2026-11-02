# -*- coding: utf-8 -*-
"""397 刀4 验收：13 个幽灵开关补 config 赋值。

票面：13 个开关**代码在读、config.py 里没有赋值行**、`getattr(..., True)` 默认真 ——
出事时无法用配置降级，只能改代码。要求：全部补上显式赋值（`= True` + 注释说明为什么
默认 True），并区分生产开关 vs 测试专用（`ENABLE_COLD_STORAGE` 只在测试里读）。

★T0 核实（不许照抄票面数字）：
  本机按 `getattr(config, "NAME", default)` 字面量扫生产面，得到 **15 个**无赋值键：
    11 个 ENABLE_* + PENDING_GRADED_PILOT_ONLY + ENABLE_COLD_STORAGE = **13 个开关**
    （正好对上票面）＋ 4 个**非开关参数**（阈值/路径）= 15。
  那 4 个参数同样只能在代码里改（EVENTBUS_QUEUE_ALARM_DEPTH / PAID_API_TRACE_REL /
  PAID_USAGE_HEARTBEAT_EVERY_BEATS / WRITE_FACE_OBSERVE_EVERY_SEC），本批一并补齐，
  但**单列说明它们是阈值不是开关**，不计入 13。

★本批纪律：默认值一律沿用代码里 getattr 的第三个参数 —— **不改任何行为**。
  所以下面有一条断言专门守"config 值 == 代码默认值"，防止"补赋值"顺手变成"改默认"。
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config as _cfg  # noqa: E402
from nucleus._silent_except import silent_exc  # noqa: E402

#: 13 个生产/测试开关（票面）
_SWITCHES = [
    "ENABLE_AUDIT_POOL_CONSUME",
    "ENABLE_AUDIT_POOL_PERSIST",
    "ENABLE_L2_ADMIT_SYSTEM_INTERNAL",
    "ENABLE_M378_SANDBOX_TTL_FREEZE",
    "ENABLE_M379_LIFELINE_LAYER",
    "ENABLE_M380_TICK_CONSUMER_GATE",
    "ENABLE_M383_OA4_SHADOW",
    "ENABLE_PAID_USAGE_HEARTBEAT",
    "ENABLE_PENDING_GRADED_CANDIDATE",
    "ENABLE_WRITE_FACE_OBSERVE",
    "PENDING_GRADED_PILOT_ONLY",
    "ENABLE_COLD_STORAGE",          # 测试专用，单列
]

#: 票面外的 4 个同病参数（阈值/路径，不是开关）
_PARAMS = [
    "EVENTBUS_QUEUE_ALARM_DEPTH",
    "PAID_API_TRACE_REL",
    "PAID_USAGE_HEARTBEAT_EVERY_BEATS",
    "WRITE_FACE_OBSERVE_EVERY_SEC",
]

_CONFIG_SRC = open(os.path.join(_ROOT, "config.py"), encoding="utf-8").read()
_ASSIGNED = set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*=", _CONFIG_SRC, re.M))
_CFG_VARS = ("config", "_c", "cfg", "_cfg", "_config", "conf", "_conf")
_RE_GET = re.compile(r'getattr\(\s*([A-Za-z_]\w*)\s*,\s*["\']([A-Z][A-Z0-9_]*)["\']\s*,\s*([^)]*?)\s*\)')


def _prod_files():
    """生产面 .py（排除 tmp/tools/tests/docs/.bak*/data）。"""
    _out = []
    for _dp, _dns, _fns in os.walk(_ROOT):
        _dns[:] = [d for d in _dns
                   if d not in {"__pycache__", "node_modules", ".git", ".venv", "venv"}
                   and not d.startswith(".bak")]
        _rel = os.path.relpath(_dp, _ROOT).replace("\\", "/")
        if _rel == "." or _rel.split("/")[0] in {"tmp", "tools", "tests", "docs", "data"}:
            continue
        for _f in _fns:
            if _f.endswith(".py") and _f != "config.py":
                _out.append(os.path.join(_dp, _f))
    return _out


def _scan_getattr_defaults() -> dict[str, list]:
    """生产面里 `getattr(<cfgvar>, "NAME", default)` 的 NAME → [(默认值, 文件)]。

    ★用 **AST** 而不是正则：正则会把注释/docstring 里的示例一起扫进来 ——
    实测 `patch_execution_dispatch.py` 的 docstring 里有 `getattr(cfg,'X',default)`
    这种说明性文字，正则扫出一个根本不存在的"幽灵开关 X"（铁律 227 同型）。
    AST 只看真实调用节点，注释与字符串一律不命中。
    """
    _res: dict[str, list] = {}
    for _p in _prod_files():
        try:
            _tree = ast.parse(open(_p, encoding="utf-8", errors="replace").read())
        except SyntaxError as _e:
            silent_exc(_e, where="m397_k4::_scan_getattr_defaults parse", level="debug")
            continue
        _rel = os.path.relpath(_p, _ROOT).replace("\\", "/")
        for _node in ast.walk(_tree):
            if not isinstance(_node, ast.Call) or len(_node.args) != 3:
                continue
            _fn = _node.func
            if not (isinstance(_fn, ast.Name) and _fn.id == "getattr"):
                continue
            _var, _name, _def = _node.args
            if not (isinstance(_var, ast.Name) and _var.id in _CFG_VARS):
                continue
            if not (isinstance(_name, ast.Constant) and isinstance(_name.value, str)):
                continue
            _key = _name.value
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", _key):
                continue
            try:
                _val = ast.literal_eval(_def)
            except Exception as _e:
                silent_exc(_e, where="m397_k4::_scan_getattr_defaults default",
                           level="debug")
                _val = None      # 默认值不是字面量（变量/表达式）⇒ 只登记键，不比对值
            _res.setdefault(_key, []).append((_val, _rel))
    return _res


# ---------------------------------------------------------------- ① 赋值行存在
@pytest.mark.parametrize("name", _SWITCHES + _PARAMS)
def test_01_has_assignment_line(name):
    """票面验收：每个开关都能在 config.py 里找到赋值行。"""
    assert name in _ASSIGNED, f"config.py 仍无 {name} 的赋值行"
    assert hasattr(_cfg, name), f"config 模块里读不到 {name}"


def test_02_no_longer_relies_on_getattr_default():
    """★核心：不再靠 getattr 默认值 —— 出事时改 False 就能降级。"""
    _scan = _scan_getattr_defaults()
    _still_ghost = [n for n in _scan if n not in _ASSIGNED]
    assert not _still_ghost, f"这些键仍无 config 赋值行：{_still_ghost}"


# ---------------------------------------------------------------- ② 默认值一致（零回归）
@pytest.mark.parametrize("name", _SWITCHES + _PARAMS)
def test_03_config_value_equals_code_default(name):
    """★防"补赋值顺手改默认"：config 里的值必须**等于**代码 getattr 的第三个参数。

    补赋值的目的是"可降级"，不是"改行为"。任何一个对不上都说明本批偷偷改了默认。
    """
    _scan = _scan_getattr_defaults()
    if name not in _scan:
        pytest.skip(f"{name} 在生产面没有 getattr 读点（如测试专用的 ENABLE_COLD_STORAGE）")
    _vals = [v for v, _f in _scan[name] if v is not None]
    if not _vals:
        pytest.skip(f"{name} 的默认值不是字面量，无法自动比对")
    assert len(set(_vals)) == 1, f"{name} 在不同读点默认值不一致：{set(_vals)}"
    _want = _vals[0]
    _got = getattr(_cfg, name)
    assert type(_got) is type(_want) or isinstance(_got, type(_want)), \
        f"{name}: config 值类型 {type(_got).__name__} ≠ 代码默认 {type(_want).__name__}"
    assert _got == _want, f"{name}: config={_got!r} 与代码默认 {_want!r} 不一致"


def test_04_all_switches_default_true():
    """11 个生产开关的当前值应为 True（票面："当前默认 True 就写 = True"）。"""
    for _n in _SWITCHES:
        if _n == "ENABLE_COLD_STORAGE":
            continue
        assert getattr(_cfg, _n) is True, f"{_n} 不是 True"


# ---------------------------------------------------------------- ③ 测试专用单列
def test_05_cold_storage_is_test_only():
    """`ENABLE_COLD_STORAGE` 只在 tests/ 里被读，生产面无人引用。"""
    _prod = _prod_files()
    for _p in _prod:
        try:
            _src = open(_p, encoding="utf-8", errors="replace").read()
        except Exception as _e:
            silent_exc(_e, where="m397_k4::test_05 read", level="debug")
            continue
        assert "ENABLE_COLD_STORAGE" not in _src, \
            f"{os.path.relpath(_p, _ROOT)} 生产面引用了 ENABLE_COLD_STORAGE（不再是测试专用）"


def test_06_cold_storage_marked_in_config():
    """config 里必须写明它是测试专用（否则下一个人会当成生产开关）。"""
    _i = _CONFIG_SRC.index("ENABLE_COLD_STORAGE = ")
    _seg = _CONFIG_SRC[max(0, _i - 600): _i]
    assert "测试专用" in _seg, "config 未标注 ENABLE_COLD_STORAGE 为测试专用"


# ---------------------------------------------------------------- ④ 防新增
def test_07_no_new_ghost_flags():
    """★防回退：生产面所有 getattr 读的 config 键都必须有赋值行。

    这是本刀唯一能"永久生效"的判据 —— 13 个补完了，但下一批还会有人加新的幽灵开关。
    """
    _scan = _scan_getattr_defaults()
    assert len(_scan) >= 15, f"扫描面太小（{len(_scan)} 个键），判据不可信"
    _ghost = sorted(n for n in _scan if n not in _ASSIGNED)
    assert not _ghost, f"出现新的幽灵开关：{_ghost}"


def test_08_scan_sanity():
    """扫描面自检：必须真的扫到了这 15 个键（否则上面的"通过"是空跑）。"""
    _scan = _scan_getattr_defaults()
    for _n in _SWITCHES + _PARAMS:
        if _n == "ENABLE_COLD_STORAGE":
            continue        # 测试专用，生产面无读点
        assert _n in _scan, f"{_n} 没被扫到 —— 正则/扫描面失效会让本文件恒绿"
