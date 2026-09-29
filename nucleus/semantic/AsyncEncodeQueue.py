# -*- coding: utf-8 -*-
"""
AsyncEncodeQueue.py —— 异步编码队列

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 文本向量编码的异步队列处理
机制: 基于AsyncEncodeQueue类实现，包含10个核心方法
定位: 语义基础设施层
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from nucleus.logger import get_module_logger
from nucleus.semantic.VectorEncoder import get_vector_encoder
from nucleus.semantic.VectorStore import get_vector_store


_logger = get_module_logger("AsyncEncodeQueue")


def _load_config() -> dict[str, Any]:
    try:
        import config as _cfg
        _raw = getattr(_cfg, "SEMANTIC_KERNEL_CONFIG", {})
        return _raw if isinstance(_raw, dict) else {}
    except Exception:
        return {}


class AsyncEncodeQueue:
    """后台批量编码队列（单例）。"""

    _instance: AsyncEncodeQueue | None = None
    _cls_lock = threading.Lock()

    BATCH_WAIT_SEC = 0.5      # 攒批最长等待（避免低流量时一直等不满 32 条）
    RECONCILE_INTERVAL_SEC = 3600   # 对账周期（星轨 Q11：每小时）

    def __init__(self):
        self._cfg = _load_config()
        self._q: queue.Queue = queue.Queue(
            maxsize=int(self._cfg.get("async_encode_queue_size", 2000)))
        self._warn_backlog = int(self._cfg.get("async_encode_warn_backlog", 1000))
        self._batch = int(self._cfg.get("batch_size", 32))

        self._queued_ids: set[str] = set()      # 队内去重
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._reconcile_thread: threading.Thread | None = None

        self._store = None
        self._encoder = None
        self._node_pool = None                   # 对账用（可空）

        self._stats = {
            "submitted": 0, "deduplicated": 0, "rejected_full": 0,
            "encoded": 0, "failed": 0, "batches": 0,
            "last_error": "", "started_at": 0.0,
        }

    # ---------------- 单例 ----------------
    @classmethod
    def get_instance(cls) -> AsyncEncodeQueue:
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---------------- 依赖 ----------------
    def _ensure_deps(self):
        if self._store is None:
            self._store = get_vector_store()
        if self._encoder is None:
            self._encoder = get_vector_encoder()
        return self._store, self._encoder

    def set_node_pool(self, node_pool):
        """注入节点池（供对账扫描用，不注入则跳过对账）。"""
        self._node_pool = node_pool

    # ---------------- 生命周期 ----------------
    def start(self) -> bool:
        cfg = _load_config()
        if not cfg.get("enable_semantic_kernel", False):
            _logger.info("[编码队列] 语义内核未开启，队列不启动")
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker, name="AsyncEncodeWorker", daemon=True)
            self._thread.start()
            self._stats["started_at"] = time.time()
            _logger.info(
                f"[编码队列] 已启动（batch={self._batch}, "
                f"容量={self._q.maxsize}, 积压告警线={self._warn_backlog}）")

            if self._reconcile_thread is None or not self._reconcile_thread.is_alive():
                self._reconcile_thread = threading.Thread(
                    target=self._reconcile_loop, name="EncodeReconcile",
                    daemon=True)
                self._reconcile_thread.start()
        return True

    def stop(self, wait_sec: float = 5.0) -> None:
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=wait_sec)
        try:
            store, _ = self._ensure_deps()
            store.flush(force=True)
        except Exception:
            pass

    # ---------------- 入队 ----------------
    def submit(self, node_id: str, text: str) -> bool:
        """提交一条编码任务。返回是否成功入队（满/空文本返回 False）。"""
        if not node_id or not text:
            return False
        nid = str(node_id)
        with self._lock:
            if nid in self._queued_ids:
                self._stats["deduplicated"] += 1
                return True                     # 已在队列中，无需重复
            try:
                self._q.put_nowait((nid, text))
            except queue.Full:
                self._stats["rejected_full"] += 1
                _logger.warning(
                    f"[编码队列] 队列已满（{self._q.maxsize}），丢弃任务 {nid[:16]}；"
                    f"该节点将保持关键词通道")
                return False
            self._queued_ids.add(nid)
            self._stats["submitted"] += 1

        # 积压告警（星轨 Q11）
        size = self._q.qsize()
        if size > self._warn_backlog and (
                size % 500 == 0 or size >= self._q.maxsize - 1):
            _logger.warning(
                f"[编码队列] 积压 {size} 条（告警线 {self._warn_backlog}），"
                f"编码速度跟不上节点生产速度；未编码节点走关键词通道，功能不受影响")
        return True

    def pending_count(self) -> int:
        return self._q.qsize()

    # ---------------- 工作线程 ----------------
    def _worker(self) -> None:
        """攒批 → 批量编码 → 批量写库。任何异常都不让线程死掉。"""
        while not self._stop_event.is_set():
            batch: list[tuple[str, str]] = []
            try:
                first = self._q.get(timeout=1.0)
                if first is None:
                    continue
                batch.append(first)
                deadline = time.time() + self.BATCH_WAIT_SEC
                while len(batch) < self._batch:
                    remain = deadline - time.time()
                    if remain <= 0:
                        break
                    try:
                        item = self._q.get(timeout=min(remain, 0.05))
                        if item is None:
                            continue
                        batch.append(item)
                    except queue.Empty:
                        break
            except queue.Empty:
                continue
            except Exception as _e:
                _logger.debug(f"[编码队列] 取任务异常: {_e}")
                continue

            try:
                self._process_batch(batch)
            except Exception as _e:
                self._stats["failed"] += len(batch)
                self._stats["last_error"] = f"{type(_e).__name__}: {_e}"
                _logger.warning(f"[编码队列] 批次处理异常（已跳过）: {_e}")
            finally:
                with self._lock:
                    for nid, _t in batch:
                        self._queued_ids.discard(nid)

    def _process_batch(self, batch: list[tuple[str, str]]) -> None:
        store, enc = self._ensure_deps()
        if not enc.is_available():
            # 模型未就绪 → 任务留在 store 外，节点继续走关键词通道
            self._stats["last_error"] = "编码器不可用（pending → 关键词通道）"
            return

        texts = [t for _n, t in batch]
        vecs = enc.encode(texts)
        if vecs is None or len(vecs) != len(batch):
            self._stats["failed"] += len(batch)
            self._stats["last_error"] = "编码返回空（pending → 关键词通道）"
            return

        ok_n = fail_n = 0
        for (nid, text), vec in zip(batch, vecs):
            good, _why = store.put(nid, text, vec)
            if good:
                ok_n += 1
            else:
                fail_n += 1
        self._stats["encoded"] += ok_n
        self._stats["failed"] += fail_n
        self._stats["batches"] += 1

    # ---------------- 对账（星轨 Q11：每小时补编码）----------------
    def _reconcile_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.RECONCILE_INTERVAL_SEC):
                break
            try:
                if not _load_config().get("enable_semantic_kernel", False):
                    continue
                fixed = self.reconcile()
                if fixed:
                    _logger.info(f"[编码队列] 对账补编码 {fixed} 条")
            except Exception as _e:
                _logger.debug(f"[编码队列] 对账异常: {_e}")

    def reconcile(self) -> int:
        """扫描节点池，把「未编码 / 文本已变更」的节点补进队列。返回补提交条数。"""
        pool = self._node_pool
        if pool is None:
            return 0
        store, _enc = self._ensure_deps()
        added = 0
        _m102_seen = set()
        try:
            # ★Dxxx/W5：取含冷驱逐节点的全集，避免冷驱逐合法节点被 reap_orphans 误判孤儿删除
            nodes = pool.get_all_including_evicted()
        except Exception as _e:
            _logger.debug(f"[编码队列] 对账取节点失败: {_e}")
            return 0
        for nd in nodes:
            try:
                nid = getattr(nd, "node_id", "") or ""
                _m102_seen.add(nid)
                if not nid:
                    continue
                text = self._node_text(nd)
                if not text:
                    continue
                if store.needs_encode(nid, text):
                    if self.submit(nid, text):
                        added += 1
            except Exception:
                continue
        # ★第102批 T-102b：反向回收——清除「节点已不存在」的孤儿向量
        #   （原 reconcile 只单向补码，只增不减，孤儿向量只涨不降）
        _m102_reaped = 0
        try:
            import config as _cfg102
            if bool(getattr(_cfg102, 'ENABLE_M102_ORPHAN_VECTOR_REAP', True)) \
                    and _m102_seen:
                _m102_reaped = int(store.reap_orphans(_m102_seen) or 0)
                if _m102_reaped:
                    _logger.info(f"[编码队列] [第102批 T-102b] 反向回收孤儿向量 {_m102_reaped} 条")
        except Exception as _e102:
            _logger.debug(f"[编码队列] [第102批 T-102b] 孤儿向量回收异常(已忽略): "
                          f"{type(_e102).__name__}: {_e102}")
        return added

    # ---------------- 工具 ----------------
    @staticmethod
    def _node_text(node: Any) -> str:
        """从节点抽取待编码文本：value 为主，keywords 补充。

        ★范围冻结（星轨 1.8）：只向量化**知识节点**。
          临时节点（ephemeral）、空内容节点不编码。
        """
        try:
            if getattr(node, "ephemeral", False):
                return ""
            value = getattr(node, "value", "") or ""
            if isinstance(value, dict):
                value = " ".join(str(v) for v in value.values() if v)
            value = str(value).strip()
            if not value:
                return ""
            kws = getattr(node, "keywords", None) or []
            if kws:
                value = value + " " + " ".join(str(k) for k in kws)
            return value.strip()
        except Exception:
            return ""

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "pending": self.pending_count(),
            "inflight": len(self._queued_ids),
            "running": bool(self._thread and self._thread.is_alive()),
        }


def get_encode_queue() -> AsyncEncodeQueue:
    return AsyncEncodeQueue.get_instance()


def shutdown_encode_queue() -> None:
    try:
        if AsyncEncodeQueue._instance is not None:
            AsyncEncodeQueue._instance.stop()
    except Exception:
        pass
