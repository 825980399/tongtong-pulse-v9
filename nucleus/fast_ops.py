from nucleus._silent_except import silent_exc

# -*- coding: utf-8 -*-
"""
fast_ops.py —— 快速操作集

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 高频调用的优化操作集合，减少性能开销
机制: 函数式模块，包含6个工具函数
定位: 性能基础设施层
"""


def _gpu_cfg(key: str, default):
    """读取 GPU_VECTOR_SEARCH 配置，任何异常回落 default（★绝不阻断检索）。"""
    try:
        import config as _cfg
        _seg = getattr(_cfg, "GPU_VECTOR_SEARCH", {})
        if isinstance(_seg, dict) and key in _seg:
            return _seg[key]
    except Exception as e:
        silent_exc(e, "nucleus/fast_ops.py:22:快速操作异常", level="warning")
    return default


# ★PHASE14：GPU 自适应的运行期状态（进程内单例）
#   设计要点：**不靠猜，靠测**。不同显卡/驱动的性能拐点差异极大
#   （GTX 1050 Ti 与 RTX 4090 的合理阈值可以差一个数量级），
#   与其把阈值写死，不如让框架自己跑出来——GPU 连续跑输 CPU 就自动停用。
_GPU_STATE = {
    "enabled": None,       # None=未判定, True/False
    "cpu_ms_avg": 0.0,     # CPU 路径历史均值（作为比较基线）
    "gpu_ms_avg": 0.0,
    "slower_streak": 0,    # GPU 连续跑输次数
    "calls": 0,
    "gpu_calls": 0,
    "last_log": 0.0,
}



def _try_gpu_search(query_vector: list, candidate_vectors: list, top_k: int):
    """★PHASE14：GPU 批量余弦 + top-k。成功返回 [(idx, score)...]，否则返回 None。

    调用前请确认：GPU 开关已开、批量已过阈值。本函数只负责「跑一次并记账」，
    不负责决策——决策在 fast_vector_search 里。
    """
    import time as _t
    _t0 = _t.perf_counter()
    try:
        from nucleus.GPUCore import get_gpu_core
        _scores = get_gpu_core().batch_cosine_gpu(query_vector, candidate_vectors)
        if not _scores or len(_scores) != len(candidate_vectors):
            return None
        _ranked = sorted(range(len(_scores)),
                         key=lambda i: _scores[i], reverse=True)[:top_k]
        _ms = (_t.perf_counter() - _t0) * 1000.0
        _GPU_STATE["gpu_calls"] += 1
        _GPU_STATE["gpu_ms_avg"] = (
            _GPU_STATE["gpu_ms_avg"] * 0.7 + _ms * 0.3)
        return [(i, float(_scores[i])) for i in _ranked], _ms
    except Exception as e:
        silent_exc(e, where="nucleus.fast_ops::_try_gpu_search L68")
        return None


