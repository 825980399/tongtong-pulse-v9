# -*- coding: utf-8 -*-
"""★第158批 上-A O-A2（P0）：分面成熟度台账（机读）。

定位
----
把「自评」从自然语言散文改成**机读台账**：每次评估产出一条结构化记录，含
**分值 / 等级 / LTS（长期序列快照）/ human_override（人工覆写）/ 打分归因 /
修复率·不可验证率**（★后两项与 N-9 口径对齐）。

为什么单独建件、而不是改 ``quality_score_v2``
---------------------------------------------
``quality_score_v2`` 已有 ``evaluate_v2``（分值）、``quality_level``（等级）、
``record_trend``（趋势 JSONL）。但 ``record_trend`` **只落 score**，把
``formula`` / ``data_source`` / ``raw`` 这些**可证伪归因**丢掉了，且没有
human_override、也没有修复率/不可验证率。本件在其之上补「台账」这一层：
**复用** ``evaluate_v2`` 的评分结果（不重复实现评分），只做结构化落盘与归因保全。

★本件即 **O-A2 与 T-自我审计-2（能力四态账本）共用的台账基建件**——任务书
「一件勿重做」：T2 直接复用 :func:`build_entry` / :func:`append_entry`，勿另建一套。

N-9 口径对齐（第158批 触发指令第9刀 ``8666e24``）
------------------------------------------------
修复率与不可验证率**一律取自** ``patch_quality_evaluator.evaluate_history``
（其 ``real_effectiveness`` / ``reprobe_effectiveness`` / ``N9_REPROBE_EXEMPT``
已在第9刀固化），本件**不另立口径**，只做搬运与入册。

写入面（严格受限）
------------------
只写两个文件：台账 JSONL 与覆写表 JSON，均在 ``data/self_awareness/`` 下。
**测试环境（pytest）下不写生产 data/**（与 DailyScheduler 同款保护）。
除此外不改任何进化决策、不发网络请求、不读生产写路径以外的位置。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "LEDGER_VERSION",
    "ledger_path",
    "override_path",
    "patch_rates",
    "probe_count",
    "load_overrides",
    "set_override",
    "clear_override",
    "lts_snapshot",
    "build_entry",
    "append_entry",
    "load_ledger",
    "register_daily",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 台账结构版本（结构变更时递增，供下游判别兼容性）。
LEDGER_VERSION = "158A-OA2-1"


# ------------------------------------------------------------------ 路径与环境
def ledger_path() -> str:
    """台账文件路径（``data/self_awareness/maturity_ledger.jsonl``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "self_awareness", "maturity_ledger.jsonl")


