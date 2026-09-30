"""补丁历史统计 —— **单一真相源**（主线第40批 T3 / P2-260、P2-247）。

背景
----
第39批 T3 为 ``SafeEvolutionExecutor`` 新增只读公共接口 ``get_evolution_stats()``，
但 ``SelfAwarenessEngine._evolution_raw_stats()``（私有回退路径）**仍保留一份独立
实现** → 两份统计逻辑并存，口径存在漂移风险（实测两处 ``avg_effectiveness``
取值来源不同：一处读顶层 ``effectiveness``，一处读 ``runtime_verify_result`` 内）。

本模块把「从补丁历史列表计算统计」抽为**唯一实现**，供两侧共同调用：

* ``nucleus/reasoning/SafeEvolutionExecutor.get_evolution_stats()``（公共接口）
* ``nucleus/self_awareness/SelfAwarenessEngine._evolution_raw_stats()``（回退路径）

口径（与第38/39批保持一致，仅消除二者的分歧）
------------------------------------------------
===================  ====================================================
字段                  判据
===================  ====================================================
``total``            历史条数
``applied``          ``applied`` 为真
``rolled_back``      ``applied`` 且（``rolled_back`` 或 ``status == "rolled_back"``）
``successful``       ``applied`` 且（未回滚且）``runtime_verified``
``failed``           ``applied`` 且 ``runtime_verify_result.verified is False``
``pending_verify``   ``applied`` 且 ``needs_runtime_verify``
``avg_effectiveness`` 各条 ``runtime_verify_result.effectiveness`` 的均值
===================  ====================================================

★分支顺序即优先级（rolled_back > successful > failed > pending_verify），
  保证一条补丁**最多计入一个**互斥分桶。

★只读：本模块不写入、不改状态、不发起网络请求。
"""
from __future__ import annotations

from typing import Any

from nucleus.evolution.patch_dedup import dedup_history

__all__ = ["EMPTY_STATS", "stats_from_patch_history"]


def _blank() -> dict[str, Any]:
    return {
        "total": 0,
        "applied": 0,
        "successful": 0,
        "failed": 0,
        "rolled_back": 0,
        "pending_verify": 0,
        "avg_effectiveness": 0.0,
        # ★第43批 T4：去重口径（raw_total=去重前，duplicates_removed=被移除条数）
        "raw_total": 0,
        "duplicates_removed": 0,
    }


#: 空统计（调用方可用于初始化 / 比较）。
EMPTY_STATS = _blank()


def stats_from_patch_history(hist: Any) -> dict[str, Any]:
    """从补丁历史列表计算统计（**唯一口径实现**）。

    Args:
        hist: 补丁历史（``PatchManager.load_json`` 的结果）。非 list → 全 0。

    Returns:
        见模块 docstring 的字段表；``avg_effectiveness`` 为 4 位小数的均值
        （无有效样本时 0.0）。
    """
    _out = _blank()
    if not isinstance(hist, list):
        return _out
    # ★主线第43批 T4（P1-280）：**按 id 去重后再统计**。
    #   实测 67 条记录只有 60 个唯一 id（7 个 id 各有 2 条：先 applied=False 后 applied=True），
    #   不去重会使"总补丁数 / 成功率"全部失真。
    _hist, _removed = dedup_history(hist)
    _out["raw_total"] = len(hist)
    _out["duplicates_removed"] = len(_removed)
    _out["total"] = len(_hist)
    _eff: list[float] = []
    for _p in _hist:
        if not isinstance(_p, dict) or not _p.get("applied"):
            continue
        _out["applied"] += 1
        _vr = _p.get("runtime_verify_result")
        # ★互斥分桶（顺序即优先级）
        if _p.get("rolled_back") or _p.get("status") == "rolled_back":
            _out["rolled_back"] += 1
        elif _p.get("runtime_verified"):
            _out["successful"] += 1
        elif isinstance(_vr, dict) and _vr.get("verified") is False:
            _out["failed"] += 1
        elif _p.get("needs_runtime_verify"):
            _out["pending_verify"] += 1
        # 效果值统一取自 runtime_verify_result（第40批统一口径）
        if isinstance(_vr, dict):
            _e = _vr.get("effectiveness")
            if isinstance(_e, (int, float)) and not isinstance(_e, bool):
                _eff.append(float(_e))
    if _eff:
        _out["avg_effectiveness"] = round(sum(_eff) / len(_eff), 4)
    return _out
