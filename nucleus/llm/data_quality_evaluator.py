"""LLM 调用留存数据质量评估器 —— 内在模型 **L1（数据质量）** 第二个落地。

主线第43批 T1（P1-255）。定位：第40批 T1 建立了调用留存管道
（``data/llm_traces/calls_YYYYMMDD.jsonl``），但数据质量一直未知 ——
本模块为后续模型训练提供**数据质量基线**。

本批 T0 实测（``calls_20260913.jsonl``，231 条）
------------------------------------------------
* ★ **146 条（63%）是测试污染**：``prompt="hi"``、``channel=c1/c2``、``model=m1/m2``、
  ``response="模拟回复"`` 等桩值，相邻时间间隔**中位数 0.00s**（批量循环特征）。
  根因：``PulseLung._m40_trace_call`` → ``get_call_recorder()`` 用生产默认目录，
  而 pytest 中大量测试会调 ``_call_via_channels("hi", ...)``。**已在 T0 加测试环境防御**。
* 真实数据 85 条：``success`` 77 / ``failed`` 9 → **成功率 90.6%**（健康）
* ``prompt_version`` **全空**（231/231）；``failed`` 记录的 ``error`` **全空**（111/111）
* 脱敏无残留密钥 ✓

评估维度（权重合计 100）
------------------------
====================  ====  =====================================================
维度                   权重  判据
====================  ====  =====================================================
``completeness``        25  ``prompt``/``channel``/``model`` 非空率 + ``success`` 记录的``response``非空率
``uniqueness``          20  按 ``prompt`` 哈希的冗余比（1 − 冗余比）
``sanitization``        25  密钥/令牌模式残留（命中按次扣分）
``diversity``           15  渠道/模型/场景的归一化香农熵
``volume``              15  记录量 + 延迟覆盖率 + token 覆盖率
====================  ====  =====================================================

★ **``purity``（非生产记录识别）作为独立字段，不计入总分** ——
  否则 63% 的测试污染会**掩盖真实质量**。纯度按「渠道是否属于真实渠道池」
  +「渠道名结构（``c1``/``m1`` 这类短代号）」判定，并在报告中**剔除非生产记录后再评**。

L1 仅观测
---------
本模块**只读** JSONL、只写质量报告，**不改变任何留存逻辑**（开关关闭即零行为）。
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter
from typing import Any

__all__ = [
    "DEFAULT_TRACE_DIR",
    "DEFAULT_REPORT_PATH",
    "WEIGHTS",
    "SCORE_DIMENSIONS",
    "classify_record",
    "load_records",
    "evaluate_records",
    "evaluate_day",
    "save_report",
    "evaluate_and_report",
    "format_summary_line",
    "evaluator_enabled",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 留存目录（与 ``call_recorder.LLM_TRACE_DIR`` 同源）。
DEFAULT_TRACE_DIR = os.path.join(_PROJECT_ROOT, "data", "llm_traces")
#: 质量报告输出路径。
DEFAULT_REPORT_PATH = os.path.join(DEFAULT_TRACE_DIR, "quality_report.json")

#: 五个维度的权重（合计 100）。
WEIGHTS = {"completeness": 25.0, "uniqueness": 20.0, "sanitization": 25.0,
           "diversity": 15.0, "volume": 15.0}

#: 计分维度顺序（报告与测试共用）。
SCORE_DIMENSIONS = ("completeness", "uniqueness", "sanitization", "diversity", "volume")

#: 密钥/令牌残留检测（★只检测、不修改；脱敏由 call_recorder 负责）。
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{12,}"),
    re.compile(r"api[_-]?key[\"'\s:=]{1,4}[A-Za-z0-9_\-]{12,}", re.I),
    re.compile(r"[A-Za-z0-9]{32,}"),          # 长随机串（可能是密钥/哈希）
)

#: 测试桩渠道名的**结构判据**（不硬编码具体名）：``c1`` / ``m2`` 这类「单字母+1~2 位数字」。
_STUB_CHANNEL_RE = re.compile(r"^[a-zA-Z]\d{1,2}$")

#: 桩判据 C：同一 prompt 出现 >= N 次 **且** 长度 <= L 字符 → 判为测试桩。
#:   依据：实测 ``hi`` 出现 96 次、间隔中位数 0.00s（批量循环特征）。
#:   阈值可经 config 覆盖（``LLM_DATA_QUALITY_STUB_REPEAT_MIN`` / ``..._LEN_MAX``）。
_STUB_REPEAT_MIN_DEFAULT = 10
_STUB_LEN_MAX_DEFAULT = 6

#: 测试桩应答文本（命中即判为非生产）。
_STUB_RESPONSES = frozenset((
    "回答内容", "第二个渠道的回答", "第二个渠道成功", "免费兜底成功",
    "模拟回复", "成功", "mock", "ok",
))


# ------------------------------------------------------------------ 配置
def evaluator_enabled() -> bool:
    """灰度开关（默认 True，L1 仅观测）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_LLM_DATA_QUALITY_EVAL", True))
    except Exception:
        return True


