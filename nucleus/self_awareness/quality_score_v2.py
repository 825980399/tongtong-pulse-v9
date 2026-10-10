# -*- coding: utf-8 -*-
"""quality_score_v2.py —— 质量分 v2（★主线第51批 T2，P0-4）

背景（旧分虚高）
--------------
旧 ``SelfAwarenessEngine.compute_overall_score`` = 9 个维度**等权平均**，
而多数维度是「**有这个模块/有数据就给分**」型：

* 「有补丁系统」就给分 —— 但补丁验证此前是**空转**的（P0-2）；
* 「有 ReportBus」就给分 —— 但**消费率恒为 0**（P0-1）；
* 「代码能跑」就给分 —— 但**器官测试覆盖率 9.1%**。

⇒ 结果：质量分 **92.99**，却在同期有 6 个未修 P0。

v2 设计原则
----------
1. **只认有效性**：每个维度必须来自**可量化的运行数据**（消费率/修复率/覆盖率/问题数）；
2. **可证伪**：每个维度必须给出 ``formula`` + ``data_source`` + ``raw`` 原始值；
3. **不惩罚「不知道」**：数据不可用 → 该维度 ``score = None``，**不参与**加权（不当作 0）；
4. **不含「有模块就给分」**：无有效性数据的模块**不得计分**。

五个维度（含权重）
----------------
| 维度 | 权重 | 公式 |
|---|---|---|
| ``module_effectiveness`` | 0.30 | ``50×报告消费率 + 50×补丁真实修复率`` |
| ``issue_severity`` | 0.25 | ``max(0, 100 − (P0×10 + P1×4 + P2×1))`` |
| ``test_coverage`` | 0.20 | ``器官测试覆盖率 × 100`` |
| ``static_health`` | 0.15 | ``max(0, 100 − ruffF × 5)`` |
| ``data_integrity`` | 0.10 | ``100 × (1 − 0.5×污染率) × 标记完整度`` |

★纯只读：除趋势文件（``data/self_awareness/quality_trend.jsonl``）外不写任何数据。

核心接口
--------
* :func:`evaluate_v2` —— 计算 v2 质量分（**主入口**）
* :func:`quality_level` —— 分数 → 等级标签（critical/warning/attention/healthy）
* :func:`record_trend` / :func:`load_trend` —— 趋势记录（JSONL 追加 / 读取）
* :func:`count_open_debts` —— 统计未修复 P0/P1/P2（债务清单含 🔴 的行）
* 五个维度评分函数：:func:`score_module_effectiveness` /
  :func:`score_issue_severity` / :func:`organ_test_coverage` /
  :func:`score_static_health` / :func:`score_data_integrity`

使用示例
--------
计算并查看各维度（每个维度含 ``formula`` / ``data_source`` / ``raw``）::

    from nucleus.self_awareness import quality_score_v2 as Q
    r = Q.evaluate_v2()
    r["score"]                       # 45.77
    Q.quality_level(r["score"])      # 'warning'
    r["dimensions"]["test_coverage"]["formula"]   # '有专门测试的...× 100'
    r["dimensions"]["test_coverage"]["raw"]        # {'organs_total': 66, ...}

追加一次趋势记录（★默认**不写盘**，需显式调用）::

    Q.record_trend(r)                # 追加到 data/self_awareness/quality_trend.jsonl
    Q.load_trend(limit=10)           # 读取最近 10 条

亦可通过诊断器只读入口获取（旧健康分方法不受影响）::

    from nucleus.diagnostics import FrameworkDiagnostics
    FrameworkDiagnostics().get_quality_score_v2()
"""
from __future__ import annotations

import glob
import io
import json
import logging
import os
import re
import sys
import time
from typing import Any, Callable

from nucleus._silent_except import silent_exc
from nucleus.data.path_utils import (
    safe_relpath as _safe_relpath,  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
)

_log = logging.getLogger(__name__)

ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

QUALITY_V2_VERSION: int = 2

#: 维度权重（★按"是否反映真实有效性"排序，不再是等权）
DIMENSION_WEIGHTS_V2: dict[str, float] = {
    "module_effectiveness": 0.30,
    "issue_severity": 0.25,
    "test_coverage": 0.20,
    "static_health": 0.15,
    "data_integrity": 0.10,
    "resolution_rate": 0.0,
}