def fast_vector_search(query_vector: list, candidate_vectors: list, top_k: int = 5) -> list:
    """
    高性能向量检索（★v27：接入 Cython top-k 一体化加速）。

    ★v27：优先使用 _topk_retrieve_cy（批量余弦 + C 层 qsort 降序 top-k，约 3.3x 加速）；
      失败时回退 _cosine_cpu_cy（批量余弦），再回退纯 Python 余弦（零冲突，接口一致）。

    ★PHASE14（2026-09-07）：新增 GPU 层。
      小林实测「GPU 宣告了但利用率纹丝不动」——根因是框架只有探测没有使用。
      现在大批量检索会真正走 GPU（torch 批量余弦），并做自适应：
      连续跑输 CPU 就自动停用，绝不拖慢。详见 config.GPU_VECTOR_SEARCH 注释。

    返回 [(index, score), ...] 按分数降序，最多 top_k 个。
    """
    import math
    import time as _time

    _GPU_STATE["calls"] += 1
    _n = len(candidate_vectors) if candidate_vectors else 0
    _dim = len(query_vector) if query_vector else 0

    # ── GPU 决策：问设备路由中枢（成本模型），不再用写死阈值 ──
    #   ★PHASE14 第四批：原实现用 min_candidates/min_dim 两个常数卡门槛，
    #     那是「某一张卡」的结论，换张卡就错。现在改由 device_router 用
    #     实测带宽/算力现算盈亏平衡点——显卡升级后拐点自动下移，无需改代码。
    #   门槛之上仍保留本文件的实测熔断（先验模型 + 后验实测，双保险）。
    _gpu_ok = False
    _gpu_reason = ""
    _est_gpu = _est_cpu = 0.0
    if _GPU_STATE["enabled"] is None:
        _GPU_STATE["enabled"] = bool(_gpu_cfg("enabled", True)) and _n > 0
    if (_GPU_STATE["enabled"]
            and _n <= int(_gpu_cfg("max_candidates_per_call", 200000))):
        try:
            from nucleus.device_router import get_router_state, should_use_gpu
            _gpu_ok, _gpu_reason, _est_gpu, _est_cpu = should_use_gpu(
                "vector_search", _n, _dim,
                win_ratio=float(_gpu_cfg("win_ratio", 1.15)))
            # 状态同步：路由中枢若已熔断，本文件的开关必须跟着关，
            # 否则会出现「中枢说停用、这里还在试」的两套状态打架。
            if not get_router_state().get("gpu_enabled", True):
                _GPU_STATE["enabled"] = False
                _gpu_ok = False
        except Exception as e:
            # 路由中枢不可用时，退回保守阈值（宁可不用 GPU，也不能乱用）
            silent_exc(e, where="nucleus.fast_ops::fast_vector_search L112")
            _gpu_ok = (_n >= int(_gpu_cfg("min_candidates", 2000))
                       and _dim >= int(_gpu_cfg("min_dim", 64)))
            _gpu_reason = "路由中枢不可用，退回保守阈值"

    if _gpu_ok:
        # ★PHASE14-防御：调用侧再兜一层。_try_gpu_search 内部虽有 try，
        #   但「GPU 路径」是可选加速项，绝不能让它以任何形式打断检索主流程——
        #   包括返回值结构异常导致的解包失败。这里统一吞掉并回落 CPU。
        try:
            _res = _try_gpu_search(query_vector, candidate_vectors, top_k)
        except Exception as e:
            silent_exc(e, where="nucleus.fast_ops::fast_vector_search L124")
            _res = None
        if _res is not None:
            try:
                _rows, _ms = _res
            except Exception as e:
                silent_exc(e, where="nucleus.fast_ops::fast_vector_search L129")
                _rows = None
            if _rows is None:
                _res = None
        if _res is not None:
            # ★后验校正：把实测耗时回写给设备路由中枢。
            #   成本模型给的是「先验估计」，真实硬件总有偏差（显存占用波动、
            #   驱动调度、共享 GPU 被别的进程抢）。实测回写才能让模型越跑越准，
            #   这是「运行全过程动态自判断」真正落地的地方。
            try:
                from nucleus.device_router import record_actual
                record_actual("gpu", _est_gpu, _ms,
                              slower_limit=int(_gpu_cfg(
                                  "auto_disable_after_slower", 5)))
            except Exception as e:
                silent_exc(e, where="nucleus.fast_ops::fast_vector_search L143")

            # 与 CPU 基线比较；尚无基线时先放行（下一次就有数了）
            _base = _GPU_STATE["cpu_ms_avg"]
            if _base > 0 and _ms > _base * float(_gpu_cfg("win_ratio", 1.0)):
                _GPU_STATE["slower_streak"] += 1
                if _GPU_STATE["slower_streak"] >= int(
                        _gpu_cfg("auto_disable_after_slower", 5)):
                    _GPU_STATE["enabled"] = False
                    try:
                        import logging as _lg
                        _lg.getLogger(__name__).info(
                            f"[GPU自适应] GPU 连续 {_GPU_STATE['slower_streak']} 次"
                            f"慢于 CPU（GPU {_ms:.1f}ms / CPU 基线 {_base:.1f}ms），"
                            f"已自动停用，后续一律走 CPU。若确认显卡可用，"
                            f"可调 config.GPU_VECTOR_SEARCH.min_candidates")
                    except Exception as e:
                        silent_exc(e, where="nucleus.fast_ops::fast_vector_search L160")
            else:
                _GPU_STATE["slower_streak"] = 0

            if _gpu_cfg("verbose_log", True):
                _now = _time.time()
                if _now - _GPU_STATE["last_log"] >= float(
                        _gpu_cfg("min_log_interval", 30.0)):
                    _GPU_STATE["last_log"] = _now
                    try:
                        import logging as _lg
                        _lg.getLogger(__name__).info(
                            f"[GPU向量检索] {_n}条×{_dim}维 已在GPU完成，"
                            f"耗时{_ms:.1f}ms（预估{_est_gpu:.1f}ms/CPU基线"
                            f"{_base:.1f}ms）；分派理由: {_gpu_reason}；"
                            f"累计GPU调用{_GPU_STATE['gpu_calls']}次")
                    except Exception as e:
                        silent_exc(e, where="nucleus.fast_ops::fast_vector_search L177")
            return _rows
        # GPU 失败 → 静默回落下方 CPU 路径

    # ★v27：Cython top-k 一体化（首选）
    _cpu_t0 = _time.perf_counter()

    def cosine_sim(a, b):
        dot = sum(x*y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x*x for x in a))
        norm_b = math.sqrt(sum(x*x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # try/finally 只为给 CPU 路径记一次账——作为 GPU 是否划算的比较基线。
    #   三个 return 分支都走同一套记账，不重复代码。
    try:
        # ★第70批 T4.2：FAISS 向量检索（GPU 未命中时的第一顺位 CPU 加速）。
        #   设计要点：**不是所有规模都该用 FAISS**。
        #     FAISS 需先建索引（O(N·D)），小候选集建索引的开销 > 暴力余弦本身，
        #     会**变慢**。因此只在候选数达到阈值（默认 5000，可配置）时才走 FAISS，
        #     小检索完全不受影响 —— 这是零回归的关键。
        #   失败/未安装/未达阈值 → 一律静默回退下方原有 CPU 路径。
        try:
            import config as _m70_cfg
            _faiss_on = bool(getattr(_m70_cfg, "ENABLE_FAISS_FAST_OPS", True))
            _faiss_min = int(getattr(_m70_cfg, "FAISS_FAST_OPS_MIN_CANDIDATES", 5000))
        except Exception as e:
            silent_exc(e, where="nucleus.fast_ops::fast_vector_search L206")
            _faiss_on = True
            _faiss_min = 5000
        if _faiss_on and _n >= _faiss_min and _n > 0 and _dim > 0:
            try:
                import numpy as _np70

                from nucleus.vector_store.faiss_store import get_faiss_store as _get_fs
                _store = _get_fs()
                if _store is not None and _store._faiss_available:
                    # 用临时索引做本次检索：候选集是调用方传入的即时向量，
                    # 不属于全局索引，故每次现建现用（FlatL2，精确）。
                    import faiss as _faiss70
                    _mat = _np70.asarray(candidate_vectors, dtype=_np70.float32)
                    if _mat.ndim == 2 and _mat.shape[1] == _dim:
                        _idx = _faiss70.IndexFlatL2(_dim)
                        _idx.add(_mat)
                        _qv = _np70.asarray([query_vector], dtype=_np70.float32)
                        _k = min(int(top_k), _mat.shape[0])
                        _d, _i = _idx.search(_qv, _k)
                        _rows70 = []
                        for _p in range(_i.shape[1]):
                            _ii = int(_i[0][_p])
                            if _ii < 0:
                                continue
                            # FAISS 返回 L2 距离 → 转成与余弦同向的「越大越相似」
                            _sim = 1.0 / (1.0 + float(_d[0][_p]))
                            _rows70.append((_ii, _sim))
                        if _rows70:
                            return _rows70
            except Exception as e:
                silent_exc(e, where="nucleus.fast_ops::fast_vector_search L235")

        # ★v27：Cython top-k 一体化（首选）
        try:
            from nucleus.reasoning._topk_retrieve_cy import topk_retrieve_cy
            _r = topk_retrieve_cy(list(query_vector),
                                  [list(v) for v in candidate_vectors], int(top_k))
            if _r is not None:
                return _r
        except Exception as e:
            silent_exc(e, where="nucleus.fast_ops::fast_vector_search L245")
        # ★v27：批量余弦 Cython（次选），再 Python 排序
        try:
            from nucleus.gpu._cosine_cpu_cy import batch_cosine_cy
            _scores = batch_cosine_cy(list(query_vector),
                                      [list(v) for v in candidate_vectors])
            _ranked = sorted(range(len(_scores)), key=lambda i: _scores[i], reverse=True)[:top_k]
            return [(i, _scores[i]) for i in _ranked]
        except Exception as e:
            silent_exc(e, where="nucleus.fast_ops::fast_vector_search L254")
        # 纯 Python 回退
        scores = [(i, cosine_sim(query_vector, vec)) for i, vec in enumerate(candidate_vectors)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]
    finally:
        _ms = (_time.perf_counter() - _cpu_t0) * 1000.0
        _GPU_STATE["cpu_ms_avg"] = (
            _GPU_STATE["cpu_ms_avg"] * 0.7 + _ms * 0.3)
        # 同上：CPU 侧实测也回写，让成本模型双向校准
        try:
            from nucleus.device_router import record_actual
            record_actual("cpu", _est_cpu, _ms)
        except Exception as e:
            silent_exc(e, where="nucleus.fast_ops::fast_vector_search L268")


# _m70_t4_faiss_fastops