def _trace_dir() -> str:
    try:
        import config
        _rel = str(getattr(config, "LLM_TRACE_DIR", "") or "")
        if _rel:
            return _rel if os.path.isabs(_rel) else os.path.join(_PROJECT_ROOT, _rel)
    except Exception:
        pass
    return DEFAULT_TRACE_DIR


def _in_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception:
        return False


def _is_production_path(path: str) -> bool:
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = os.path.abspath(_PROJECT_ROOT).replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception:
        return True


def channel_whitelist() -> set:
    """真实渠道名集合（★从配置动态推导，**不硬编码渠道名**）。"""
    _out: set = set()
    try:
        import config
        _chs = (getattr(config, "REMOTE_API_CHANNELS", {}) or {}).get("default_channels", []) or []
        for _c in _chs:
            if isinstance(_c, dict) and _c.get("name"):
                _out.add(str(_c["name"]))
        if (getattr(config, "REMOTE_API_CHANNELS", {}) or {}).get("advanced_api_key"):
            _out.add("advanced")
    except Exception:
        pass
    return _out


# ------------------------------------------------------------------ 纯度判定
def _stub_thresholds() -> tuple:
    """桩判据 C 的阈值（可经 config 覆盖）。"""
    _r = _l = None
    try:
        import config as _c
        _r = int(getattr(_c, "LLM_DATA_QUALITY_STUB_REPEAT_MIN", _STUB_REPEAT_MIN_DEFAULT))
        _l = int(getattr(_c, "LLM_DATA_QUALITY_STUB_LEN_MAX", _STUB_LEN_MAX_DEFAULT))
    except Exception:
        pass
    return (_r if isinstance(_r, int) and _r > 0 else _STUB_REPEAT_MIN_DEFAULT,
            _l if isinstance(_l, int) and _l > 0 else _STUB_LEN_MAX_DEFAULT)


def classify_record(rec: dict, whitelist: set | None = None,
                    repeat_counts: dict | None = None) -> str:
    """把一条记录分为 ``production`` / ``suspect`` / ``unknown``。

    ★判据（**结构性，尽量不硬编码具体名**）：
      A. 渠道名匹配 ``^[a-zA-Z]\\d{1,2}$``（``c1``/``m1`` 这类桩）→ ``suspect``
      B. 渠道非空且**不在真实渠道池** → ``suspect``
      C. 应答文本命中桩值集合（``模拟回复``/``第二个渠道成功``…）→ ``suspect``
      D. ★**高频短桩**：同一 ``prompt`` 出现 >= N 次**且**长度 <= L 字符 → ``suspect``
         （实测 ``hi`` 出现 96 次、间隔中位数 0.00s；阈值可配置，见 `_stub_thresholds`）
      E. 渠道与 model 均空 → ``unknown``
      F. 其余 → ``production``
    """
    if not isinstance(rec, dict):
        return "unknown"
    _wl = channel_whitelist() if whitelist is None else whitelist
    _ch = str(rec.get("channel") or "")
    _resp = str(rec.get("response") or "").strip()
    _prompt = str(rec.get("prompt") or "")
    if _ch and (_STUB_CHANNEL_RE.match(_ch) or (_wl and _ch not in _wl)):
        return "suspect"
    if _resp and _resp in _STUB_RESPONSES:
        return "suspect"
    if repeat_counts:
        _rmin, _lmax = _stub_thresholds()
        if len(_prompt) <= _lmax and int(repeat_counts.get(_prompt, 0)) >= _rmin:
            return "suspect"
    if not _ch and not str(rec.get("model") or ""):
        return "unknown"
    return "production"


