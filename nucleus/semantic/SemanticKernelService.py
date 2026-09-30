# -*- coding: utf-8 -*-
"""
SemanticKernelService.py —— 语义内核服务

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 语义计算内核服务，支持相似度与关联
机制: 函数式模块，包含7个工具函数
定位: 语义核心层
"""

from __future__ import annotations

import threading
from typing import Any

from nucleus.logger import get_module_logger
from nucleus._silent_except import silent_exc


_logger = get_module_logger("SemanticKernelService")

_state: dict[str, Any] = {
    "installed": False,
    "reason": "",
    "vectors": 0,
    "queue_running": False,
    "engine_injected": False,
}
_lock = threading.RLock()


def _load_config() -> dict[str, Any]:
    try:
        import config as _cfg
        _raw = getattr(_cfg, "SEMANTIC_KERNEL_CONFIG", {})
        return _raw if isinstance(_raw, dict) else {}
    except Exception:
        return {}


def install(resonance_engine=None, node_pool=None,
            start_queue: bool = True,
            auto_build_if_empty: bool = False) -> dict[str, Any]:
    """把语义内核装到框架上。

    Args:
        resonance_engine: 五维共振引擎实例（注入向量 provider）
        node_pool: 节点池（供对账扫描）
        start_queue: 是否启动异步编码队列
        auto_build_if_empty: 向量库为空时是否后台自动全量编码（默认 False，
            因为会占用 CPU 数十分钟，建议手动跑 tools/build_semantic_index.py）

    Returns:
        状态字典（不抛异常）
    """
    with _lock:
        try:
            return _install_inner(resonance_engine, node_pool,
                                  start_queue, auto_build_if_empty)
        except Exception as _e:
            _state.update({"installed": False,
                           "reason": f"{type(_e).__name__}: {_e}"})
            _logger.warning(f"[语义内核] 接入失败（框架照常运行）: {_e}")
            return dict(_state)


def _install_inner(engine, node_pool, start_queue, auto_build) -> dict[str, Any]:
    cfg = _load_config()
    if not cfg.get("enable_semantic_kernel", False):
        _state.update({
            "installed": False,
            "reason": "开关 enable_semantic_kernel=False（灰度未开启，走纯关键词）"})
        _logger.info("[语义内核] 未开启：灰度开关为 False，检索走关键词通道")
        return dict(_state)

    from nucleus.semantic.AsyncEncodeQueue import get_encode_queue
    from nucleus.semantic.VectorEncoder import get_vector_encoder
    from nucleus.semantic.VectorStore import get_vector_store

    store = get_vector_store()
    store._ensure_loaded()
    n_vec = store.status()["count"]
    _state["vectors"] = n_vec

    # ---- 1. 注入共振引擎（1.3 的向量通道入口）----
    if engine is not None:
        try:
            engine.set_vector_provider(store)
            _state["engine_injected"] = True
            _logger.info(f"[语义内核] 向量通道已接入共振引擎（库内 {n_vec} 条）")
        except Exception as _e:
            _logger.warning(f"[语义内核] provider 注入失败: {_e}")

    # ---- 2. 异步编码队列（1.4）----
    if start_queue:
        try:
            q = get_encode_queue()
            q._store = store
            if node_pool is not None:
                q.set_node_pool(node_pool)
            if q.start():
                _state["queue_running"] = True
        except Exception as _e:
            _logger.warning(f"[语义内核] 编码队列启动失败: {_e}")

    # ---- 3. 空库提示 ----
    if n_vec == 0:
        enc = get_vector_encoder()
        if not enc.is_available():
            _logger.warning(
                "[语义内核] 向量库为空且编码器不可用 —— "
                "检索全程走关键词通道。需先解决模型下载（约 90MB）")
        elif auto_build:
            _logger.info("[语义内核] 向量库为空，后台启动全量编码…")
            _start_background_build(node_pool, store)
        else:
            _logger.warning(
                "[语义内核] 向量库为空 —— 语义通道暂无数据，检索走关键词。"
                "请先构建索引：python tools/build_semantic_index.py --build")

    _state["installed"] = True
    _state["reason"] = "ok"
    return dict(_state)


def _start_background_build(node_pool, store) -> None:
    """后台全量编码（仅当 auto_build_if_empty=True）。"""
    def _run():
        try:
            from nucleus.semantic.SemanticIndexer import SemanticIndexer
            idx = SemanticIndexer(node_pool=node_pool, store=store)
            r = idx.build(batch_size=int(_load_config().get("batch_size", 32)))
            _logger.info(f"[语义内核] 后台全量编码完成: {r.get('encoded')} 条")
        except Exception as _e:
            _logger.warning(f"[语义内核] 后台全量编码失败: {_e}")

    t = threading.Thread(target=_run, name="SemanticAutoBuild", daemon=True)
    t.start()


def get_state() -> dict[str, Any]:
    """返回当前装配状态（供 status 命令 / 监控面板用）。"""
    with _lock:
        out = dict(_state)
    try:
        if out.get("installed"):
            from nucleus.semantic.VectorEncoder import get_vector_encoder
            from nucleus.semantic.VectorStore import get_vector_store
            out["store"] = get_vector_store().status()
            out["encoder"] = get_vector_encoder().status()
    except Exception as e:
        print(f"[WARNING] SemanticKernelService.py:145: {type(e).__name__}: {e}")
    return out


def shutdown() -> None:
    """框架退出时调用：停队列 + 强制落盘。"""
    try:
        from nucleus.semantic.AsyncEncodeQueue import shutdown_encode_queue
        shutdown_encode_queue()
    except Exception as e:
        silent_exc(e, where="nucleus.semantic.SemanticKernelService::shutdown L163")
    try:
        from nucleus.semantic.VectorStore import shutdown_vector_store
        shutdown_vector_store()
    except Exception as e:
        print(f"[WARNING] SemanticKernelService.py:160: {type(e).__name__}: {e}")
    _logger.info("[语义内核] 已安全退出（队列停止，向量已落盘）")
