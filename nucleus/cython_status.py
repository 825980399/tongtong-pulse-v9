# -*- coding: utf-8 -*-
"""
cython_status.py —— Cython状态监测

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 监测Cython编译模块的加载与运行状态
机制: 函数式模块，包含3个工具函数
定位: 性能基础设施层
"""

from __future__ import annotations

from typing import Any


# (模块名, 导入路径, 中文用途)
_EXTENSIONS: list[tuple[str, str, str]] = [
    ("_oscillon_cy", "nucleus.field._oscillon_cy", "振荡场"),
    ("_frequency_codec_cy", "nucleus.pulse._frequency_codec_cy", "频率编解码"),
    ("_resonance_cy", "nucleus.synapsys._resonance_cy", "五维共振"),
    ("_inner_world_math_cy", "nucleus.reasoning._inner_world_math_cy", "内在世界数学"),
    ("_topk_retrieve_cy", "nucleus.reasoning._topk_retrieve_cy", "top-k检索"),
    ("_cosine_cpu_cy", "nucleus.gpu._cosine_cpu_cy", "批量余弦"),
]


def _cython_switch_on() -> bool:
    """读 use_cython_extensions 开关（保守兜底 False）。"""
    try:
        from config import FEATURE
        return bool(FEATURE.get("use_cython_extensions", False))
    except Exception:
        return False


def probe_cython_extensions(do_import: bool = True) -> dict[str, Any]:
    """探测全部 Cython 扩展的可用状态。

    Args:
        do_import: 是否真的尝试 import（默认 True）。
            启动自检时应该为 True——真正的加载状态才是用户关心的。

    Returns:
        {
          "switch_on": bool,        # 总开关
          "total": int,
          "loaded": int,
          "items": [{"name","label","available","reason"}...],
          "all_loaded": bool,
        }
    """
    _switch = _cython_switch_on()
    _items: list[dict[str, Any]] = []

    for _name, _path, _label in _EXTENSIONS:
        _ok = False
        _reason = ""
        if not do_import:
            _reason = "未探测"
        else:
            try:
                import importlib
                importlib.import_module(_path)
                _ok = True
            except ImportError as _e:
                # 区分「没编译」和「编译了但依赖缺失」——后者要提示具体原因
                _msg = str(_e) or "ImportError"
                if "No module named" in _msg and _name in _msg:
                    _reason = "未编译（缺 .pyd/.so）"
                else:
                    _reason = f"依赖缺失: {_msg[:60]}"
            except Exception as _e:
                _reason = f"加载异常: {type(_e).__name__}: {str(_e)[:60]}"

        _items.append({
            "name": _name,
            "label": _label,
            "path": _path,
            "available": _ok,
            "reason": _reason,
        })

    _loaded = sum(1 for _i in _items if _i["available"])
    return {
        "switch_on": _switch,
        "total": len(_items),
        "loaded": _loaded,
        "items": _items,
        "all_loaded": _loaded == len(_items),
    }


def format_cython_report(result: dict[str, Any] | None = None) -> str:
    """把探测结果压成一行人话（供启动日志直接打印）。"""
    if result is None:
        result = probe_cython_extensions()
    _loaded = result.get("loaded", 0)
    _total = result.get("total", 0)

    if _loaded == _total and _total > 0:
        return f"Cython扩展自检: {_loaded}/{_total} 全部加载，底层加速已生效"

    # 有缺失时，把缺失项的名字列出来，方便直接去编译
    _missing = [_i["label"] for _i in result.get("items", []) if not _i["available"]]
    _detail = "、".join(_missing) if _missing else "未知"

    if not result.get("switch_on", False):
        _hint = "（use_cython_extensions=False，已主动关闭，非故障）"
    else:
        _hint = "（将走 Python 回退，性能约降 3 倍；运行 setup_cython.py 可编译）"

    return (f"Cython扩展自检: {_loaded}/{_total} 加载，缺失: {_detail} {_hint}")


__all__ = ["EXTENSIONS", "format_cython_report", "probe_cython_extensions"]

EXTENSIONS = _EXTENSIONS