# ------------------------------------------------------------------ IO
def load_records(path: str, limit: int = 0) -> list:
    """读取 JSONL 全部记录（坏行跳过，绝不抛出）。``limit>0`` 只读前 N 行。"""
    _out = []
    if not path or not os.path.isfile(path):
        return _out
    try:
        with io.open(path, encoding="utf-8", errors="replace") as _f:
            for _i, _line in enumerate(_f):
                if limit and _i >= limit:
                    break
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _r = json.loads(_line)
                except ValueError:
                    continue
                if isinstance(_r, dict):
                    _out.append(_r)
    except OSError:
        return _out
    return _out


def _latest_trace_file(trace_dir: str | None = None, day: str | None = None) -> str:
    _d = trace_dir or _trace_dir()
    _name = "calls_%s.jsonl" % (day or time.strftime("%Y%m%d"))
    return os.path.join(_d, _name)


# ------------------------------------------------------------------ 维度
def _norm_entropy(counter: Counter) -> float:
    """归一化香农熵（0~1）；样本 <2 类时为 0。"""
    _tot = sum(counter.values())
    if _tot <= 1 or len(counter) < 2:
        return 0.0
    _h = -sum((v / _tot) * math.log(v / _tot) for v in counter.values() if v > 0)
    return _h / math.log(len(counter))


def _score_completeness(recs: list) -> tuple[float, dict, list]:
    _n = len(recs)
    if not _n:
        return 0.0, {}, ["无记录，完整率无法计算"]
    _need = ("prompt", "channel", "model")
    _rates = {}
    for _k in _need:
        _rates[_k] = sum(1 for r in recs if str(r.get(_k) or "").strip()) / _n
    # ★ response 只对 success 记录计算（failed 无响应是预期行为，不应扣分）；
    #   若无 success 记录 → 该项 **N/A**，**不计入分母**（否则会把"不适用"当成"缺失"）。
    _succ = [r for r in recs if str(r.get("status")) == "success"]
    if _succ:
        _rates["response(success)"] = (
            sum(1 for r in _succ if str(r.get("response") or "").strip()) / len(_succ))
    _avg = sum(_rates.values()) / len(_rates)
    _findings = []
    for _k, _v in _rates.items():
        if _v < 0.9:
            _findings.append("字段 `%s` 非空率仅 %.1f%%" % (_k, _v * 100.0))
    _pv = sum(1 for r in recs if str(r.get("prompt_version") or "").strip()) / _n
    if _pv < 0.5:
        _findings.append("`prompt_version` 非空率仅 %.1f%%（元数据缺失，"
                         "影响按提示词版本回溯）" % (_pv * 100.0))
    return round(WEIGHTS["completeness"] * _avg, 2), _rates, _findings


def _score_uniqueness(recs: list) -> tuple[float, dict, list]:
    _n = len(recs)
    if not _n:
        return 0.0, {}, ["无记录，重复率无法计算"]
    _hashes = Counter()
    for _r in recs:
        _p = str(_r.get("prompt") or "")
        _hashes[hashlib.md5(_p.encode("utf-8")).hexdigest()] += 1
    _unique = len(_hashes)
    _redundant = _n - _unique
    _ratio = _redundant / _n
    _score = round(WEIGHTS["uniqueness"] * max(0.0, 1.0 - _ratio), 2)
    _findings = []
    if _ratio > 0.3:
        _top = _hashes.most_common(1)[0]
        _sample = next((str(r.get("prompt"))[:60] for r in recs
                        if hashlib.md5(str(r.get("prompt") or "").encode("utf-8")).hexdigest()
                        == _top[0]), "")
        _findings.append("重复率 %.1f%%（唯一 prompt %d/%d）；最高频 prompt 出现 %d 次：`%s`"
                         % (_ratio * 100.0, _unique, _n, _top[1], _sample))
    return _score, {"total": _n, "unique_prompts": _unique,
                    "redundancy": round(_ratio, 4)}, _findings


