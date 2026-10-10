# -*- coding: utf-8 -*-
"""语义缓存阈值离线校准（★主线第44批 T2 / P0-250 + P2-288）。

问题
----
``nucleus/llm/semantic_cache.py`` 的命中判定阈值 ``SEMANTIC_CACHE_THRESHOLD``
自第41批起固定为 **0.92**，但**从未用真实语料校准过**。第41批实测记录：
bge-small-zh 上「同类最小 0.7220 / 异类最大 0.6746」——若属实，0.92 意味着
**几乎永不命中**（缓存升 L2 的收益为 0，而 0.92 还是设计文档里拍的值）。

本工具做两件事（**全部只读，不碰生产缓存**）
--------------------------------------------
1. **标注探针集评估**：内置 N 组「同义改写对」与「不同话题对」，
   对每个候选阈值算 precision / recall / F1 → **推荐阈值**。
   ★缓存场景 precision 优先（误命中 = 把 A 的答案给 B，比漏命中更糟）。
2. **真实语料分布**：从 ``data/llm_traces/calls_*.jsonl`` 取去重 prompt，
   算 leave-one-out 命中率曲线 + 两两相似度分布 → 给出**真实可达命中率**。

用法
----
    python tools/calibrate_semantic_threshold.py                    # 全量
    python tools/calibrate_semantic_threshold.py --no-corpus        # 只跑探针集
    python tools/calibrate_semantic_threshold.py --report out.json

★编码器不可用（模型未就绪/开关关闭）时明确报错退出，**不伪造数据**。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 任务书点名要分析的候选阈值。
CANDIDATE_THRESHOLDS = (0.85, 0.88, 0.90, 0.92, 0.95)

#: 更宽的扫描区间（用于给出"最优区间"而非只看 5 个点）。
SWEEP_THRESHOLDS = tuple(round(0.60 + 0.02 * i, 2) for i in range(20))  # 0.60~0.98

#: ★标注探针集：同义改写对（语义相同 → **应当命中**）。
PROBE_SAME: tuple[tuple[str, str], ...] = (
    ("深度学习的原理是什么", "深度学习的基本原理是什么"),
    ("请解释一下注意力机制", "注意力机制是怎么工作的"),
    ("Python 中如何读取文件", "Python 怎么读一个文件"),
    ("什么是反向传播", "反向传播是什么意思"),
    ("请分析这段代码的问题", "帮我看看这段代码有什么问题"),
    ("如何优化数据库查询速度", "数据库查询慢怎么优化"),
    ("解释一下什么是过拟合", "过拟合是什么意思"),
    ("介绍一下Transformer架构", "Transformer 架构是怎样的"),
    ("如何配置日志轮转", "日志轮转怎么配置"),
    ("什么是语义缓存", "语义缓存是什么意思"),
)

#: ★标注探针集：不同话题对（语义不同 → **必须不命中**）。
PROBE_DIFF: tuple[tuple[str, str], ...] = (
    ("深度学习的原理是什么", "今天天气怎么样"),
    ("请解释一下注意力机制", "如何配置防火墙规则"),
    ("Python 中如何读取文件", "神经网络一共有多少层"),
    ("什么是反向传播", "帮我订一张去北京的火车票"),
    ("请分析这段代码的问题", "推荐几本好看的小说"),
    ("如何优化数据库查询速度", "晚饭吃什么比较好"),
    ("解释一下什么是过拟合", "请把这段话翻译成英文"),
    ("介绍一下Transformer架构", "我的快递到哪了"),
    ("如何配置日志轮转", "这首歌叫什么名字"),
    ("什么是语义缓存", "世界杯什么时候开始"),
)


def _cosine(a: Any, b: Any) -> float:
    """余弦相似度（零范数 → 0.0）。"""
    try:
        _dot = 0.0
        _na = 0.0
        _nb = 0.0
        for _x, _y in zip(a, b):
            _fx = float(_x)
            _fy = float(_y)
            _dot += _fx * _fy
            _na += _fx * _fx
            _nb += _fy * _fy
        if _na <= 0.0 or _nb <= 0.0:
            return 0.0
        return _dot / (math.sqrt(_na) * math.sqrt(_nb))
    except Exception as e:
        silent_exc(e, where="tools.calibrate_semantic_threshold::_cosine L94")
        return 0.0


def resolve_encoder(wait_sec: float = 120.0) -> Any:
    """取真实编码器；异步加载未就绪时**有界等待**。不可用返回 None。"""
    try:
        from nucleus.semantic.VectorEncoder import get_vector_encoder
    except Exception as e:
        silent_exc(e, where="tools.calibrate_semantic_threshold::resolve_encoder L103")
        return None
    _enc = get_vector_encoder()
    if _enc is None:
        return None
    _t0 = time.time()
    while time.time() - _t0 < wait_sec:
        try:
            _v = _enc.encode_one("就绪探测")
        except Exception as e:
            silent_exc(e, where="tools.calibrate_semantic_threshold::resolve_encoder L112")
            _v = None
        if _v is not None and len(_v) > 0:
            return _enc
        time.sleep(1.0)
    return None


def _encode(encoder: Any, texts: list[str]) -> list[Any]:
    """批量编码（逐条调用，保持与 semantic_cache 一致的单条路径）。"""
    _out = []
    for _t in texts:
        try:
            _out.append(encoder.encode_one(_t))
        except Exception:
            _out.append(None)
    return _out


def evaluate_probe(encoder: Any) -> dict[str, Any]:
    """在标注探针集上算每个阈值的 precision / recall / F1。"""
    _same = list(PROBE_SAME)
    _diff = list(PROBE_DIFF)
    _texts = []
    for _a, _b in _same + _diff:
        _texts.extend([_a, _b])
    _vecs = _encode(encoder, _texts)
    _ok = sum(1 for _v in _vecs if _v is not None)
    _n = len(_same)
    _same_sims = []
    _diff_sims = []
    for _i in range(_n):
        _same_sims.append(_cosine(_vecs[2 * _i], _vecs[2 * _i + 1]))
        _diff_sims.append(_cosine(_vecs[2 * _n + 2 * _i], _vecs[2 * _n + 2 * _i + 1]))

    _rows = []
    for _t in CANDIDATE_THRESHOLDS:
        _tp = sum(1 for _s in _same_sims if _s >= _t)
        _fn = _n - _tp
        _fp = sum(1 for _s in _diff_sims if _s >= _t)
        _tn = _n - _fp
        _prec = _tp / (_tp + _fp) if (_tp + _fp) else 0.0
        _rec = _tp / (_tp + _fn) if (_tp + _fn) else 0.0
        _f1 = (2 * _prec * _rec / (_prec + _rec)) if (_prec + _rec) else 0.0
        _rows.append({"threshold": _t, "tp": _tp, "fp": _fp, "fn": _fn,
                      "tn": _tn, "precision": round(_prec, 4),
                      "recall": round(_rec, 4), "f1": round(_f1, 4)})

    # 最优：precision >= 0.95 里 F1 最大；无解则 F1 最大者
    _safe = [r for r in _rows if r["precision"] >= 0.95]
    _best = max(_safe or _rows, key=lambda r: (r["f1"], r["precision"]))
    return {
        "encoded": _ok, "pairs_each": _n,
        "same_min": round(min(_same_sims), 4) if _same_sims else None,
        "same_max": round(max(_same_sims), 4) if _same_sims else None,
        "diff_max": round(max(_diff_sims), 4) if _diff_sims else None,
        "diff_min": round(min(_diff_sims), 4) if _diff_sims else None,
        "same_sims": [round(x, 4) for x in _same_sims],
        "diff_sims": [round(x, 4) for x in _diff_sims],
        "rows": _rows,
        "recommended": _best["threshold"],
        "recommended_row": _best,
        "safe_precision_0_95": bool(_safe),
    }


def sweep_probe(encoder: Any) -> list[dict[str, Any]]:
    """在更宽阈值区间上扫描，找 F1 最优区间。"""
    _same = list(PROBE_SAME)
    _diff = list(PROBE_DIFF)
    _texts = []
    for _a, _b in _same + _diff:
        _texts.extend([_a, _b])
    _vecs = _encode(encoder, _texts)
    _n = len(_same)
    _ss = [_cosine(_vecs[2 * _i], _vecs[2 * _i + 1]) for _i in range(_n)]
    _ds = [_cosine(_vecs[2 * _n + 2 * _i], _vecs[2 * _n + 2 * _i + 1])
           for _i in range(_n)]
    _out = []
    for _t in SWEEP_THRESHOLDS:
        _tp = sum(1 for _s in _ss if _s >= _t)
        _fp = sum(1 for _s in _ds if _s >= _t)
        _prec = _tp / (_tp + _fp) if (_tp + _fp) else 0.0
        _rec = _tp / _n if _n else 0.0
        _f1 = (2 * _prec * _rec / (_prec + _rec)) if (_prec + _rec) else 0.0
        _out.append({"threshold": _t, "precision": round(_prec, 4),
                     "recall": round(_rec, 4), "f1": round(_f1, 4)})
    return _out


def load_corpus(directory: str, limit: int = 400) -> list[str]:
    """从留存 JSONL 取去重后的 prompt（保持出现顺序，取前 ``limit`` 条）。"""
    _seen: set[str] = set()
    _out: list[str] = []
    for _fp in sorted(glob.glob(os.path.join(directory, "calls_*.jsonl"))):
        try:
            with io.open(_fp, "r", encoding="utf-8", errors="replace") as f:
                for _line in f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _rec = json.loads(_line)
                    except Exception as e:
                        silent_exc(e, "calibrate_semantic_threshold.py:213:load_corpus", level="warning")
                        continue
                    _p = str(_rec.get("prompt", "") or "").strip()
                    if not _p or _p in _seen:
                        continue
                    if len(_p) < 4:
                        continue
                    _seen.add(_p)
                    _out.append(_p)
                    if len(_out) >= limit:
                        return _out
        except Exception as e:
            silent_exc(e, "calibrate_semantic_threshold.py:224:load_corpus", level="warning")
            continue
    return _out


def corpus_stats(encoder: Any, prompts: list[str],
                 thresholds: tuple[float, ...] = CANDIDATE_THRESHOLDS,
                 max_pair_scan: int = 300) -> dict[str, Any]:
    """真实语料的 leave-one-out「最近邻相似度」分布 + 各阈值命中率。

    ★leave-one-out：每条 prompt 与**其余所有** prompt 比，取最大相似度；
    命中率 = 最近邻相似度 >= 阈值的比例（即"若缓存已装满，会不会命中"）。
    """
    if not prompts:
        return {"n": 0}
    _vecs = _encode(encoder, prompts)
    _valid = [(i, v) for i, v in enumerate(_vecs) if v is not None]
    if len(_valid) < 2:
        return {"n": len(_valid), "note": "有效向量不足"}
    _idx = [i for i, _ in _valid]
    _vs = [v for _, v in _valid]
    _n = len(_vs)
    _nn = []
    for _i in range(_n):
        _best = -2.0
        for _j in range(_n):
            if _i == _j:
                continue
            _s = _cosine(_vs[_i], _vs[_j])
            if _s > _best:
                _best = _s
        _nn.append(_best)
    _nn_sorted = sorted(_nn)
    _rows = []
    for _t in thresholds:
        _hit = sum(1 for _s in _nn if _s >= _t)
        _rows.append({"threshold": _t, "hit": _hit,
                      "hit_rate": round(_hit / _n, 4)})
    return {
        "n": _n,
        "nn_min": round(_nn_sorted[0], 4),
        "nn_p25": round(_nn_sorted[int(0.25 * (_n - 1))], 4),
        "nn_median": round(_nn_sorted[_n // 2], 4),
        "nn_p75": round(_nn_sorted[int(0.75 * (_n - 1))], 4),
        "nn_max": round(_nn_sorted[-1], 4),
        "rows": _rows,
        # 分布直方（0.1 分桶），便于文档画图
        "hist": _histogram(_nn),
    }


def _histogram(values: list[float]) -> dict[str, int]:
    """0.1 分桶直方（桶标签为下界，形如 ``"0.7"``）。"""
    _h: dict[str, int] = {}
    for _v in values:
        _k = round(math.floor(_v * 10) / 10.0, 1)
        _h[str(_k)] = _h.get(str(_k), 0) + 1
    return dict(sorted(_h.items(), key=lambda kv: float(kv[0])))


def run(corpus_dir: str | None = None, with_corpus: bool = True,
        wait_sec: float = 120.0) -> dict[str, Any]:
    """执行完整校准，返回报告 dict。``encoder`` 不可用 → 抛 RuntimeError。"""
    _enc = resolve_encoder(wait_sec=wait_sec)
    if _enc is None:
        raise RuntimeError(
            "语义编码器不可用（模型未就绪或 SEMANTIC_KERNEL_CONFIG.enable_semantic_kernel=False）"
            "—— 校准需要真实向量，拒绝伪造数据")
    _rep: dict[str, Any] = {"encoder_ready": True, "probe": evaluate_probe(_enc),
                            "sweep": sweep_probe(_enc)}
    if with_corpus and corpus_dir:
        _prompts = load_corpus(corpus_dir)
        _rep["corpus_dir"] = corpus_dir
        _rep["corpus"] = corpus_stats(_enc, _prompts, thresholds=SWEEP_THRESHOLDS)
        _rep["corpus"]["sample_prompts"] = _prompts[:5]
    return _rep


def _default_corpus_dir() -> str:
    try:
        import config
        _rel = str(getattr(config, "LLM_TRACE_DIR", "data/llm_traces"))
    except Exception:
        _rel = "data/llm_traces"
    if os.path.isabs(_rel):
        return _rel
    return os.path.join(_ROOT, _rel.replace("/", os.sep))


def main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="语义缓存阈值离线校准（第44批 T2）")
    _ap.add_argument("--corpus-dir", default=None, help="留存语料目录（默认 config.LLM_TRACE_DIR）")
    _ap.add_argument("--no-corpus", action="store_true", help="跳过真实语料扫描")
    _ap.add_argument("--report", default=None, help="报告 JSON 输出路径")
    _ap.add_argument("--wait", type=float, default=120.0, help="等待编码器就绪秒数")
    _ns = _ap.parse_args(argv)
    _cdir = _ns.corpus_dir or _default_corpus_dir()
    try:
        _rep = run(_cdir, with_corpus=not _ns.no_corpus, wait_sec=_ns.wait)
    except RuntimeError as _e:
        print("[校准] 不可用:", _e)
        return 2
    _p = _rep["probe"]
    print("[校准] 探针集 同义对=%d 异类对=%d 已编码=%d"
          % (_p["pairs_each"], _p["pairs_each"], _p["encoded"]))
    print("[校准] 同类相似度 min=%.4f max=%.4f | 异类相似度 max=%.4f min=%.4f"
          % (_p["same_min"], _p["same_max"], _p["diff_max"], _p["diff_min"]))
    print("[校准] 阈值   precision recall  f1")
    for _r in _p["rows"]:
        print("        %.2f   %.4f    %.4f   %.4f"
              % (_r["threshold"], _r["precision"], _r["recall"], _r["f1"]))
    print("[校准] ★推荐阈值 = %.2f（precision=%.4f recall=%.4f f1=%.4f）"
          % (_p["recommended"], _p["recommended_row"]["precision"],
             _p["recommended_row"]["recall"], _p["recommended_row"]["f1"]))
    if "corpus" in _rep and _rep["corpus"].get("n"):
        _c = _rep["corpus"]
        print("[校准] 真实语料 n=%d 最近邻相似度 min=%.4f 中位=%.4f max=%.4f"
              % (_c["n"], _c["nn_min"], _c["nn_median"], _c["nn_max"]))
        for _r in _c["rows"]:
            print("        阈值 %.2f → 命中率 %.4f" % (_r["threshold"], _r["hit_rate"]))
    if _ns.report:
        with io.open(_ns.report, "w", encoding="utf-8") as f:
            f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
        print("[校准] 报告 →", _ns.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