def override_path() -> str:
    """人工覆写表路径（``data/self_awareness/maturity_overrides.json``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "self_awareness", "maturity_overrides.json")


def _is_test_env() -> bool:
    """测试环境判定（pytest 下不得写生产 data/）。"""
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::_is_test_env")
        return False


def _is_production_data_path(path: str) -> bool:
    """路径是否位于项目的 ``data/`` 下（生产数据区）。"""
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = _PROJECT_ROOT.replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::_is_production_data_path")
        return True


def _writable(path: str) -> bool:
    """是否允许写入该路径（生产区 + 测试环境 → 拒绝）。"""
    if _is_test_env() and _is_production_data_path(path):
        sys.stderr.write("[maturity_ledger] 测试环境跳过生产写入: %s\n" % path)
        return False
    return True


# ------------------------------------------------------------------ 采数（N-9 口径）
def patch_rates(hist_path: str | None = None) -> dict[str, Any]:
    """修复率 / 不可验证率 —— **★严格取自 N-9 口径**，本件不另立判据。

    判据（与 ``patch_quality_evaluator`` 第158批第9刀完全一致）：
      * **可判定且有效**（计入修复率分子）：``real_effectiveness > 0``，
        或复现探针被采纳（``reprobe_used``）且 ``reprobe_effectiveness > 0``。
      * **未修复**（``fake_pass`` / ``bad``）计为未修复。
      * **不可验证**（``unverifiable``）：baseline=0 无参照物，单独计率。

    Returns:
        ``{"total", "fix_rate", "unverifiable_rate", "fixed", "unverifiable",
        "fake_pass", "reprobe_adopted", "source", "n9_aligned"}``；
        采集失败时各项为 ``None`` / 0，且 ``"source"`` 标明原因。
    """
    _out: dict[str, Any] = {
        "total": 0, "fix_rate": None, "unverifiable_rate": None,
        "fixed": 0, "unverifiable": 0, "fake_pass": 0, "reprobe_adopted": 0,
        "source": "unavailable", "n9_aligned": True,
    }
    try:
        from nucleus.evolution.patch_quality_evaluator import evaluate_history
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::patch_rates import")
        _out["source"] = "import_failed"
        return _out
    _p = hist_path or os.path.join(_PROJECT_ROOT, "data", "patches", "patch_history.json")
    if not os.path.isfile(_p):
        _out["source"] = "history_missing"
        return _out
    try:
        _hist = json.load(io.open(_p, encoding="utf-8"))
        _res = evaluate_history(_hist)
        _items = [x for x in (_res.get("patches") or []) if isinstance(x, dict)]
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::patch_rates evaluate")
        _out["source"] = "evaluate_failed"
        return _out

    _n = len(_items)
    if _n == 0:
        _out["source"] = "empty_history"
        return _out

    _fixed = 0
    _unver = 0
    _fake = 0
    _reprobe = 0
    for _it in _items:
        _label = _it.get("label")
        if _label == "unverifiable":
            _unver += 1
            continue
        if _label == "fake_pass":
            _fake += 1
        if bool(_it.get("reprobe_used")):
            _reprobe += 1
        _real = _it.get("real_effectiveness")
        _rp = _it.get("reprobe_effectiveness")
        _eff = _rp if bool(_it.get("reprobe_used")) and isinstance(_rp, (int, float)) else _real
        if isinstance(_eff, (int, float)) and not isinstance(_eff, bool) and _eff > 0:
            _fixed += 1

    _judgeable = _n - _unver
    _out.update({
        "total": _n,
        "fixed": _fixed,
        "unverifiable": _unver,
        "fake_pass": _fake,
        "reprobe_adopted": _reprobe,
        "fix_rate": (round(_fixed / float(_judgeable), 4) if _judgeable > 0 else None),
        "unverifiable_rate": round(_unver / float(_n), 4),
        "source": "patch_quality_evaluator(N-9)",
    })
    return _out


def probe_count() -> int:
    """``data/probe/`` 下探针文件数（O-A2 挂载面之一，只做**读数**不改路由）。"""
    _d = os.path.join(_PROJECT_ROOT, "data", "probe")
    try:
        if not os.path.isdir(_d):
            return 0
        return len([f for f in os.listdir(_d) if os.path.isfile(os.path.join(_d, f))])
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::probe_count")
        return 0


# ------------------------------------------------------------------ human_override
def load_overrides(path: str | None = None) -> dict[str, Any]:
    """读取人工覆写表 ``{dim: {"score", "by", "reason", "ts"}}``。"""
    _p = path or override_path()
    if not os.path.isfile(_p):
        return {}
    try:
        _d = json.load(io.open(_p, encoding="utf-8"))
        return _d if isinstance(_d, dict) else {}
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::load_overrides")
        return {}


def _save_overrides(data: dict[str, Any], path: str | None = None) -> str:
    _p = path or override_path()
    if not _writable(_p):
        return _p
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with io.open(_p, "w", encoding="utf-8") as _f:
            json.dump(data, _f, ensure_ascii=False, indent=2)
    except OSError as e:
        sys.stderr.write("[maturity_ledger] 覆写表写入失败: %s: %s\n"
                         % (type(e).__name__, e))
    return _p


def set_override(dim: str, score: float, by: str = "", reason: str = "",
                 path: str | None = None) -> dict[str, Any]:
    """设置某维度的人工覆写分值（★留痕：谁改的、为什么）。

    Args:
        dim: 维度名（须是 ``evaluate_v2`` 返回的维度之一，否则覆写不生效）。
        score: 覆写后的分值（0-100）。
        by: 操作者标识（如 ``星轨``）。
        reason: 覆写理由（必填语义上，空串也会记录并标注 ``reason_empty``）。

    Returns:
        覆写后的完整覆写表。
    """
    _d = load_overrides(path)
    _d[str(dim)] = {
        "score": float(score),
        "by": str(by),
        "reason": str(reason) if reason else "(reason_empty)",
        "ts": time.time(),
    }
    _save_overrides(_d, path)
    return _d


def clear_override(dim: str, path: str | None = None) -> bool:
    """清除某维度的人工覆写。返回是否确有清除。"""
    _d = load_overrides(path)
    if str(dim) not in _d:
        return False
    _d.pop(str(dim))
    _save_overrides(_d, path)
    return True


# ------------------------------------------------------------------ LTS（长期序列）
def lts_snapshot(prev: list[dict] | None, cur_score: float | None) -> dict[str, Any]:
    """长期序列快照：条数、上一条分值、差值、EMA、上一条等级。

    EMA 取 alpha=0.3（近端更敏感但保留历史惯性），仅对非 None 分值累计。
    """
    _rows = [r for r in (prev or []) if isinstance(r, dict)]
    _scores = [r.get("score") for r in _rows if isinstance(r.get("score"), (int, float))]
    _prev = _scores[-1] if _scores else None
    _ema = None
    for _s in _scores:
        _ema = float(_s) if _ema is None else (0.3 * float(_s) + 0.7 * _ema)
    if cur_score is not None and isinstance(cur_score, (int, float)) and not isinstance(cur_score, bool):
        _ema = float(cur_score) if _ema is None else (0.3 * float(cur_score) + 0.7 * _ema)
    return {
        "entries": len(_rows),
        "prev_score": _prev,
        "delta": (round(float(cur_score) - float(_prev), 2)
                  if (_prev is not None and cur_score is not None
                      and isinstance(cur_score, (int, float))) else None),
        "ema": (round(_ema, 2) if _ema is not None else None),
        "prev_level": (_rows[-1].get("level") if _rows else None),
    }


# ------------------------------------------------------------------ 台账构建与读写
def _weighted_score(dims: dict[str, dict]) -> float | None:
    """按 v2 权重对维度加权（只计可用项，权重归一化；与 ``evaluate_v2`` 同规则）。"""
    try:
        from nucleus.self_awareness.quality_score_v2 import DIMENSION_WEIGHTS_V2
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::_weighted_score import")
        return None
    _acc, _tw = 0.0, 0.0
    for _name, _d in (dims or {}).items():
        if not isinstance(_d, dict):
            continue
        _s = _d.get("score")
        if not isinstance(_s, (int, float)) or isinstance(_s, bool):
            continue
        _w = float(DIMENSION_WEIGHTS_V2.get(_name, 0.0))
        if _w <= 0:
            continue
        _acc += float(_s) * _w
        _tw += _w
    return round(_acc / _tw, 2) if _tw > 0 else None


def build_entry(result: dict[str, Any], overrides: dict[str, Any] | None = None,
                prev: list[dict] | None = None, batch: str = "") -> dict[str, Any]:
    """由 ``evaluate_v2`` 结果构建一条台账条目（★保全归因）。

    Args:
        result: ``quality_score_v2.evaluate_v2`` 的返回值。
        overrides: 人工覆写表；``None`` → 自动载入。
        prev: 历史台账条目（用于 LTS）；``None`` → 自动载入最近 50 条。
        batch: 批次标签（如 ``158上-A``），便于按批检索。

    Returns:
        台账条目 dict（未落盘）。
    """
    _ov = load_overrides() if overrides is None else (overrides or {})
    _prev = load_ledger(limit=50) if prev is None else (prev or [])

    _dims = dict(result.get("dimensions") or {})
    # ---- 应用 human_override（★留痕：覆写前后都记）
    _applied: dict[str, Any] = {}
    for _name, _cfg in (_ov or {}).items():
        if not isinstance(_cfg, dict) or _name not in _dims:
            continue
        _s = _cfg.get("score")
        if not isinstance(_s, (int, float)) or isinstance(_s, bool):
            continue
        _orig = _dims[_name].get("score") if isinstance(_dims[_name], dict) else None
        _dims[_name] = dict(_dims[_name])
        _dims[_name]["score"] = round(float(_s), 2)
        _dims[_name]["human_override"] = {
            "by": _cfg.get("by", ""),
            "reason": _cfg.get("reason", ""),
            "ts": _cfg.get("ts"),
        }
        _applied[_name] = {"from": _orig, "to": round(float(_s), 2)}

    _score = result.get("score")
    if _applied:
        _recomputed = _weighted_score(_dims)
        if _recomputed is not None:
            _score = _recomputed

    try:
        from nucleus.self_awareness.quality_score_v2 import quality_level
        _level = quality_level(_score)
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::build_entry level")
        _level = "unknown"

    # ★第158批 上-A T-唯一口径件-1：台账**只引唯一口径件的值**，不得自行重算。
    #   延迟 import 避免与 metrics_spec → capability_ledger 形成加载期循环。
    _metrics: dict[str, Any] = {}
    try:
        from nucleus.self_awareness.metrics_spec import all_metrics as _ms_all
        for _m in _ms_all():
            if _m.get("status") == "active":
                _metrics[_m["id"]] = {
                    "value": _m.get("value"),
                    "unit": _m.get("unit"),
                    "definition": _m.get("definition"),
                    "source": _m.get("source"),
                    "value_kind": _m.get("value_kind"),
                }
    except Exception as _me:
        # ★不得静默：口径件不可用需留痕（台账照常写，只是缺 metrics 段）
        silent_exc(_me, where="nucleus.self_awareness.maturity_ledger::build_entry metrics",
                   level="warning")

    return {
        "ts": time.time(),
        "batch": str(batch or ""),
        "version": LEDGER_VERSION,
        # ---- 分值 / 等级
        "score": _score,
        "level": _level,
        "score_raw": result.get("score"),
        # ---- LTS（长期序列）
        "lts": lts_snapshot(_prev, _score),
        # ---- human_override
        "human_override": _applied,
        # ---- 打分归因（★保全 formula / data_source / raw）
        "attribution": {
            _k: {
                "score": (_v.get("score") if isinstance(_v, dict) else None),
                "formula": (_v.get("formula") if isinstance(_v, dict) else None),
                "data_source": (_v.get("data_source") if isinstance(_v, dict) else None),
                "raw": (_v.get("raw") if isinstance(_v, dict) else {}),
            }
            for _k, _v in _dims.items()
        },
        # ---- 修复率 / 不可验证率（★N-9 口径）
        "rates": patch_rates(),
        # ---- 指标唯一口径件（T-唯一口径件-1：总账只引这里的值）
        "metrics_canonical": _metrics,
        # ---- 挂载面读数
        "probe_count": probe_count(),
        "available": list(result.get("available") or []),
        "reason": result.get("reason", ""),
    }


def append_entry(entry: dict[str, Any], path: str | None = None) -> str:
    """追加一条台账（JSONL，单行一条）。返回文件路径。"""
    _p = path or ledger_path()
    if not _writable(_p):
        return _p
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with io.open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        sys.stderr.write("[maturity_ledger] 台账写入失败: %s: %s\n" % (type(e).__name__, e))
    return _p


def load_ledger(path: str | None = None, limit: int = 50) -> list[dict]:
    """读取最近 ``limit`` 条台账。"""
    _p = path or ledger_path()
    if not os.path.isfile(_p):
        return []
    _out: list[dict] = []
    try:
        for _line in io.open(_p, encoding="utf-8", errors="replace"):
            _line = _line.strip()
            if not _line:
                continue
            try:
                _out.append(json.loads(_line))
            except ValueError as e:
                silent_exc(e, "nucleus.self_awareness.maturity_ledger:load_ledger 行解析失败",
                           level="warning")
                continue
    except OSError as e:
        silent_exc(e, "nucleus.self_awareness.maturity_ledger:load_ledger 读取异常",
                   level="warning")
        return []
    return _out[-limit:]


def register_daily(result: dict[str, Any] | None = None, batch: str = "",
                   path: str | None = None) -> dict[str, Any]:
    """★每批/每日登记入口（供 ``DailyScheduler.run_once`` 调用）。

    Args:
        result: ``evaluate_v2`` 结果；``None`` → 现场调用 ``evaluate_v2()`` 采集。
        batch: 批次标签。
        path: 台账路径（测试可注入 tmp 路径）。

    Returns:
        已落盘的台账条目；采集失败时返回带 ``error`` 字段的 dict。
    """
    try:
        from nucleus.self_awareness.quality_score_v2 import evaluate_v2
        _res = result if isinstance(result, dict) else evaluate_v2()
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.maturity_ledger::register_daily evaluate")
        return {"error": "%s: %s" % (type(e).__name__, e), "version": LEDGER_VERSION}
    _entry = build_entry(_res, batch=batch)
    append_entry(_entry, path)
    return _entry