def _score_sanitization(recs: list) -> tuple[float, dict, list]:
    _n = len(recs)
    if not _n:
        # ★无样本时不得给满分（"不适用" ≠ "合格"）
        return 0.0, {"hits": 0}, []
    _hits = 0
    _by_pat: Counter = Counter()
    _samples = []
    for _r in recs:
        _blob = "%s %s %s" % (str(_r.get("prompt") or ""), str(_r.get("response") or ""),
                              str(_r.get("error") or ""))
        for _p in _SECRET_PATTERNS:
            if _p.search(_blob):
                _hits += 1
                _by_pat[_p.pattern[:26]] += 1
                if len(_samples) < 3:
                    _samples.append({"pattern": _p.pattern[:26],
                                     "trace_id": _r.get("trace_id")})
    _score = round(max(0.0, WEIGHTS["sanitization"] - 5.0 * _hits), 2)
    _findings = []
    if _hits:
        _findings.append("★脱敏遗漏：命中 %d 次密钥/令牌模式（%s）—— 应登记为债务，"
                         "由 call_recorder 的脱敏规则补强" % (_hits, dict(_by_pat)))
    return _score, {"hits": _hits, "by_pattern": dict(_by_pat),
                    "samples": _samples}, _findings


def _score_diversity(recs: list) -> tuple[float, dict, list]:
    if not recs:
        return 0.0, {}, ["无记录，多样性无法计算"]
    _ch = Counter(str(r.get("channel") or "<空>") for r in recs)
    _md = Counter(str(r.get("model") or "<空>") for r in recs)
    _og = Counter(str(r.get("origin") or "<空>") for r in recs)
    _avg = (_norm_entropy(_ch) + _norm_entropy(_md) + _norm_entropy(_og)) / 3.0
    _score = round(WEIGHTS["diversity"] * _avg, 2)
    _findings = []
    if len(_og) <= 1:
        _findings.append("场景（origin）仅 %d 类 —— 覆盖不足，"
                         "数据对多场景训练的支撑有限" % len(_og))
    return _score, {"channels": dict(_ch.most_common()),
                    "models": dict(_md.most_common()),
                    "origins": dict(_og.most_common()),
                    "norm_entropy": round(_avg, 4)}, _findings


def _score_volume(recs: list) -> tuple[float, dict, list]:
    _n = len(recs)
    if not _n:
        return 0.0, {}, ["无记录"]
    # ① 量：>=50 条得满分，线性
    _s_vol = min(1.0, _n / 50.0)
    # ② 延迟覆盖
    _dur = [r.get("duration") for r in recs if isinstance(r.get("duration"), (int, float))]
    _dur_ok = (sum(1 for x in _dur if x and x > 0) / len(_dur)) if _dur else 0.0
    # ③ token 覆盖
    _tok = [r.get("tokens") for r in recs if isinstance(r.get("tokens"), (int, float))]
    _tok_ok = (sum(1 for x in _tok if x and x > 0) / len(_tok)) if _tok else 0.0
    _avg = (_s_vol + _dur_ok + _tok_ok) / 3.0
    _score = round(WEIGHTS["volume"] * _avg, 2)
    _findings = []
    if _dur_ok < 0.5:
        _findings.append("延迟覆盖率仅 %.1f%% —— 多数记录未采集 duration" % (_dur_ok * 100.0))
    if _tok_ok < 0.5:
        _findings.append("token 覆盖率仅 %.1f%% —— 用量统计不完整" % (_tok_ok * 100.0))
    return _score, {"records": _n, "duration_coverage": round(_dur_ok, 4),
                    "token_coverage": round(_tok_ok, 4)}, _findings


