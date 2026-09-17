"""语义缓存器 v0 —— 内在模型 L1 层第一个落地（主线第41批 T3 / P0-250）。

定位
----
相同/高度相似的用户问题反复调用大模型 → 浪费额度 + 增加延迟。
本模块复用框架**已有**的 ``bge-small-zh-v1.5``（`nucleus/semantic/VectorEncoder`，
90MB ONNX、512 维、已 L2 归一化）做语义相似度匹配，为后续「命中即返回」积累真实命中率数据。

★L1 观测级红线（**本批只观测，不改变任何对话输出**）
---------------------------------------------------
* :meth:`observe_async` 只把 (prompt, response) 投递到**后台队列**，由 daemon 线程
  异步编码 / 比对 / 落盘 —— 主推理流程**零阻塞**。
* :meth:`lookup` 返回的是**命中统计**（相似度 / 命中次数），**绝不返回缓存内容**；
  调用方无从据此替换回复 → 结构性保证"不改变输出"。
* 关闭开关（``ENABLE_SEMANTIC_CACHE_OBSERVE=False``）→ 不启线程、不落盘、零 IO。

存储
----
``<SEMANTIC_CACHE_DIR>/semantic_cache.jsonl``，每行一条 JSON：
``{hash, prompt, response, ts, hit_count, vec}``（vec = 512 个 float32）。
* 容量上限 ``SEMANTIC_CACHE_CAPACITY``（默认 10000）→ 超限按 **LRU**（``ts`` 最旧优先）淘汰；
* TTL ``SEMANTIC_CACHE_TTL_DAYS``（默认 7）→ 过期条目在加载/写入时清理；
* 单条超长文本按 ``SEMANTIC_CACHE_MAX_TEXT``（默认 4000 字符）截断，控制文件体积。

★依赖隔离：编码器通过 ``encoder`` 参数可注入（测试传假件，**不加载真模型**）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 0.85          # ★第45批 T1：按第44批实测校准（原 0.92）
DEFAULT_CAPACITY = 10000
DEFAULT_TTL_DAYS = 7
DEFAULT_MAX_TEXT = 4000
_HASH_LEN = 16


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:_HASH_LEN]


@dataclass
class CacheEntry:
    """缓存条目（含向量，供余弦比对）。"""

    hash: str
    prompt: str
    response: str
    ts: float
    hit_count: int = 0
    vec: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"hash": self.hash, "prompt": self.prompt,
                "response": self.response, "ts": self.ts,
                "hit_count": self.hit_count, "vec": self.vec}

    @classmethod
    def from_dict(cls, d: Any) -> CacheEntry | None:
        if not isinstance(d, dict):
            return None
        try:
            return cls(hash=str(d.get("hash", "")),
                       prompt=str(d.get("prompt", "")),
                       response=str(d.get("response", "")),
                       ts=float(d.get("ts", 0) or 0),
                       hit_count=int(d.get("hit_count", 0) or 0),
                       vec=[float(x) for x in (d.get("vec") or [])])
        except Exception:
            return None


class SemanticCache:
    """语义缓存器（L1 观测级）。线程安全；编码异步。"""

    def __init__(self, base_dir: str | None = None, encoder: Any = None,
                 auto_start: bool = True) -> None:
        self._lock = threading.RLock()
        self._base_dir_override = base_dir
        self._encoder_override = encoder
        self._encoder_resolved: Any = None
        self._entries: dict[str, CacheEntry] = {}
        self._loaded = False
        self._q: queue.Queue = queue.Queue(maxsize=2000)
        self._worker: threading.Thread | None = None
        self._stop_flag = False
        # 统计
        self._stat = {"observed": 0, "hits": 0, "misses": 0,
                      "dropped": 0, "errors": 0, "writes": 0}
        if auto_start and self.enabled():
            self._ensure_worker()

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(_cfg("ENABLE_SEMANTIC_CACHE_OBSERVE", True))

    def threshold(self) -> float:
        """命中阈值（★第45批 T1 / P1-293：按实测校准 0.92 → 0.85）。

        ★灰度回退：``ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION``（默认 True）。
        关闭 → 返回 ``SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION``（0.92）
        ⇒ 与第41~44批行为**完全一致**（零回归）。
        ★本模块内**不含 0.92 字面量** —— 校准前值只存在于 config（数据而非硬编码）。
        """
        try:
            if not bool(_cfg("ENABLE_SEMANTIC_CACHE_THRESHOLD_CALIBRATION", True)):
                _pre = _cfg("SEMANTIC_CACHE_THRESHOLD_BEFORE_CALIBRATION",
                            DEFAULT_THRESHOLD)
                return float(_pre or DEFAULT_THRESHOLD)
            return float(_cfg("SEMANTIC_CACHE_THRESHOLD", DEFAULT_THRESHOLD)
                         or DEFAULT_THRESHOLD)
        except Exception:
            return DEFAULT_THRESHOLD

    def capacity(self) -> int:
        try:
            return max(1, int(_cfg("SEMANTIC_CACHE_CAPACITY", DEFAULT_CAPACITY) or 1))
        except Exception:
            return DEFAULT_CAPACITY

    def ttl_sec(self) -> float:
        try:
            _d = float(_cfg("SEMANTIC_CACHE_TTL_DAYS", DEFAULT_TTL_DAYS) or 0)
        except Exception:
            _d = DEFAULT_TTL_DAYS
        return _d * 86400.0 if _d > 0 else 0.0

    def max_text(self) -> int:
        try:
            return max(1, int(_cfg("SEMANTIC_CACHE_MAX_TEXT", DEFAULT_MAX_TEXT) or 1))
        except Exception:
            return DEFAULT_MAX_TEXT

    def base_dir(self) -> str:
        if self._base_dir_override:
            return self._base_dir_override
        _rel = str(_cfg("SEMANTIC_CACHE_DIR", "data/cache") or "data/cache")
        if os.path.isabs(_rel):
            return _rel
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        return os.path.join(_root, _rel.replace("/", os.sep))

    def cache_file(self) -> str:
        return os.path.join(self.base_dir(), "semantic_cache.jsonl")

    # ------------------------------------------------------------------
    # 编码器（懒加载；可注入）
    # ------------------------------------------------------------------
    def _encoder(self) -> Any:
        if self._encoder_override is not None:
            return self._encoder_override
        if self._encoder_resolved is not None:
            return self._encoder_resolved
        try:
            from nucleus.semantic.VectorEncoder import get_vector_encoder
            self._encoder_resolved = get_vector_encoder()
        except Exception:
            self._encoder_resolved = None
        return self._encoder_resolved

    def _encode_one(self, text: str) -> list | None:
        try:
            _enc = self._encoder()
            if _enc is None:
                return None
            _v = _enc.encode_one(text)
            if _v is None:
                return None
            return [float(x) for x in _v]
        except Exception:
            self._stat["errors"] += 1
            return None

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _truncate(self, text: Any) -> str:
        _s = text if isinstance(text, str) else ("" if text is None else str(text))
        _m = self.max_text()
        return _s if len(_s) <= _m else _s[:_m]

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        _fp = self.cache_file()
        if not os.path.isfile(_fp):
            return
        _now = time.time()
        _ttl = self.ttl_sec()
        try:
            with io.open(_fp, encoding="utf-8", errors="replace") as f:
                for _line in f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _e = CacheEntry.from_dict(json.loads(_line))
                    except Exception:
                        continue
                    if _e is None or not _e.hash:
                        continue
                    if _ttl > 0 and (_now - _e.ts) > _ttl:
                        continue
                    self._entries[_e.hash] = _e
        except Exception:
            return
        self._evict()

    def _evict(self) -> None:
        """超额 → LRU（取 ts 最旧）淘汰。"""
        _cap = self.capacity()
        if len(self._entries) <= _cap:
            return
        _rows = sorted(self._entries.values(), key=lambda e: e.ts)
        for _e in _rows[: len(self._entries) - _cap]:
            self._entries.pop(_e.hash, None)

    def _append_line(self, entry: CacheEntry) -> bool:
        try:
            _d = self.base_dir()
            if not os.path.isdir(_d):
                os.makedirs(_d, exist_ok=True)
            with self._lock:
                with io.open(self.cache_file(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False,
                                       default=str) + "\n")
            self._stat["writes"] += 1
            return True
        except Exception:
            self._stat["errors"] += 1
            return False

    # ------------------------------------------------------------------
    # 核心：查询 / 写入
    # ------------------------------------------------------------------
    @staticmethod
    def _cosine(a: list, b: list) -> float:
        """余弦（两向量均已 / 未归一化皆可；零范数返回 0.0）。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        _dot = 0.0
        _na = 0.0
        _nb = 0.0
        for _x, _y in zip(a, b):
            _dot += _x * _y
            _na += _x * _x
            _nb += _y * _y
        if _na <= 0.0 or _nb <= 0.0:
            return 0.0
        return _dot / ((_na ** 0.5) * (_nb ** 0.5))

    def lookup(self, prompt: str, vec: list | None = None) -> dict | None:
        """只读查询：返回**命中统计**（★绝不返回缓存内容）。

        Returns:
            ``{"similarity": float, "hit": bool, "hit_count": int,
               "entry_hash": str}``；无缓存 / 不可用时 ``None``。
        """
        if not prompt:
            return None
        self._load()
        _v = vec if vec is not None else self._encode_one(prompt)
        if not _v:
            return None
        _best: CacheEntry | None = None
        _best_sim = -2.0
        with self._lock:
            _entries = list(self._entries.values())
        for _e in _entries:
            if not _e.vec:
                continue
            _s = self._cosine(_v, _e.vec)
            if _s > _best_sim:
                _best_sim, _best = _s, _e
        if _best is None:
            return None
        _thr = self.threshold()
        if _best_sim >= _thr:
            _best.hit_count += 1
            self._stat["hits"] += 1
            return {"similarity": round(_best_sim, 4), "hit": True,
                    "hit_count": _best.hit_count, "entry_hash": _best.hash}
        self._stat["misses"] += 1
        return {"similarity": round(_best_sim, 4), "hit": False,
                "hit_count": _best.hit_count, "entry_hash": _best.hash}

    def add(self, prompt: str, response: str, vec: list | None = None) -> bool:
        """写入一条缓存（同 hash 已存在则刷新 ts）。"""
        if not prompt:
            return False
        _h = _hash_text(prompt)
        _v = vec if vec is not None else self._encode_one(prompt)
        if not _v:
            return False
        _e = CacheEntry(hash=_h, prompt=self._truncate(prompt),
                        response=self._truncate(response), ts=time.time(),
                        vec=_v)
        with self._lock:
            self._entries[_h] = _e
            self._evict()
        return self._append_line(_e)

    # ------------------------------------------------------------------
    # L1 观测入口（异步；零阻塞）
    # ------------------------------------------------------------------
    @staticmethod
    def _is_test_env() -> bool:
        """是否处于 pytest 环境（防"测试意外加载真模型"拖慢/污染）。"""
        try:
            if os.environ.get("PYTEST_CURRENT_TEST"):
                return True
            return "pytest" in sys.modules
        except Exception:
            return False

    def observe_async(self, prompt: str, response: str,
                      origin: str = "") -> bool:
        """投递一次观测（**立即返回**，编码/比对在后台线程完成）。"""
        if not self.enabled() or not prompt:
            return False
        # ★测试环境且未注入编码器 → 不投递（避免加载 90MB 真模型）
        if self._encoder_override is None and self._is_test_env():
            return False
        self._ensure_worker()
        try:
            self._q.put_nowait({"prompt": prompt, "response": response,
                                "origin": origin, "ts": time.time()})
            return True
        except queue.Full:
            self._stat["dropped"] += 1
            return False

    def process_pending(self) -> int:
        """同步处理积压（★测试用；生产由 daemon 线程调用）。返回处理条数。"""
        _n = 0
        while True:
            try:
                _item = self._q.get_nowait()
            except queue.Empty:
                break
            self._handle(_item)
            _n += 1
        return _n

    def _handle(self, item: dict) -> None:
        """处理一条观测：先比对（记命中统计），**再写入本次调用**。

        ★第41批 T3 修正（真实缺陷）：原实现在 ``lookup()`` 返回 ``None`` 时
        直接 ``return`` —— 而空缓存/编码器未就绪时 ``lookup`` 恒为 ``None``，
        导致**缓存永远不积累**（首次观测写不进去，之后每次都还是空）。
        现改为：无论比对结果如何，**都写入**本次 (prompt, response)。
        """
        try:
            self._stat["observed"] += 1
            self.lookup(item["prompt"])          # 只更新命中统计，返回值为统计
            self.add(item["prompt"], item.get("response", ""))
        except Exception:
            self._stat["errors"] += 1

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop_flag = False
            self._worker = threading.Thread(
                target=self._worker_loop, name="semantic-cache",
                daemon=True)
            self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_flag:
            try:
                _item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            self._handle(_item)

    def close(self, timeout: float = 3.0) -> None:
        """停止后台线程（测试收尾用）。"""
        self._stop_flag = True
        _w = self._worker
        if _w is not None and _w.is_alive():
            _w.join(timeout=timeout)
        self._worker = None

    # ------------------------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            _n = len(self._entries)
        return {"enabled": self.enabled(), "entries": _n,
                "capacity": self.capacity(), "threshold": self.threshold(),
                "ttl_days": self.ttl_sec() / 86400.0,
                "base_dir": self.base_dir(), **self._stat}

    def hit_rate(self) -> float:
        _o = self._stat["hits"] + self._stat["misses"]
        return round(self._stat["hits"] / _o, 4) if _o else 0.0


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_cache: SemanticCache | None = None
_singleton_lock = threading.Lock()


def get_semantic_cache() -> SemanticCache | None:
    """进程内单例（首次调用时创建）。**永不抛出。**"""
    global _cache
    try:
        with _singleton_lock:
            if _cache is None:
                _cache = SemanticCache()
            return _cache
    except Exception:
        return None


def reset_semantic_cache() -> None:
    """重置单例（测试用）。"""
    global _cache
    with _singleton_lock:
        if _cache is not None:
            try:
                _cache.close()
            except Exception:
                pass
        _cache = None


def observe_call(prompt: str, response: str, origin: str = "") -> bool:
    """便捷入口：观测一次调用（单例不可用/开关关闭 → False，绝不抛出）。"""
    _c = get_semantic_cache()
    if _c is None:
        return False
    return _c.observe_async(prompt, response, origin=origin)
