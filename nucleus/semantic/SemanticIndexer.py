# -*- coding: utf-8 -*-
"""
SemanticIndexer.py —— 语义索引器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 知识节点的语义索引构建与管理
机制: 基于SemanticIndexer类实现，包含7个核心方法
定位: 语义检索层
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from nucleus.logger import get_module_logger
from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
from nucleus.semantic.VectorEncoder import get_vector_encoder
from nucleus.semantic.VectorStore import get_vector_store
from nucleus.data.DataAccessLayer import safe_read_json


_logger = get_module_logger("SemanticIndexer")

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROGRESS_FILE = os.path.join(_PROJECT_ROOT, "data/knowledge/index_progress.json")


class SemanticIndexer:
    """全量语义索引构建器。"""

    def __init__(self, node_pool=None, store=None, encoder=None,
                 queue: AsyncEncodeQueue | None = None):
        self._pool = node_pool
        self._store = store or get_vector_store()
        self._encoder = encoder or get_vector_encoder()
        self._queue = queue
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """请求取消（协作式，下一批开始前退出）。"""
        self._cancel.set()

    # ---------------------------------------------------------
    def collect_targets(self, include_evicted: bool = True
                        ) -> tuple[list[tuple[str, str]], dict[str, int]]:
        """收集待编码目标。返回 ([(node_id, text), ...], 统计)。

        include_evicted=True 时调 get_all_including_evicted()（含冷存兜底，
        会触发磁盘 IO），否则调 get_all()（纯内存）。
        """
        if self._pool is None:
            return [], {"error": "未注入节点池"}
        try:
            nodes = (self._pool.get_all_including_evicted() if include_evicted
                     else self._pool.get_all())
        except Exception as _e:
            _logger.warning(f"[索引构建] 取节点失败: {_e}")
            return [], {"error": str(_e)}

        targets: list[tuple[str, str]] = []
        stat = {"total_nodes": len(nodes), "skipped_ephemeral": 0,
                "skipped_empty": 0, "already_encoded": 0, "to_encode": 0}

        for nd in nodes:
            nid = getattr(nd, "node_id", "") or ""
            if not nid:
                continue
            if getattr(nd, "ephemeral", False):
                stat["skipped_ephemeral"] += 1
                continue
            text = AsyncEncodeQueue._node_text(nd)
            if not text:
                stat["skipped_empty"] += 1
                continue
            if self._store.needs_encode(nid, text):
                targets.append((nid, text))
                stat["to_encode"] += 1
            else:
                stat["already_encoded"] += 1
        return targets, stat

    # ---------------------------------------------------------
    def build(self, batch_size: int = 32, watchdog_sec: float = 300.0,
              include_evicted: bool = True,
              progress_cb: Callable[[dict], None] | None = None,
              dry_run: bool = False) -> dict[str, Any]:
        """构建全量索引。

        Args:
            batch_size: 编码批次大小（内部协作者要求 32-64）
            watchdog_sec: 看门狗心跳间隔（默认 5 分钟）
            include_evicted: 是否含冷存节点（首次全量建议 True）
            progress_cb: 进度回调
            dry_run: 只统计不编码（用于预检）

        Returns:
            统计字典
        """
        t_start = time.time()
        result: dict[str, Any] = {
            "ok": False, "dry_run": dry_run,
            "encoded": 0, "failed": 0, "batches": 0,
            "elapsed_sec": 0.0, "cancelled": False, "reason": "",
        }

        if dry_run:
            targets, stat = self.collect_targets(include_evicted)
            result.update(stat)
            result["ok"] = True
            result["elapsed_sec"] = round(time.time() - t_start, 2)
            return result

        if not self._encoder.is_available():
            result["reason"] = (
                "编码器不可用（模型未下载 / 开关未开）。"
                "框架照常运行，检索走关键词通道。")
            _logger.warning(f"[索引构建] {result['reason']}")
            return result

        targets, stat = self.collect_targets(include_evicted)
        result.update(stat)
        total = len(targets)
        if total == 0:
            result["ok"] = True
            result["reason"] = "无待编码节点（可能已全部编码完成）"
            result["elapsed_sec"] = round(time.time() - t_start, 2)
            _logger.info(f"[索引构建] {result['reason']}")
            return result

        _logger.info(
            f"[索引构建] 开始：待编码 {total} / 总节点 {stat.get('total_nodes', 0)}"
            f"（已编码 {stat.get('already_encoded', 0)}，"
            f"跳过 ephemeral {stat.get('skipped_ephemeral', 0)}，"
            f"空内容 {stat.get('skipped_empty', 0)}）")

        # ---- 看门狗：区分「慢」和「卡死」----
        progress_state = {"done": 0, "total": total, "last_ts": time.time()}
        stop_wd = threading.Event()

        def _watchdog():
            while not stop_wd.wait(10.0):
                idle = time.time() - progress_state["last_ts"]
                if idle >= watchdog_sec:
                    _logger.warning(
                        f"[索引构建] 看门狗：已 {idle/60:.1f} 分钟无进展 —— "
                        f"进程存活但可能卡住（当前 {progress_state['done']}/"
                        f"{progress_state['total']}）。若持续无进展可 Ctrl+C "
                        f"中断，已编码部分会落盘，重跑自动续跑。")
                    progress_state["last_ts"] = time.time()   # 重置，避免刷屏

        wd = threading.Thread(target=_watchdog, name="IndexWatchdog", daemon=True)
        wd.start()

        # ---- 分批编码 ----
        next_report = max(1, int(total * 0.1))
        try:
            for i in range(0, total, batch_size):
                if self._cancel.is_set():
                    result["cancelled"] = True
                    result["reason"] = "收到取消请求"
                    _logger.warning("[索引构建] 已取消，正在保存已编码部分…")
                    break

                chunk = targets[i:i + batch_size]
                texts = [t for _n, t in chunk]
                vecs = self._encoder.encode(texts)
                if vecs is None or len(vecs) != len(chunk):
                    result["failed"] += len(chunk)
                    _logger.warning(
                        f"[索引构建] 批次 {result['batches']} 编码失败，"
                        f"跳过（这些节点保持关键词通道）")
                    progress_state["done"] += len(chunk)
                    progress_state["last_ts"] = time.time()
                    continue

                ok_n, fail_n = 0, 0
                for (nid, text), vec in zip(chunk, vecs):
                    good, _why = self._store.put(nid, text, vec)
                    ok_n, fail_n = (ok_n + 1, fail_n) if good else (ok_n, fail_n + 1)
                result["encoded"] += ok_n
                result["failed"] += fail_n
                result["batches"] += 1

                progress_state["done"] += len(chunk)
                progress_state["last_ts"] = time.time()

                done = progress_state["done"]
                if done >= next_report or done >= total:
                    pct = done / total * 100
                    el = time.time() - t_start
                    speed = done / el if el > 0 else 0
                    eta = (total - done) / speed if speed > 0 else 0
                    _logger.info(
                        f"[索引构建] 进度 {done}/{total} ({pct:.0f}%) | "
                        f"{speed:.1f} 条/秒 | 预计剩余 {eta/60:.1f} 分钟")
                    next_report += max(1, int(total * 0.1))
                    self._write_progress(done, total, speed)
                    if progress_cb:
                        try:
                            progress_cb({"done": done, "total": total,
                                         "pct": pct, "speed": speed, "eta_sec": eta})
                        except Exception:
                            pass

            result["ok"] = not result["cancelled"]
        except KeyboardInterrupt:
            result["cancelled"] = True
            result["reason"] = "用户中断（Ctrl+C）"
            _logger.warning("[索引构建] 收到中断信号，正在保存已编码部分…")
        except Exception as _e:
            result["reason"] = f"{type(_e).__name__}: {_e}"
            result["ok"] = False
            _logger.warning(f"[索引构建] 异常终止: {_e}")
        finally:
            stop_wd.set()
            # ★中断也要落盘 —— 这就是断点续跑的基础
            try:
                self._store.flush(force=True)
            except Exception as _e:
                _logger.warning(f"[索引构建] 收尾落盘失败: {_e}")
            result["elapsed_sec"] = round(time.time() - t_start, 2)
            self._write_progress(progress_state["done"], total,
                                 progress_state["done"] / max(0.001, time.time() - t_start),
                                 done_flag=result["ok"])

        st = self._store.status()
        _logger.info(
            f"[索引构建] 完成：新增编码 {result['encoded']}，失败 {result['failed']}，"
            f"耗时 {result['elapsed_sec']:.1f}s | 向量库现有 {st['count']} 条")
        return result

    # ---------------------------------------------------------
    @staticmethod
    def _write_progress(done: int, total: int, speed: float,
                        done_flag: bool = False) -> None:
        try:
            os.makedirs(os.path.dirname(_PROGRESS_FILE), exist_ok=True)
            data = {
                "done": done, "total": total, "speed": speed,
                "updated_at": time.time(), "finished": done_flag,
            }
            tmp = _PROGRESS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, _PROGRESS_FILE)
        except Exception:
            pass

    @staticmethod
    def read_progress() -> dict[str, Any]:
        try:
            return safe_read_json(_PROGRESS_FILE, default={})
        except Exception as e:
            print(f"[WARNING] SemanticIndexer.py:254: {type(e).__name__}: {e}")
            return {}
