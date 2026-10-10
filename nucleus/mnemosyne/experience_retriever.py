"""经验语义检索器 —— 内在模型 **L2** 第一个落地（主线第42批 T3 / P1-265）。

现状（实测）
------------
``ExperiencePool.query_experiences()`` 只做**元数据过滤 + 时间倒序**，既不支持语义
检索，也不排除污染经验。库内 1501 条中有 **1193 条（79.5%）** 标记
``polluted=True``，且 ``pollution_reason`` **全部为「内容重复(历史灌水)」** ——
它们的 ``summary`` 高度模板化（"我曾因维持系统平衡而行动…"），
按时间倒序取前 N 条时**极易被同质文本占满**。

本模块提供
----------
1. **语义检索** —— 复用 :class:`nucleus.semantic.VectorEncoder`
   （bge-small-zh-v1.5 / 512 维；``encode()`` 批量编码 + ``cosine_matrix()`` + ``topk()``）
2. **污染过滤** —— 默认排除 ``polluted=True``（可配置）
3. **质量排序** —— 相似度 × 质量权重（``quality_weight``）× 激活度加成
4. **污染分析** —— :func:`analyze_pollution` 产出 ``data/experience/pollution_report.json``
5. **L1 仅观测** —— :meth:`ExperienceRetriever.observe` 只记录
   「新检索器 vs 现有检索器」的条数与重叠，**不替换** ``query_experiences()``，
   不改变任何既有行为（关闭开关后与改造前完全一致）。

性能
----
编码是瓶颈（单条 ~10-30ms）。本模块只对**有界候选集**编码（``scan_limit``，默认 200），
且向量**按 id 缓存**（重复调用不重复编码）。生产观测走 :meth:`observe_daily`
（挂在每日任务上，1 次/天），**不进入任何热路径**。
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
    "DEFAULT_POLLUTION_REPORT_PATH",
    "DEFAULT_SCAN_LIMIT",
    "ExperienceRetriever",
    "analyze_pollution",
    "get_retriever",
    "reset_retriever",
    "retriever_enabled",
    "save_pollution_report",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 污染分析报告路径。
DEFAULT_POLLUTION_REPORT_PATH = os.path.join(_PROJECT_ROOT, "data", "experience",
                                             "pollution_report.json")
#: 单次检索最多编码的候选条数（防热路径/离线脚本耗时失控）。
DEFAULT_SCAN_LIMIT = 200


# ------------------------------------------------------------------ 配置
def retriever_enabled() -> bool:
    """L1 观测开关（默认 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_EXPERIENCE_RETRIEVER_OBSERVE", True))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.experience_retriever::retriever_enabled L64")
        return True


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _in_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.experience_retriever::_in_test_env L79")
        return False


def _is_production_data_path(path: str) -> bool:
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = os.path.abspath(_PROJECT_ROOT).replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception as e:
        silent_exc(e, where="nucleus.mnemosyne.experience_retriever::_is_production_data_path L88")
        return True


# ------------------------------------------------------------------ 文本
def experience_text(exp: dict) -> str:
    """把一条经验压成检索文本（``motivation`` 优先，``summary`` 补充）。"""
    if not isinstance(exp, dict):
        return ""
    _m = str(exp.get("motivation") or "").strip()
    _s = str(exp.get("summary") or "").strip()
    if _m and _s:
        return "%s。%s" % (_m, _s)
    return _m or _s


def _quality_weight(exp: dict) -> float:
    _v = exp.get("quality_weight")
    if isinstance(_v, bool) or not isinstance(_v, (int, float)):
        return 1.0
    return max(0.0, min(1.0, float(_v)))


def _activation_bonus(exp: dict) -> float:
    _n = exp.get("activation_count")
    if isinstance(_n, bool) or not isinstance(_n, (int, float)):
        return 0.0
    return min(1.0, float(_n) / 10.0)