# ------------------------------------------------------------------ 主评估
def evaluate_records(records: list, *, drop_suspect: bool = True) -> dict:
    """评估记录集合，返回五维评分 + 总分 + 纯度（``drop_suspect=True`` 时剔除测试污染再评）。"""
    _all = [r for r in (records if isinstance(records, (list, tuple)) else [])
            if isinstance(r, dict)]
    _wl = channel_whitelist()
    _rc: Counter = Counter(str(r.get("prompt") or "") for r in _all)
    _cls = Counter(classify_record(r, _wl, _rc) for r in _all)
    _prod = [r for r in _all if classify_record(r, _wl, _rc) == "production"]
    _sus = [r for r in _all if classify_record(r, _wl, _rc) == "suspect"]
    _scope = _prod if (drop_suspect and _prod) else _all

    _scores: dict = {}
    _details: dict = {}
    _findings: list = []
    for _name, _fn in (("completeness", _score_completeness),
                       ("uniqueness", _score_uniqueness),
                       ("sanitization", _score_sanitization),
                       ("diversity", _score_diversity),
                       ("volume", _score_volume)):
        _s, _d, _f = _fn(_scope)
        _scores[_name] = _s
        _details[_name] = _d
        _findings.extend("[%s] %s" % (_name, x) for x in _f)

    _total = round(sum(_scores.get(k, 0.0) for k in SCORE_DIMENSIONS), 2)
    _purity = {
        "total": len(_all),
        "production": len(_prod),
        "suspect": len(_sus),
        "purity": round(len(_prod) / len(_all), 4) if _all else 0.0,
        "suspect_samples": [{"trace_id": r.get("trace_id"), "channel": r.get("channel"),
                             "model": r.get("model"),
                             "prompt": str(r.get("prompt"))[:40]} for r in _sus[:5]],
    }
    if _sus:
        _findings.append("[purity] ★检测到 %d/%d（%.1f%%）非生产记录（测试污染）——"
                         "已从评分样本中剔除；根因是测试调用写生产留存目录，"
                         "本批 T0 已加测试环境防御" % (
                             len(_sus), len(_all), 100.0 * len(_sus) / max(1, len(_all))))
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "production_only" if (drop_suspect and _prod) else "all_records",
        "total_records": len(_all),
        "scored_records": len(_scope),
        "score": _total,
        "dimensions": _scores,
        "details": _details,
        "purity": _purity,
        "findings": _findings,
    }


def evaluate_day(day: str | None = None, trace_dir: str | None = None,
                 *, drop_suspect: bool = True) -> dict:
    """评估某一天的留存文件（默认今天）。"""
    _path = _latest_trace_file(trace_dir, day)
    _recs = load_records(_path)
    _rep = evaluate_records(_recs, drop_suspect=drop_suspect)
    _rep["source_file"] = _path
    _rep["file_exists"] = os.path.isfile(_path)
    return _rep


def format_summary_line(report: dict) -> str:
    """生成任务书要求的日志行。"""
    _d = report.get("dimensions", {}) or {}
    _c = (report.get("details", {}) or {}).get("completeness", {}) or {}
    _u = (report.get("details", {}) or {}).get("uniqueness", {}) or {}
    _s = (report.get("details", {}) or {}).get("sanitization", {}) or {}
    _comp = sum(_c.values()) / len(_c) if _c else 0.0
    _pred = 1.0 - sum(1 for v in _d.values() if v <= 0) / max(1, len(_d))
    return ("[数据质量] 评分=%s 完整率=%.0f%% 重复率=%.0f%% 脱敏遗漏=%s"
            "（样本=%s 纯度=%.0f%% 各维=%s）" % (
                report.get("score", 0.0), _comp * 100.0,
                (_u.get("redundancy", 0.0) or 0.0) * 100.0,
                _s.get("hits", 0),
                report.get("scored_records", 0),
                (report.get("purity", {}) or {}).get("purity", 0.0) * 100.0,
                {k: _d.get(k) for k in SCORE_DIMENSIONS}))


# ------------------------------------------------------------------ 落盘
def save_report(report: dict, path: str | None = None) -> str | None:
    """写质量报告；测试环境 + 生产路径 → 拒写（返回 None）。"""
    _p = path or DEFAULT_REPORT_PATH
    if path is None and _in_test_env() and _is_production_path(_p):
        return None
    try:
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with io.open(_p, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(report, ensure_ascii=False, indent=2))
        return _p
    except OSError:
        return None


def evaluate_and_report(day: str | None = None, trace_dir: str | None = None,
                        report_path: str | None = None, logger: Any = None,
                        *, drop_suspect: bool = True) -> dict | None:
    """读留存 → 评估 → 写报告 → 打日志。**只读**，不改变留存逻辑。"""
    if not evaluator_enabled():
        return None
    _rep = evaluate_day(day, trace_dir, drop_suspect=drop_suspect)
    _rep["report_path"] = save_report(_rep, report_path)
    if logger is not None:
        try:
            logger.info(format_summary_line(_rep))
            for _f in _rep.get("findings", []):
                logger.info("[数据质量] %s", _f)
        except Exception as _e:
            print("[数据质量] 日志输出失败: %s: %s" % (type(_e).__name__, _e), file=sys.stderr)
    return _rep