#: 扣分系数（出现在公式里，便于审计）
SEVERITY_PENALTY: dict[str, float] = {"P0": 10.0, "P1": 4.0, "P2": 1.0}
RUFF_F_PENALTY: float = 5.0
POLLUTION_WEIGHT: float = 0.5


# ==================== 工具 ====================

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def _dim(score: float | None, formula: str, source: str,
         raw: Any = None) -> dict[str, Any]:
    """构造维度结果（★可证伪：分数 + 公式 + 数据源 + 原始值）。"""
    return {
        "score": (round(_clamp(score), 2) if score is not None else None),
        "formula": formula,
        "data_source": source,
        "raw": raw if raw is not None else {},
    }


# ==================== ① 模块有效性 ====================

def _report_consumption_rate() -> float | None:
    """报告消费率。

    ★采数顺序（可证伪）：
    ① **本进程内存态** ReportBus（有报告时最准确）；
    ② 内存态为空 → 从 ``data/reports/**/*.json``（含 ``_archive_*`` 归档）
       统计 ``consumed_by`` 非空的报告占比（反映**真实历史消费**）。
    """
    try:
        from nucleus.reporting.report_bus import get_report_bus
        _st = get_report_bus(persist=False).get_stats()
        if int(_st.get("published_total", 0) or 0) > 0 \
                or int(_st.get("total", 0) or 0) > 0:
            _r = _st.get("consumption_rate")
            if isinstance(_r, (int, float)):
                return float(_r)
    except Exception as _e:
        # ★不得静默：内存态采集失败需留痕（随后走磁盘兜底）
        sys.stderr.write("[quality_v2] 内存态消费率采集失败: {}: {}\n".format(type(_e).__name__, _e))
    return _disk_consumption_rate()