# ------------------------------------------------------------------ 检索器
class ExperienceRetriever:
    """语义经验检索（只读 + L1 观测）。"""

    def __init__(self, pool: Any = None, encoder: Any = None,
                 top_k: int | None = None, filter_polluted: bool | None = None):
        self._pool = pool
        self._encoder = encoder
        self._top_k = int(top_k) if top_k else int(_cfg("EXPERIENCE_RETRIEVER_TOP_K", 5) or 5)
        self._filter_polluted = (bool(filter_polluted) if filter_polluted is not None
                                 else bool(_cfg("EXPERIENCE_RETRIEVER_FILTER_POLLUTED", True)))
        self._vec_cache: dict = {}          # id → np.ndarray
        self._cache_sig: tuple = ()
        self._obs_count = 0
        self._last_observe: dict = {}

    # -------------------------------------------------- 依赖
    def pool(self) -> Any:
        if self._pool is None:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            self._pool = get_experience_pool()
        return self._pool

    def encoder(self) -> Any:
        if self._encoder is None:
            from nucleus.semantic.VectorEncoder import get_vector_encoder
            self._encoder = get_vector_encoder()
        return self._encoder

    def available(self) -> bool:
        """编码器是否就绪（未就绪时 :meth:`retrieve` 返回空列表）。"""
        try:
            return bool(self.encoder().is_available())
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.experience_retriever::available L151")
            return False

    def top_k(self) -> int:
        return max(1, self._top_k)

    def filter_polluted(self) -> bool:
        return bool(self._filter_polluted)

    # -------------------------------------------------- 候选集
    def _all_experiences(self) -> list:
        try:
            _p = self.pool()
            _raw = getattr(_p, "_experiences", None)
            if isinstance(_raw, list):
                return _raw
            _q = getattr(_p, "query_experiences", None)
            if callable(_q):
                return list(_q(limit=100000) or [])
            return []
        except Exception:
            return []

    def candidates(self, include_polluted: bool | None = None,
                   limit: int | None = None) -> list:
        """候选经验（默认排除污染；按 timestamp 倒序；最多 ``limit`` 条）。

        ★ 仅取最近 ``limit`` 条是为了给编码耗时设上界（见模块 docstring「性能」）。
        """
        _inc = (not self._filter_polluted) if include_polluted is None else bool(include_polluted)
        _lim = int(limit) if limit else int(_cfg("EXPERIENCE_RETRIEVER_SCAN_LIMIT",
                                                 DEFAULT_SCAN_LIMIT) or DEFAULT_SCAN_LIMIT)
        _out = []
        for _e in self._all_experiences():
            if not isinstance(_e, dict):
                continue
            if not _inc and _e.get("polluted"):
                continue
            if not experience_text(_e):
                continue
            _out.append(_e)
        _out.sort(key=lambda e: e.get("timestamp", 0) or 0, reverse=True)
        return _out[:_lim] if _lim > 0 else _out

    # -------------------------------------------------- 检索
    def _ensure_vectors(self, exps: list):
        """按 id 缓存向量；候选集不变时复用（★避免重复编码）。"""
        import numpy as _np
        _sig = tuple(str(e.get("id")) for e in exps)
        if _sig == self._cache_sig and self._vec_cache:
            return _np.array([self._vec_cache[str(e.get("id"))] for e in exps])
        _texts = [experience_text(e) for e in exps]
        _mat = self.encoder().encode(_texts)
        if _mat is None:
            return None
        _arr = _np.asarray(_mat)
        self._vec_cache = {}
        for _i, _e in enumerate(exps):
            if _i < len(_arr):
                self._vec_cache[str(_e.get("id"))] = _arr[_i]
        self._cache_sig = _sig
        return _arr

    def _score(self, sims, exps: list) -> list:
        """相似度 × 质量权重 + 激活度加成 → 排序后的 (下标, 综合分, 相似度)。"""
        _rows = []
        for _i, _e in enumerate(exps):
            if _i >= len(sims):
                break
            _s = float(sims[_i])
            _final = _s * (0.7 + 0.3 * _quality_weight(_e)) + 0.05 * _activation_bonus(_e)
            _rows.append((_i, _final, _s))
        _rows.sort(key=lambda x: x[1], reverse=True)
        return _rows

    def retrieve(self, query: str, top_k: int | None = None,
                 filter_polluted: bool | None = None, limit: int | None = None) -> list:
        """语义检索 top-k。编码器不可用 / 无候选 → 空列表（绝不抛出）。"""
        if not query or not isinstance(query, str):
            return []
        try:
            if not self.available():
                return []
            from nucleus.semantic.VectorEncoder import VectorEncoder as _VE
            _inc = None
            if filter_polluted is not None:
                _inc = not bool(filter_polluted)
            _cand = self.candidates(include_polluted=_inc, limit=limit)
            if not _cand:
                return []
            _mat = self._ensure_vectors(_cand)
            if _mat is None or len(_mat) == 0:
                return []
            _qv = self.encoder().encode_one(query)
            if _qv is None:
                return []
            _sims = _VE.cosine_matrix(_qv, _mat)
            _rows = self._score(_sims, _cand)
            _k = int(top_k) if top_k else self.top_k()
            _out = []
            for _i, _final, _sim in _rows[:_k]:
                _e = dict(_cand[_i])
                _e["_similarity"] = round(_sim, 4)
                _e["_score"] = round(_final, 4)
                _out.append(_e)
            return _out
        except Exception as _e:                      # 检索失败不得影响调用方
            print("[经验检索] 检索异常: %s: %s" % (type(_e).__name__, _e), file=sys.stderr)
            return []

    # -------------------------------------------------- 对比（L1 观测核心）
    def baseline_retrieve(self, query: str, limit: int | None = None) -> list:
        """**现有**检索器口径的结果（元数据过滤 + 时间倒序，含污染）。

        ★ 仅用于对比观测，不替代 ``ExperiencePool.query_experiences()``。
        """
        _lim = int(limit) if limit else self.top_k()
        try:
            _q = getattr(self.pool(), "query_experiences", None)
            if callable(_q):
                return list(_q(limit=_lim) or [])
        except Exception as _e:
            print("[经验检索] 现有检索器调用失败: %s: %s" % (type(_e).__name__, _e),
                  file=sys.stderr)
        return []

    def compare(self, query: str, top_k: int | None = None) -> dict:
        """对比新检索器与现有检索器（条数 / 重叠 / 污染占比）。"""
        _k = int(top_k) if top_k else self.top_k()
        _new = self.retrieve(query, top_k=_k)
        _old = self.baseline_retrieve(query, limit=_k)
        _new_ids = {str(e.get("id")) for e in _new}
        _old_ids = {str(e.get("id")) for e in _old}
        _old_polluted = sum(1 for e in _old if e.get("polluted"))
        return {
            "query": query,
            "new_count": len(_new),
            "old_count": len(_old),
            "overlap": len(_new_ids & _old_ids),
            "new_polluted": sum(1 for e in _new if e.get("polluted")),
            "old_polluted": _old_polluted,
            "old_polluted_ratio": round(_old_polluted / len(_old), 4) if _old else 0.0,
            "new_ids": sorted(_new_ids),
            "old_ids": sorted(_old_ids),
        }

    def observe(self, query: str, top_k: int | None = None, logger: Any = None) -> dict | None:
        """L1 旁路观测：记录对比结果，**不返回检索结果、不改变任何行为**。"""
        if not retriever_enabled():
            return None
        _cmp = self.compare(query, top_k=top_k)
        self._obs_count += 1
        self._last_observe = _cmp
        _line = ("[经验检索] 新检索器返回%d条，现有返回%d条，重叠%d条"
                 "（现有污染%d条/%.1f%%）"
                 % (_cmp["new_count"], _cmp["old_count"], _cmp["overlap"],
                    _cmp["old_polluted"], _cmp["old_polluted_ratio"] * 100.0))
        if logger is not None:
            try:
                logger.info(_line)
            except Exception as _e:
                print("[经验检索] 日志失败: %s: %s" % (type(_e).__name__, _e), file=sys.stderr)
        return _cmp

    def observe_daily(self, sample: int = 3, logger: Any = None) -> dict:
        """每日观测（低频）：用库内最近干净经验的文本做 N 次对比，返回汇总。

        ★ 挂在每日任务上（1 次/天），**不进入任何热路径**。
        """
        if not retriever_enabled():
            return {"status": "disabled"}
        if not self.available():
            return {"status": "encoder_unavailable"}
        _cand = self.candidates(include_polluted=False)
        if not _cand:
            return {"status": "no_candidates"}
        _queries = [experience_text(e) for e in _cand[:max(1, int(sample))]]
        _rows = []
        for _q in _queries:
            _c = self.observe(_q, logger=logger)
            if _c:
                _rows.append(_c)
        _sum = {
            "status": "ok",
            "samples": len(_rows),
            "avg_new": round(sum(r["new_count"] for r in _rows) / len(_rows), 2) if _rows else 0,
            "avg_old": round(sum(r["old_count"] for r in _rows) / len(_rows), 2) if _rows else 0,
            "avg_overlap": round(sum(r["overlap"] for r in _rows) / len(_rows), 2) if _rows else 0,
            "old_polluted_ratio_avg": round(
                sum(r["old_polluted_ratio"] for r in _rows) / len(_rows), 4) if _rows else 0.0,
        }
        if logger is not None:
            try:
                logger.info("[经验检索] 每日观测: %d 个样本 / 新均%s条 / 旧均%s条 / 重叠均%s条"
                            "（旧结果污染率均 %.1f%%）"
                            % (_sum["samples"], _sum["avg_new"], _sum["avg_old"],
                               _sum["avg_overlap"], _sum["old_polluted_ratio_avg"] * 100.0))
            except Exception as _e:
                print("[经验检索] 日志失败: %s: %s" % (type(_e).__name__, _e), file=sys.stderr)
        return _sum

    def stats(self) -> dict:
        return {
            "enabled": retriever_enabled(),
            "encoder_available": self.available(),
            "top_k": self.top_k(),
            "filter_polluted": self.filter_polluted(),
            "cached_vectors": len(self._vec_cache),
            "observations": self._obs_count,
            "last_observe": self._last_observe,
        }


# ------------------------------------------------------------------ 单例
_retriever: ExperienceRetriever | None = None


def get_retriever() -> ExperienceRetriever:
    """获取进程内单例（可注入替身用于测试）。"""
    global _retriever
    if _retriever is None:
        _retriever = ExperienceRetriever()
    return _retriever


def reset_retriever() -> None:
    """重置单例（测试用）。"""
    global _retriever
    _retriever = None


# ------------------------------------------------------------------ 污染分析
def analyze_pollution(pool: Any = None) -> dict:
    """统计经验库污染分布（只读）。"""
    try:
        if pool is None:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            pool = get_experience_pool()
        _raw = getattr(pool, "_experiences", None)
        if not isinstance(_raw, list):
            _q = getattr(pool, "query_experiences", None)
            _raw = list(_q(limit=100000) or []) if callable(_q) else []
    except Exception as _e:
        return {"status": "error", "error": "%s: %s" % (type(_e).__name__, _e), "total": 0}

    from collections import Counter
    _total = len(_raw)
    _polluted = [e for e in _raw if isinstance(e, dict) and e.get("polluted")]
    _reasons = Counter(str(e.get("pollution_reason")) for e in _polluted)
    _flags = Counter(str(e.get("quality_flag")) for e in _raw if isinstance(e, dict))
    _summ = Counter(str(bool(e.get("is_summarized"))) for e in _raw if isinstance(e, dict))
    _with_text = sum(1 for e in _raw if isinstance(e, dict) and experience_text(e))
    _rep: dict = {
        "status": "ok",
        "total": _total,
        "polluted": len(_polluted),
        "clean": _total - len(_polluted),
        "pollution_rate": round(len(_polluted) / _total, 4) if _total else 0.0,
        "by_reason": dict(_reasons.most_common()),
        "by_quality_flag": dict(_flags.most_common()),
        "is_summarized": dict(_summ),
        "text_coverage": round(_with_text / _total, 4) if _total else 0.0,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _rep["findings"] = []
    if _rep["pollution_rate"] > 0.5:
        _rep["findings"].append(
            "污染率 %.1f%%（%d/%d）—— 现有检索器按时间倒序取前 N 条时，"
            "结果极易被同质模板文本占满。" % (_rep["pollution_rate"] * 100.0,
                                              _rep["polluted"], _rep["total"]))
    if _reasons:
        _rep["findings"].append("污染原因分布：%s"
                                % "、".join("%s×%d" % (k, v) for k, v in _reasons.most_common(3)))
    return _rep


def save_pollution_report(report: dict, path: str | None = None) -> str | None:
    """写污染分析报告；测试环境 + 生产路径 → 拒写（返回 None）。"""
    _p = path or DEFAULT_POLLUTION_REPORT_PATH
    if path is None and _in_test_env() and _is_production_data_path(_p):
        return None
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with io.open(_p, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(report, ensure_ascii=False, indent=2))
        return _p
    except OSError as e:
        silent_exc(e, "experience_retriever:438:报告写出异常", level="warning")
        return None