def _disk_consumption_rate() -> float | None:
    """从磁盘报告文件统计消费率（含归档目录）。"""
    _base = os.path.join(ROOT, "data", "reports")
    if not os.path.isdir(_base):
        return None
    _n, _c = 0, 0
    for _dp, _dns, _fns in os.walk(_base):
        for _fn in _fns:
            if not _fn.endswith(".json"):
                continue
            try:
                _j = json.load(io.open(os.path.join(_dp, _fn),
                                       encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(_j, dict):
                continue
            _n += 1
            if _j.get("consumed_by"):
                _c += 1
    if _n == 0:
        return None
    return round(_c / float(_n), 4)


def _patch_true_fix_rate() -> float | None:
    """补丁真实修复率（★主动复现口径，见 patch_active_reprobe）。"""
    try:
        from nucleus.evolution.patch_active_reprobe import true_fix_rate
        _p = os.path.join(ROOT, "data", "patches", "patch_history.json")
        if not os.path.isfile(_p):
            return None
        _d = json.load(io.open(_p, encoding="utf-8"))
        _ps = _d.get("patches") if isinstance(_d, dict) and "patches" in _d else _d
        if not isinstance(_ps, list) or not _ps:
            return None
        return true_fix_rate(_ps)
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.quality_score_v2::_patch_true_fix_rate L181")
        return None


def _report_resolution_rate() -> float | None:
    """闭环解决率（问题解决率，★第111批 T-111b）。

    采数：本进程 ReportBus.get_stats().resolution_rate（best-effort 内存态）。
    """
    try:
        from nucleus.reporting.report_bus import get_report_bus
        _st = get_report_bus(persist=False).get_stats()
        _r = _st.get("resolution_rate")
        if isinstance(_r, (int, float)):
            return float(_r)
    except Exception as _e:
        sys.stderr.write("[quality_v2] 内存态解决率采集失败: {}: {}\n".format(type(_e).__name__, _e))
    return None


def score_module_effectiveness() -> dict[str, Any]:
    """模块有效性 = 50×报告消费率 + 50×补丁真实修复率。

    ★这是**取代**「有模块就给分」的核心维度：
    模块存在但消费率 0 / 修复率 0 → 该维度直接低分。
    """
    _cons = _report_consumption_rate()
    _fix = _patch_true_fix_rate()
    _parts, _used = 0.0, 0
    if _cons is not None:
        _parts += 50.0 * _clamp(_cons, 0.0, 1.0)
        _used += 1
    if _fix is not None:
        _parts += 50.0 * _clamp(_fix, 0.0, 1.0)
        _used += 1
    if _used == 0:
        return _dim(None, "50×报告消费率 + 50×补丁真实修复率",
                    "ReportBus(内存/磁盘) / patch_history(主动复现)",
                    {"consumption_rate": _cons, "true_fix_rate": _fix})
    # 两项都有时各占 50；只有一项时按其自身 ×100 计
    _score = _parts if _used == 2 else _parts * 2.0
    return _dim(_score, "50×报告消费率 + 50×补丁真实修复率",
                "ReportBus(内存/磁盘) / patch_history(主动复现)",
                {"consumption_rate": _cons, "true_fix_rate": _fix,
                 "parts_used": _used})


def score_resolution_rate() -> dict[str, Any]:
    """闭环健康度：问题解决率（★第111批 T-111b）。

    口径：报告异常在后续同类报告中不再越阈 → 判定「已解决」；
    解决率 = 已解决异常数 / 近窗内登记异常数（best-effort，详见 ReportBus）。
    """
    _r = _report_resolution_rate()
    if _r is None:
        return _dim(None, "100 × 问题解决率(已解决/近窗登记)",
                    "ReportBus.get_stats().resolution_rate",
                    {"resolution_rate": None})
    return _dim(round(100.0 * _clamp(_r, 0.0, 1.0), 2),
                "100 × 问题解决率(已解决/近窗登记)",
                "ReportBus.get_stats().resolution_rate",
                {"resolution_rate": _r})


# ==================== ② 问题严重度 ====================

_DEBT_DOC: str = os.path.join(
    "docs", "完整进化路线与技术债务清单_v1.0.md")
_RED: str = "\U0001F534"          # 🔴
_DEBT_RE: re.Pattern = re.compile(r"\*\*(P[012])-(\d+)\*\*")


def count_open_debts(doc_path: str | None = None) -> dict[str, int]:
    """统计**未修复**的 P0/P1/P2 问题数（唯一编号去重）。

    判据：债务清单中含 ``🔴``（待修复/未修）的表格行。

    ★Dxxx-3：内部总账（债务清单）在公开包中不存在时**降级**而非崩溃：
    内部环境路径存在→正常统计；公开环境路径缺失→记 warning 并返回空计数
    （严重度维度 score=None，不参与加权），不抛错、不静默吞。
    """
    _p = doc_path or os.path.join(ROOT, _DEBT_DOC)
    _out = {"P0": 0, "P1": 0, "P2": 0}
    if not os.path.isfile(_p):
        _log.warning("债务清单缺失 %s：问题严重度维度降级为无数据（score=None）", _p)
        return _out
    try:
        _t = io.open(_p, encoding="utf-8", errors="replace").read()
    except OSError as _e:
        _log.warning("读取债务清单失败 %s：严重度维度降级（%s）", _p, _e)
        return _out
    _sets: dict[str, set] = {"P0": set(), "P1": set(), "P2": set()}
    for _line in _t.replace("\r\n", "\n").split("\n"):
        if _RED not in _line:
            continue
        for _m in _DEBT_RE.finditer(_line):
            _sets[_m.group(1)].add(int(_m.group(2)))
    for _k in _out:
        _out[_k] = len(_sets[_k])
    return _out


def score_issue_severity() -> dict[str, Any]:
    """问题严重度 = max(0, 100 − (P0×10 + P1×4 + P2×1))。"""
    _c = count_open_debts()
    _pen = sum(SEVERITY_PENALTY[_k] * _c[_k] for _k in _c)
    return _dim(100.0 - _pen,
                "max(0, 100 − (P0×10 + P1×4 + P2×1))",
                "docs/完整进化路线与技术债务清单_v1.0.md（含 🔴 的行）",
                dict(_c, penalty=_pen))


# ==================== ③ 测试覆盖率 ====================

def organ_test_coverage() -> dict[str, Any]:
    """器官测试覆盖率 = 有**专门测试文件**的器官模块 / 器官模块总数。"""
    _organs = [f for f in glob.glob(os.path.join(ROOT, "organs", "**", "*.py"),
                                    recursive=True)
               if os.path.basename(f) != "__init__.py"]
    _tests = [os.path.basename(f).lower()
              for f in glob.glob(os.path.join(ROOT, "tests", "**", "*.py"),
                                 recursive=True)]
    if not _organs:
        return _dim(None, "有专门测试的器官模块数 / 器官模块总数 × 100",
                    "organs/ vs tests/", {})
    _covered = []
    for _o in _organs:
        _base = os.path.splitext(os.path.basename(_o))[0].lower()
        _key = _base.replace("pulse", "")
        if _key and any(_key in _t for _t in _tests):
            _covered.append(_safe_relpath(_o, ROOT).replace("\\", "/"))
    _ratio = len(_covered) / float(len(_organs))
    return _dim(_ratio * 100.0,
                "有专门测试的器官模块数 / 器官模块总数 × 100",
                "organs/**.py 与 tests/**.py 文件名匹配",
                {"organs_total": len(_organs), "covered": len(_covered),
                 "ratio": round(_ratio, 4), "covered_sample": _covered[:10]})


# ==================== ④ 静态健康 ====================

def _find_ruff() -> str | None:
    """动态定位 ruff 可执行文件（跨环境，无写死绝对路径）。

    查找顺序：
    1. ``PATH`` 上的 ``ruff``（``shutil.which``）；
    2. 当前解释器所在目录的 ``Scripts/ruff.exe``（Windows venv/系统安装）；
    3. 当前解释器所在目录的 ``bin/ruff``（POSIX venv）。
    全部未命中返回 ``None``（调用方按"数据不可用"处理，不惩罚）。
    """
    import shutil
    _ruff = shutil.which("ruff")
    if _ruff:
        return _ruff
    _bindir = os.path.dirname(os.path.abspath(sys.executable))
    for _cand in (
        os.path.join(_bindir, "Scripts", "ruff.exe"),
        os.path.join(_bindir, "Scripts", "ruff"),
        os.path.join(_bindir, "bin", "ruff"),
        os.path.join(_bindir, "ruff.exe"),
        os.path.join(_bindir, "ruff"),
    ):
        if os.path.isfile(_cand):
            return _cand
    return None


def count_ruff_f() -> int | None:
    """全库 ``ruff --select F`` 错误数（失败返回 None）。"""
    try:
        import subprocess
        _ruff = _find_ruff()
        if not _ruff or not os.path.isfile(_ruff):
            return None
        _r = subprocess.run([_ruff, "check", "--select", "F",
                             "--output-format", "concise", "."],
                            cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180)
        _lines = [x for x in (_r.stdout or "").splitlines() if ": F" in x]
        return len(_lines)
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.quality_score_v2::count_ruff_f L362")
        return None


def score_static_health(ruff_f: int | None = None) -> dict[str, Any]:
    """静态健康 = max(0, 100 − ruffF×5)。"""
    _n = count_ruff_f() if ruff_f is None else ruff_f
    if _n is None:
        return _dim(None, "max(0, 100 − ruffF×5)", "ruff --select F", {})
    return _dim(100.0 - RUFF_F_PENALTY * _n,
                "max(0, 100 − ruffF×5)", "ruff --select F",
                {"ruff_f": _n})


# ==================== ⑤ 数据完整性 ====================

_POOL: str = os.path.join("data", "experience", "experience_pool.json")


def experience_pollution() -> dict[str, Any]:
    """经验库污染率 + 清理标记完整度。"""
    _p = os.path.join(ROOT, _POOL)
    if not os.path.isfile(_p):
        return {"rate": None, "marked_ratio": None, "total": 0}
    try:
        _d = json.load(io.open(_p, encoding="utf-8"))
        _r = _d.get("experiences") if isinstance(_d, dict) else _d
        if not isinstance(_r, list) or not _r:
            return {"rate": None, "marked_ratio": None, "total": 0}
        _pol = [x for x in _r if isinstance(x, dict) and x.get("polluted")]
        _marked = [x for x in _pol if "is_cleaned" in x or "pollution_risk" in x]
        return {"rate": round(len(_pol) / float(len(_r)), 4),
                "marked_ratio": (round(len(_marked) / float(len(_pol)), 4)
                                 if _pol else 1.0),
                "total": len(_r), "polluted": len(_pol),
                "marked": len(_marked)}
    except (OSError, ValueError):
        return {"rate": None, "marked_ratio": None, "total": 0}


def score_data_integrity(pol: dict[str, Any] | None = None) -> dict[str, Any]:
    """数据完整性 = 100 × (1 − 0.5×污染率) × 标记完整度。

    * 污染率：``polluted=True`` 记录占比（已标记 ≠ 已清除，故只折半扣分）；
    * 标记完整度：已污染记录中带清理标记的比例（未标记 → ×0.5 惩罚）。
    """
    _pol = pol if pol is not None else experience_pollution()
    _rate = _pol.get("rate")
    _mk = _pol.get("marked_ratio")
    if _rate is None:
        return _dim(None, "100 × (1 − 0.5×污染率) × 标记完整度",
                    "data/experience/experience_pool.json", _pol)
    _base = 100.0 * (1.0 - POLLUTION_WEIGHT * _rate)
    _factor = 1.0 if (_mk is None or _mk >= 0.99) else 0.5
    return _dim(_base * _factor,
                "100 × (1 − 0.5×污染率) × 标记完整度",
                "data/experience/experience_pool.json",
                dict(_pol, factor=_factor))


# ==================== 汇总 ====================

#: 维度名 → 求值函数
_EVALUATORS: dict[str, Callable[[], dict[str, Any]]] = {
    "module_effectiveness": score_module_effectiveness,
    "issue_severity": score_issue_severity,
    "test_coverage": organ_test_coverage,
    "static_health": score_static_health,
    "data_integrity": score_data_integrity,
    "resolution_rate": score_resolution_rate,
}


def evaluate_v2(dimensions: dict[str, dict] | None = None) -> dict[str, Any]:
    """计算 v2 质量分。

    Args:
        dimensions: 可注入维度结果（测试用）；``None`` → 真实采集。

    Returns:
        ``{score, dimensions, weights, available, reason, version}``

    ★加权平均**只计可用维度**（``score is not None``），权重按可用项**归一化**
      —— 数据缺失不打 0 分，也不人为拉低总分。
    """
    _dims: dict[str, dict] = {}
    for _name, _fn in _EVALUATORS.items():
        if dimensions is not None and _name in dimensions:
            _dims[_name] = dimensions[_name]
        else:
            try:
                _dims[_name] = _fn()
            except Exception as _e:
                _dims[_name] = _dim(None, "(采集异常)", str(_e), {})
    _acc, _tw = 0.0, 0.0
    _used = []
    for _name, _d in _dims.items():
        _s = _d.get("score")
        if _s is None:
            continue
        _w = DIMENSION_WEIGHTS_V2.get(_name, 0.0)
        if _w <= 0:
            continue
        _acc += float(_s) * _w
        _tw += _w
        _used.append(_name)
    _score = round(_acc / _tw, 2) if _tw > 0 else None
    return {
        "score": _score,
        "dimensions": _dims,
        "weights": dict(DIMENSION_WEIGHTS_V2),
        "available": _used,
        "reason": ("" if _tw >= 0.5 else
                   "可用维度权重不足（{:.2f}），结果仅供参考".format(_tw)),
        "version": QUALITY_V2_VERSION,
    }


def quality_level(score: float | None) -> str:
    """质量分 → 等级标签（★与旧引擎同档位，便于对比）。"""
    if score is None:
        return "unknown"
    if score < 40:
        return "critical"
    if score < 70:
        return "warning"
    if score < 90:
        return "attention"
    return "healthy"


# ==================== 趋势记录 ====================

def trend_path() -> str:
    """趋势文件路径（``data/self_awareness/quality_trend.jsonl``）。

    Returns:
        趋势文件的绝对路径（JSONL，每行一条评估记录）。
    """
    return os.path.join(ROOT, "data", "self_awareness", "quality_trend.jsonl")


def record_trend(result: dict[str, Any], path: str | None = None) -> str:
    """把一次评估追加到趋势文件（JSONL）。**唯一允许的写操作**。"""
    _p = path or trend_path()
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _row = {
            "ts": time.time(),
            "score": result.get("score"),
            "level": quality_level(result.get("score")),
            "dimensions": {k: v.get("score")
                           for k, v in (result.get("dimensions") or {}).items()},
        }
        with io.open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
    except OSError as _e:
        # ★不得静默：趋势写入失败（评估本身已完成，不影响返回值）
        sys.stderr.write("[quality_v2] 趋势记录写入失败: {}: {}\n".format(type(_e).__name__, _e))
    return _p


def load_trend(path: str | None = None, limit: int = 50) -> list[dict]:
    """读取趋势记录（最近 ``limit`` 条）。"""
    _p = path or trend_path()
    if not os.path.isfile(_p):
        return []
    _out = []
    try:
        for _line in io.open(_p, encoding="utf-8", errors="replace"):
            _line = _line.strip()
            if not _line:
                continue
            try:
                _out.append(json.loads(_line))
            except ValueError as e:
                silent_exc(e, "quality_score_v2:503:日志行JSON解析失败", level="warning")
                continue
    except OSError as e:
        silent_exc(e, "quality_score_v2:505:日志文件读取异常", level="warning")
        return []
    return _out[-limit:]

# _m51_t2_cons
# _m51_t2_nopass