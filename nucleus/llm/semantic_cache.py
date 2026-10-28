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
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.log_state import changed as _m369_changed, sig as _m369_sig

# ★191刀2：L2 遥测 INFO 日志输出（命中率/污染率·按场景分布）
# ★205刀1：logger名改挂 pulse 子树，使 telemetry() 输出落盘 pulse.log
_L = logging.getLogger("pulse.semantic_cache_l2")

# ★198刀1：telemetry() 入口一次性 INFO 去重标记（进程内仅首调记录）。
_TELEMETRY_ENTRY_LOGGED = False

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
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:54:语义缓存操作异常", level="warning")
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
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::from_dict L91")
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
        self._dirty: set = set()  # ★B156-10：需回写磁盘的条目 hash 集合
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
            with open(_fp, encoding="utf-8", errors="replace") as f:
                for _line in f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _e = CacheEntry.from_dict(json.loads(_line))
                    except Exception as _m369_e:
                        silent_exc(_m369_e, where="nucleus/llm/semantic_cache.py:_load", level="debug")
                        continue
                    if _e is None or not _e.hash:
                        continue
                    if _ttl > 0 and (_now - _e.ts) > _ttl:
                        continue
                    self._entries[_e.hash] = _e
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::_load L232")
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
            with self._lock, open(self.cache_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False,
                                   default=str) + "\n")
            self._stat["writes"] += 1
            return True
        except Exception:
            self._stat["errors"] += 1
            return False

    def flush(self) -> int:
        """将内存中已变更的条目（如累加的 ``hit_count``）回写磁盘 jsonl。

        ★B156-10 修正：原实现 ``hit_count`` 仅在内存累加、**从不落盘**，且 ``add``
        会新建 ``CacheEntry(hit_count=0)`` 覆盖既有计数，导致「命中口径」跨写入/重启
        后失真（死置）。本方法按当前 ``_entries`` 全量重写 jsonl（按 hash 去重），
        使 ``hit_count`` 真正落盘。仅由守护线程（``_handle`` 后）与 ``close()`` 触发，
        主推理流程零阻塞。
        """
        if not self._dirty and not self._entries:
            return 0
        try:
            _d = self.base_dir()
            if not os.path.isdir(_d):
                os.makedirs(_d, exist_ok=True)
            _fp = self.cache_file()
            with self._lock:
                _rows = [e.to_dict() for e in self._entries.values()]
                self._dirty.clear()
            with open(_fp, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(_r, ensure_ascii=False, default=str) + "\n" for _r in _rows)
            return len(_rows)
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::flush")
            return 0

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

    def lookup(self, prompt: str, vec: list | None = None,
               with_content: bool = False) -> dict | None:
        """只读查询：默认返回**命中统计**（★L1 红线：绝不返回缓存内容）。

        ★主线第138批 T-138d：新增 ``with_content``（默认 ``False`` → **旧调用方行为完全不变**）。
        ``True`` 时额外返回 ``response`` / ``prompt``（仅供 L2 层决策类消费，
        **需由调用方自行过安全闸门**）。

        Returns:
            ``{"similarity": float, "hit": bool, "hit_count": int,
               "entry_hash": str}``（``with_content=True`` 时另含
               ``response`` / ``prompt``）；无缓存 / 不可用时 ``None``。
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
        _out = {"similarity": round(_best_sim, 4), "entry_hash": _best.hash}
        if _best_sim >= _thr:
            _best.hit_count += 1
            self._stat["hits"] += 1
            self._dirty.add(_best.hash)  # ★B156-10：标记待回写
            _out["hit"] = True
        else:
            self._stat["misses"] += 1
            _out["hit"] = False
        _out["hit_count"] = _best.hit_count   # ★与原实现一致：命中后的计数
        if with_content:
            _out["response"] = _best.response
            _out["prompt"] = _best.prompt
        return _out

    def add(self, prompt: str, response: str, vec: list | None = None) -> bool:
        """写入一条缓存（同 hash 已存在则刷新 ts）。"""
        if not prompt:
            return False
        _h = _hash_text(prompt)
        _v = vec if vec is not None else self._encode_one(prompt)
        if not _v:
            return False
        with self._lock:
            # ★B156-10：同 hash 已存在时保留既有 hit_count（避免 _handle 流程里
            # lookup 累加的命中数被新建 CacheEntry 的 0 值覆盖，导致「命中口径」失真）
            _existing = self._entries.get(_h)
            _keep_hits = _existing.hit_count if _existing else 0
            _e = CacheEntry(hash=_h, prompt=self._truncate(prompt),
                            response=self._truncate(response), ts=time.time(),
                            hit_count=_keep_hits, vec=_v)
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
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::_is_test_env L350")
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
            self.flush()                         # ★B156-10：hit_count 落盘回写
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
        try:
            self.flush()  # ★B156-10：退出前把内存命中数落盘
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::close")
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
    except Exception as e:
        silent_exc(e, where="nucleus.llm.semantic_cache::get_semantic_cache L452")
        return None


def reset_semantic_cache() -> None:
    """重置单例（测试用）。"""
    global _cache
    with _singleton_lock:
        if _cache is not None:
            try:
                _cache.close()
            except Exception as e:
                silent_exc(e, where="nucleus.llm.semantic_cache::reset_semantic_cache L463")
        _cache = None


def observe_call(prompt: str, response: str, origin: str = "") -> bool:
    """便捷入口：观测一次调用（单例不可用/开关关闭 → False，绝不抛出）。"""
    _c = get_semantic_cache()
    if _c is None:
        return False
    return _c.observe_async(prompt, response, origin=origin)


# ===========================================================================
# ★主线第138批 T-138d：语义缓存 L2（**返回级**）
# ---------------------------------------------------------------------------
# 依据：`docs/设计文档/语义缓存升L2方案_v1.0.md`（第44批）§2.2/§5。
#
# 定位：L1（SemanticCache）结构上**不可能**返回内容 → 可常开；
#   L2 后这条保证消失 → 用**四道闸门 + 内存 TTL** 替代，任一不过静默回落。
#
# 与 L1 的边界：
#   * L2 自带**独立内存存储**（TTL 1 小时，不持久化，不污染 data/cache）；
#   * 不改 L1 的 7 天观测账（两层分层共存）；
#   * 关闭（``ENABLE_SEMANTIC_CACHE_L2=False``）→ **零副作用**，逐字回 L1 行为。
#
# ★隔离：编码器可注入（测试传假件，**不加载真模型**）。
# ===========================================================================
#: 幂等闸：指代词 / 会话私有标记（含则跳过缓存，上下文敏感高发区）。
_L2_DEICTIC_PATTERNS = (
    "这个", "那个", "上面", "刚才", "刚刚", "继续", "再来", "上一个",
    "它", "他们", "她们", "这条", "那条", "上次", "刚说", "下一个",
)
#: 幂等闸：时间 / 随机 / UUID 特征（正则）。
_L2_TIME_PATTERNS = (
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",          # 日期串
    r"\d{1,2}:\d{2}",                        # 时间串
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}",  # UUID 片段
    r"\buser[_\-]?id\b", r"\buuid\b",
    "今天", "现在", "刚刚", "目前", "此刻",
)


def _l2_cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:_l2_cfg", level="debug")
        return default


def _l2_prompt_is_session_private(prompt: str) -> bool:
    """幂等闸子判定：命中指代词 / 时间随机标记 → True（跳过缓存）。"""
    if not prompt:
        return True
    try:
        import re as _re
        for _p in _L2_DEICTIC_PATTERNS:
            if _p in prompt:
                return True
        for _p in _L2_TIME_PATTERNS:
            if _re.search(_p, prompt):
                return True
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:_l2_prompt_is_session_private",
                   level="debug")
        return True  # 不确定 → 宁可不用缓存
    return False


def _l2_classify_scene(prompt: str) -> str:
    """★188刀1：将 prompt 粗分为 代码类/闲聊类/推理类（确定性启发式，仅供遥测分桶）。

    不依赖任何外部调用，纯本地字符串判定；分类结果仅用于遥测计数，
    不参与任何缓存命中/拒绝判定（零行为变化）。
    """
    if not prompt:
        return "chat"
    _code_markers = ("def ", "class ", "import ", "function", "=>", "```",
                     "Traceback", "Exception", "Error:", "return ", "SELECT ",
                     "{", "}", "print(", "var ", "const ", "public ", "=>")
    _reason_markers = ("为什么", "如何", "分析", "因为", "所以", "逻辑", "推导",
                       "原因", "解释", "原理", "比较", "区别", "vs", "?",
                       "？", "怎么", "怎样", "论证", "结论")
    _code = sum(1 for m in _code_markers if m in prompt)
    _reason = sum(1 for m in _reason_markers if m in prompt)
    if _code >= 2:
        return "code"
    if _reason >= 1:
        return "reasoning"
    return "chat"


#: ★377刀6：**机器产物硬签名** —— 命中即判 code，不受 `_code_markers` 的「≥2」限制。
#:   为什么需要这一层：`_l2_classify_scene` 判 code 要求**两个**标记，于是
#:   「只有 `Traceback` 一个标记」的报错行会被判成 `chat`，从而被 origin 场景闸放过 ——
#:   刀6 生产口径探针实测就是从这里漏的（`Traceback (most recent call last):` ⇒ chat ⇒ 放行）。
#:   只收两条「人不会在自然语言提问里逐字写出来」的签名：栈回溯头、代码围栏。
#:   **刻意不**把 `def ` / `import ` / `return ` 这类单词塞进来：那个方向会误杀
#:   「Python 里 import 和 from 的区别是什么？」这种**真·编程知识问答**，
#:   等于把刀6 的目的反过来做（第一版「只放 chat」已经栽过同类跟头）。
#:   单词类仍只走原有的 ≥2 计数。
_M377_HARD_CODE_SIGNATURES = ("traceback (most recent call last)", "```")


def _m377_scene_for_gate(prompt: str) -> str:
    """★377刀6：**准入闸专用**场景口径 = 硬签名优先，其余仍走 `_l2_classify_scene`。

    两层是**叠加**，不是替换：`_l2_classify_scene()` 原样不动，
    188刀1 的遥测分桶（命中率/污染率按场景）口径逐字不变；
    本函数只补两条**机器产物**硬签名，把「单标记的栈/围栏」从 `chat` 里捞出来。
    """
    _p = (prompt or "").lower()
    for _sig in _M377_HARD_CODE_SIGNATURES:
        if _sig in _p:
            return "code"
    return _l2_classify_scene(prompt)


#: ★378刀5（T-378-L2签名分级-1）：把「机器产物判据」从**两条字符串**升级成**分级签名**。
#:   377刀6 只收了栈回溯头与代码围栏 —— 那是"最要命"，不是"穷举"。实测仍漏网的都是
#:   `_code >= 2` 数不到的单标记形态：单处 `SELECT … FROM`、单处行首 `print(` /
#:   `def foo(` / `import os`、单行 `ValueError: …`（没有 Traceback 头）。
#:
#:   ★两条判据都认**形状**、不认"出现过某个词"：
#:     ① 行首代码关键字 —— 自然语言提问不会把 "def foo(" 写在行首；
#:     ② SQL 的 select…from **成对**出现 —— 解释"SELECT 和 UPDATE 的区别"时不会带 from；
#:     ③ 行首 `XxxError:` / `XxxException:` —— 报错行形态（句中的 `Error:` 不算）。
#:   ★刻意**不收**：裸 `import ` / `print(` / `Error:` 出现在**句中** —— 那会误杀
#:   「Python 里 import 和 from 的区别是什么？」，与本刀目的正相反（377刀6 已栽过一次）。
_M378_CODE_LINE_PREFIX_RE = re.compile(
    r"(?m)^[ \t]*(?:def |class |async def |import [A-Za-z_]|from [A-Za-z_][\w.]* import "
    r"|return |print\(|raise |@[A-Za-z_])")
_M378_SQL_PAIR_RE = re.compile(r"(?is)\bselect\b[^\n]{0,300}?\bfrom\b")
_M378_EXC_LINE_RE = re.compile(r"(?m)^[ \t]*[A-Za-z_][\w.]*(?:Error|Exception)\s*:")


def _m378_scene_for_gate(prompt: str) -> str:
    """★378刀5（T-378-L2签名分级-1）：准入闸场景口径 = 377 硬签名 ∧ 378 分级签名 ∧ 旧分类轴。

    叠加顺序（先严后宽）；`_l2_classify_scene()` 仍然**一行不改**
    —— 188刀1 建它时只承诺用于遥测分桶，本函数不去动那个契约。
    """
    if _m377_scene_for_gate(prompt) == "code":
        return "code"
    _p = prompt or ""
    if (_M378_CODE_LINE_PREFIX_RE.search(_p)
            or _M378_SQL_PAIR_RE.search(_p)
            or _M378_EXC_LINE_RE.search(_p)):
        return "code"
    return _l2_classify_scene(prompt)


@dataclass
class L2Entry:
    """L2 缓存条目（内存；含向量供余弦比对）。"""

    hash: str
    prompt: str
    response: str
    ts: float
    last_hit_ts: float
    origin: str = ""
    scene: str = ""
    vec: list = field(default_factory=list)


class SemanticCacheL2:
    """语义缓存 L2：**命中且过四闸 → 直接返回缓存响应**。纯内存、线程安全、**永不抛出**。

    ★284批刀7 命中优先级语义：**L1 优先，L2 兜底**——L1 是精准命中（完全匹配/高相似，
    只回命中统计、结构上不返回内容），L2 是语义相似（四闸通过后返回缓存响应）。
    两层缓存不互相遮蔽：L1 精准命中优先；L2 仅在 L1 命中语义判定后作为语义相似兜底。

    四道闸门（全部通过才返回；任一失败 → 静默回落渠道调用）：
      ① 置信闸：余弦 sim ≥ 阈值（沿用 L1 校准值 0.85）；
      ② 时效闸：now - ts ≤ L2 TTL（1 小时）；
      ③ 幂等闸：origin 过准入闸（★377刀6：`user_query` 全放；
         `system_internal` 仅限**知识问答类**即场景 `chat`/`reasoning`，`code` 类仍拒）
         且 prompt 无指代/时间标记 且 长度 ≥ MIN_LEN；
      ④ 质量闸：缓存响应非空。

    淘汰：超容量 → LRU（按 ``last_hit_ts``，而非写入 ts）；过 TTL 条目懒删。
    """

    #: ★377刀6：`system_internal`（自动学习映射）放进白名单，但**只限知识问答类** ——
    #: 具体场景由 `_M377_ORIGIN_SCENE_GATE` 限定，evolution/code 类仍拒。
    _ALLOWED_ORIGINS = ("user_query", "system_internal")

    #: ★377刀6：每个 origin 的**场景闸**（不在表里的 origin 原样放行）。
    #:   `system_internal` 放 `chat` + `reasoning`，**只拒 `code`**。
    #:   为什么不是「只放 chat」—— 拿现有分类轴实测就会撞墙：`_l2_classify_scene`
    #:   把任何带「为什么/怎么/？」的句子判成 `reasoning`，而**知识问答本身就是提问**
    #:   ⇒ 只放 chat 等于把所有真·知识问答全拒（本刀第一版就这么写，单测当场红）。
    #:   在这条轴上，真正要挡的只有 `code`（演化改码形态），那才是
    #:   「缓存旧答案污染演化」的来源。故闸 = 非 code。
    #:   ★判据不是裸用 `_l2_classify_scene`，而是 `_m377_scene_for_gate()` ——
    #:   它在上面那层之上补了「机器产物硬签名」（单标记栈/围栏也算 code），
    #:   原因见该常量注释：裸用会放过只带一个 `Traceback` 的报错行。
    _M377_ORIGIN_SCENE_GATE = {"system_internal": ("chat", "reasoning")}

    @classmethod
    def _m377_system_internal_on(cls) -> bool:
        """★377刀6 灰度开关（默认开）：关掉即逐字回到 331 批的「只放 user_query」。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_L2_ADMIT_SYSTEM_INTERNAL", True))
        except Exception as _e:
            silent_exc(_e, where="SemanticCacheL2::_m377_system_internal_on")
            return True

    @classmethod
    def _m377_origin_gate(cls, origin: str, prompt: str) -> tuple[bool, str]:
        """★377刀6：origin 准入闸 = **白名单** ∧ **每 origin 的场景闸**。

        返回 `(是否放行, 原因)`；原因用于遥测分桶（"被拒"不再是黑箱）。

        四态：`origin_not_allowed` / `system_internal_admission_off` /
        `allowed_knowledge_qa` / `scene_<x>_not_knowledge_qa` / `allowed`。
        """
        _o = str(origin or "")
        if _o not in cls._ALLOWED_ORIGINS:
            return False, "origin_not_allowed"
        _scenes = cls._M377_ORIGIN_SCENE_GATE.get(_o) or ()
        if not _scenes:
            return True, "allowed"
        if _o == "system_internal" and not cls._m377_system_internal_on():
            return False, "system_internal_admission_off"
        # ★378刀5（T-378-L2签名分级-1）：判据升级为分级签名（行首代码/SQL 成对/异常行），
        #   仍含 377刀6 的两条硬签名；旧分类轴只作最后回落。
        _scene = _m378_scene_for_gate(prompt)
        if _scene in _scenes:
            return True, "allowed_knowledge_qa"
        return False, "scene_%s_not_knowledge_qa" % _scene

    def _m377_adm_note(self, admitted: bool, why: str) -> None:
        """★377刀6：准入结果分桶计数（`{原因: 次数}`；永不抛）。"""
        try:
            _k = "admitted" if admitted else "rejected"
            _b = self._m377_adm.setdefault(_k, {})
            _b[why] = _b.get(why, 0) + 1
        except Exception as _e:
            silent_exc(_e, where="SemanticCacheL2::_m377_adm_note")

    def __init__(self, cache: SemanticCache | None = None, encoder: Any = None) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, L2Entry] = {}
        self._cache = cache                    # 可选：复用 L1 编码器与阈值
        self._encoder_override = encoder
        self._stat = {"hits": 0, "misses": 0, "rejected": 0, "stores": 0}
        # ★377刀6：准入闸分桶计数（`{原因: 次数}`）——
        #   改前 origin 被拒是**直接 return**，不进 `_stat`，于是"非用户流量全被挡"
        #   这件事在遥测里完全看不见（表现为 hits/misses 恒 0 的"空转"，与"没请求"同形）。
        self._m377_adm = {"admitted": {}, "rejected": {}}
        # ★188刀1：L2 只读遥测（命中率/污染率·按场景分桶：代码类/闲聊类/推理类）。
        #   纯计数、零行为变化（不影响四闸命中判定/阈值/开关）。下游负反馈经
        #   record_feedback() 回填，未被调用时 neg 恒为 0（跑 2-3 批出分布）。
        self._telemetry = {
            "overall":   {"hits": 0, "misses": 0, "neg": 0},
            "code":      {"hits": 0, "misses": 0, "neg": 0},
            "chat":      {"hits": 0, "misses": 0, "neg": 0},
            "reasoning": {"hits": 0, "misses": 0, "neg": 0},
        }

    # ---------------- 配置 ----------------
    def enabled(self) -> bool:
        return bool(_l2_cfg("ENABLE_SEMANTIC_CACHE_L2", False))

    def ttl_sec(self) -> float:
        try:
            return max(0.0, float(_l2_cfg("SEMANTIC_CACHE_L2_TTL_SEC", 3600) or 0))
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.ttl_sec",
                       level="debug")
            return 3600.0

    def size(self) -> int:
        """★316批刀2：内存层条目数（只读；区分「store 空」与「真不一致」）。"""
        try:
            with self._lock:
                return len(self._entries)
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.size", level="debug")
            return 0

    def capacity(self) -> int:
        try:
            return max(1, int(_l2_cfg("SEMANTIC_CACHE_L2_CAPACITY", 2000) or 1))
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.capacity",
                       level="debug")
            return 2000

    def min_len(self) -> int:
        try:
            return max(1, int(_l2_cfg("SEMANTIC_CACHE_L2_MIN_LEN", 8) or 1))
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.min_len",
                       level="debug")
            return 8

    def ratio(self) -> float:
        try:
            return min(1.0, max(0.0, float(_l2_cfg("SEMANTIC_CACHE_L2_RATIO", 1.0) or 0.0)))
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.ratio",
                       level="debug")
            return 1.0

    def threshold(self) -> float:
        if self._cache is not None:
            return self._cache.threshold()
        try:
            return float(_l2_cfg("SEMANTIC_CACHE_THRESHOLD", DEFAULT_THRESHOLD)
                         or DEFAULT_THRESHOLD)
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.threshold",
                       level="debug")
            return DEFAULT_THRESHOLD

    # ---------------- 编码 ----------------
    def _encoder(self) -> Any:
        if self._encoder_override is not None:
            return self._encoder_override
        if self._cache is not None:
            return self._cache._encoder()
        try:
            from nucleus.semantic.VectorEncoder import get_vector_encoder
            return get_vector_encoder()
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2._encoder",
                       level="debug")
            return None

    def _encode_one(self, text: str) -> list | None:
        try:
            _enc = self._encoder()
            if _enc is None:
                return None
            _v = _enc.encode_one(text)
            return [float(x) for x in _v] if _v is not None else None
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2._encode_one",
                       level="debug")
            return None

    # ---------------- 内部：淘汰 ----------------
    def _purge_expired(self, now: float) -> None:
        _ttl = self.ttl_sec()
        if _ttl <= 0:
            return
        _dead = [_h for _h, _e in self._entries.items() if (now - _e.ts) > _ttl]
        for _h in _dead:
            self._entries.pop(_h, None)

    def _evict(self) -> None:
        _cap = self.capacity()
        if len(self._entries) <= _cap:
            return
        _rows = sorted(self._entries.values(), key=lambda e: e.last_hit_ts)
        for _e in _rows[: len(self._entries) - _cap]:
            self._entries.pop(_e.hash, None)

    # ★331批刀9：去标识化影子计数（**被准入闸拒绝**的写入请求记一次）
    #   落盘只含「origin 名 + 键哈希 + 计数」——**不存任何原话**（PII 面不扩大）。
    _M331_SHADOW_PATH = os.path.join("data", "reports",
                                     "semantic_cache_l2_shadow_331.json")
    _M331_SHADOW_BUF: dict = {"by_origin": {}, "total": 0,
                              "admitted_origins": list(_ALLOWED_ORIGINS)}

    @classmethod
    def _m331_shadow_note(cls, origin: str, prompt: str) -> None:
        """★331批刀9：记一次"本可进 L2 但被准入闸拦下"的**去标识化**计数。

        为什么记而不是放行：开准入（把自主流量加进 `_ALLOWED_ORIGINS`）是**策略裁定**，
        会改变缓存内容构成与 PII 面，不在本批授权。记数只是把 330 刀5 的
        "料从未来过"（当时是推断）变成**可数事实**，为裁定提供依据。
        失败一律静默：影子计数坏了不能影响主链路。
        """
        try:
            _o = str(origin or "") or "(empty)"
            if _o in cls._ALLOWED_ORIGINS:
                return
            _h = hashlib.sha256(str(prompt or "").encode("utf-8",
                                                          errors="replace")).hexdigest()
            _b = cls._M331_SHADOW_BUF
            _slot = _b["by_origin"].get(_o)
            if not isinstance(_slot, dict):
                _slot = {"count": 0, "distinct_keys": []}
                _b["by_origin"][_o] = _slot
            _slot["count"] = int(_slot.get("count", 0)) + 1
            if _h[:16] not in _slot["distinct_keys"]:
                if len(_slot["distinct_keys"]) < 100:
                    _slot["distinct_keys"].append(_h[:16])
            _b["total"] = int(_b.get("total", 0)) + 1
        except Exception as _e:
            silent_exc(_e, where="SemanticCacheL2::_m331_shadow_note")

    @classmethod
    def _m331_shadow_flush(cls, root: str | None = None) -> bool:
        """★331批刀9：把影子计数原子落盘（只含计数与哈希）。"""
        try:
            _base = root or os.getcwd()
            _p = os.path.join(_base, cls._M331_SHADOW_PATH)
            os.makedirs(os.path.dirname(_p), exist_ok=True)
            _body = {"version": "331.1",
                     "note": "去标识化影子计数：仅 origin/键哈希/计数，**无原话**",
                     "admitted_origins": list(cls._ALLOWED_ORIGINS),
                     "total": int(cls._M331_SHADOW_BUF.get("total", 0)),
                     "by_origin": dict(cls._M331_SHADOW_BUF.get("by_origin") or {}),
                     "updated_at": time.time()}
            _tmp = _p + ".tmp"
            with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_body, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, _p)
            return True
        except Exception as _e:
            silent_exc(_e, where="SemanticCacheL2::_m331_shadow_flush")
            return False

    # ---------------- 写入 ----------------
    def store(self, prompt: str, response: str, origin: str = "",
              vec: list | None = None) -> bool:
        """写入一条 L2 缓存（仅 **合法 origin + 非空响应 + 足够长 prompt**）。"""
        if not self.enabled() or not prompt or not response:
            return False
        _adm377, _adm_why377 = self._m377_origin_gate(origin, prompt)
        if not _adm377:
            self._m377_adm_note(False, _adm_why377)
            # ★331批刀9：被准入闸拦下 ⇒ 记一次去标识化影子计数（不改准入语义）
            self._m331_shadow_note(origin, prompt)
            return False
        self._m377_adm_note(True, _adm_why377)
        if len(prompt) < self.min_len():
            return False
        if _l2_prompt_is_session_private(prompt):
            return False
        _v = vec if vec is not None else self._encode_one(prompt)
        if not _v:
            return False
        _now = time.time()
        _scene = _l2_classify_scene(prompt)
        _e = L2Entry(hash=_hash_text(prompt), prompt=prompt, response=response,
                     ts=_now, last_hit_ts=_now, origin=str(origin),
                     scene=_scene, vec=_v)
        with self._lock:
            self._entries[_e.hash] = _e
            self._purge_expired(_now)
            self._evict()
        self._stat["stores"] += 1
        return True

    # ---------------- 查询（四闸） ----------------
    def lookup(self, prompt: str, origin: str = "",
               vec: list | None = None) -> dict | None:
        """命中且过四闸 → 返回 ``{…, "response": …}``；否则 ``None``（静默回落）。

        ★188刀1：仅在计数层追加只读遥测（命中/未命中/拒绝按场景分桶），
        不改变四闸判定、阈值或任何返回内容（零行为变化）。
        """
        _scene = _l2_classify_scene(prompt)
        try:
            if not self.enabled() or not prompt:
                return None
            _adm377, _adm_why377 = self._m377_origin_gate(origin, prompt)
            if not _adm377:
                self._m377_adm_note(False, _adm_why377)
                # ★331批刀9：查询侧同样记影子（"本可命中但被准入拦"的次数）
                self._m331_shadow_note(origin, prompt)
                return None
            self._m377_adm_note(True, _adm_why377)
            if len(prompt) < self.min_len():
                self._stat["rejected"] += 1
                self._tele_bump(_scene, "misses")
                return None
            if _l2_prompt_is_session_private(prompt):
                self._stat["rejected"] += 1
                self._tele_bump(_scene, "misses")
                return None
            _now = time.time()
            with self._lock:
                self._purge_expired(_now)
                _entries = list(self._entries.values())
            if not _entries:
                self._stat["misses"] += 1
                self._tele_bump(_scene, "misses")
                return None
            _v = vec if vec is not None else self._encode_one(prompt)
            if not _v:
                self._stat["misses"] += 1
                self._tele_bump(_scene, "misses")
                return None
            _best: L2Entry | None = None
            _best_sim = -2.0
            for _e in _entries:
                if not _e.vec:
                    continue
                _s = SemanticCache._cosine(_v, _e.vec)
                if _s > _best_sim:
                    _best_sim, _best = _s, _e
            if _best is None:
                self._stat["misses"] += 1
                self._tele_bump(_scene, "misses")
                return None
            # ① 置信闸
            if _best_sim < self.threshold():
                self._stat["misses"] += 1
                self._tele_bump(_scene, "misses")
                return None
            # ② 时效闸
            _ttl = self.ttl_sec()
            if _ttl > 0 and (_now - _best.ts) > _ttl:
                self._stat["misses"] += 1
                self._tele_bump(_scene, "misses")
                return None
            # ④ 质量闸
            if not _best.response:
                self._stat["rejected"] += 1
                self._tele_bump(_scene, "misses")
                return None
            # 流量分桶（确定性）
            _r = self.ratio()
            if _r < 1.0:
                _bucket = int(_hash_text(prompt)[:8], 16) % 100
                if _bucket >= int(_r * 100):
                    self._stat["rejected"] += 1
                    self._tele_bump(_scene, "misses")
                    return None
            _best.last_hit_ts = _now
            self._stat["hits"] += 1
            self._tele_bump(_scene, "hits")
            return {"response": _best.response, "similarity": round(_best_sim, 4),
                    "entry_hash": _best.hash, "origin": _best.origin}
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:SemanticCacheL2.lookup",
                       level="debug")
            return None

    def _tele_bump(self, scene: str, kind: str) -> None:
        """★188刀1：遥测计数累加（命中/未命中按场景分桶）。零副作用。"""
        _key = {"hit": "hits", "miss": "misses"}.get(kind, kind)
        try:
            _s = scene if scene in self._telemetry else "chat"
            self._telemetry[_s][_key] += 1
            self._telemetry["overall"][_key] += 1
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::_tele_bump")

    def record_feedback(self, entry_hash: str, negative: bool = True) -> None:
        """★188刀1：下游对一次 L2 命中（已返回缓存响应）的负反馈回填。

        仅供遥测（污染率 = neg / hits）；不改动任何命中判定或存储。
        未被下游调用时 neg 恒为 0（跑 2-3 批出分布）。
        """
        try:
            with self._lock:
                _e = self._entries.get(entry_hash)
                _scene = getattr(_e, "scene", "") or "chat" if _e else "chat"
            if negative:
                self._telemetry[_scene]["neg"] += 1
                self._telemetry["overall"]["neg"] += 1
        except Exception as e:
            silent_exc(e,
                       "nucleus/llm/semantic_cache.py:SemanticCacheL2.record_feedback",
                       level="debug")

    def telemetry(self) -> dict:
        """★188刀1：只读遥测快照（命中率/污染率·按场景分桶）。零副作用。"""
        # ★198刀1：入口一次性 INFO（验收：telemetry() 确被进入，用于区分「分支未及」与「异常吞」）。
        #   进程内仅首调记录；不改任何命中判定 / 存储 / 返回。
        global _TELEMETRY_ENTRY_LOGGED
        if not _TELEMETRY_ENTRY_LOGGED:
            _TELEMETRY_ENTRY_LOGGED = True
            _L.info("[semantic_cache_l2] telemetry 入口被调用（目标读数采集启动）")
        with self._lock:
            _t = {_k: dict(_v) for _k, _v in self._telemetry.items()}
        for _v in _t.values():
            _h = _v.get("hits", 0)
            _m = _v.get("misses", 0)
            _v["hit_rate"] = round(_h / (_h + _m), 4) if (_h + _m) else 0.0
            _v["pollution_rate"] = round(_v.get("neg", 0) / _h, 4) if _h else 0.0
        # ★377刀6：准入闸分桶单独落一行（变化才打）。
        #   为什么不并进下面那行：下面那行有「读数不变就抑制」的判据（369 刀1），
        #   把准入桶塞进同一个签名会让两个不同事实互相遮蔽；且准入先于四闸发生，
        #   被拒的请求**根本不进** hits/misses ⇒ 不单列就永远是「全 0 空转」。
        try:
            _adm377 = getattr(self, "_m377_adm", None) or {}
            _adm_a = int(sum((_adm377.get("admitted") or {}).values()))
            _adm_r = int(sum((_adm377.get("rejected") or {}).values()))
            _adm_sig = "%s|%s" % (sorted((_adm377.get("admitted") or {}).items()),
                                  sorted((_adm377.get("rejected") or {}).items()))
            if (_adm_a or _adm_r) and _m369_changed("semantic_cache_l2.admission",
                                                    _adm_sig):
                _L.info("[semantic_cache_l2] 准入闸 admission admitted=%d rejected=%d "
                        "明细=%s", _adm_a, _adm_r, _adm377)
        except Exception as _adm_e:
            silent_exc(_adm_e,
                       where="SemanticCacheL2::telemetry admission")
        # ★191刀2：INFO 日志输出（命中率/污染率·按场景分布），方便观测共窗收证。
        #   仅新增日志，不改任何命中判定 / 存储 / 返回。
        try:
            # ★369刀1（E9 F2 族）：读数变化才打，整次快照聚合为一行。
            #   实证：40.9h / 6034 条（约 148 条/h），读数恒 0 仍每拍按场景刷一整套。
            #   降噪不等于消失：读数不变只记抑制计数，一变立刻恢复一行含全部场景明细的汇总。
            if not _m369_changed("semantic_cache_l2.telemetry", _m369_sig(_t)):
                return _t
            _tot_h = sum(int(_v.get("hits", 0) or 0) for _v in _t.values())
            _tot_m = sum(int(_v.get("misses", 0) or 0) for _v in _t.values())
            _tot_n = sum(int(_v.get("neg", 0) or 0) for _v in _t.values())
            _tot_hr = round(_tot_h / (_tot_h + _tot_m), 4) if (_tot_h + _tot_m) else 0.0
            _tot_pr = round(_tot_n / _tot_h, 4) if _tot_h else 0.0
            _L.info(
                "[semantic_cache_l2] telemetry 汇总 scenes=%d hits=%d misses=%d neg=%d stores=%d"
                " hit_rate=%s pollution_rate=%s 明细=%s",
                len(_t), _tot_h, _tot_m, _tot_n, int(self._stat.get("stores", 0) or 0),
                _tot_hr, _tot_pr,
                " ".join(
                    "%s(h=%s/m=%s/hr=%s/pr=%s)" % (
                        _k, _v.get("hits", 0), _v.get("misses", 0),
                        _v.get("hit_rate", 0.0), _v.get("pollution_rate", 0.0),
                    )
                    for _k, _v in sorted(_t.items())
                ),
            )
        except Exception as e:
            silent_exc(e, where="nucleus.llm.semantic_cache::SemanticCacheL2.telemetry.log")
        return _t

    # ---------------- 观测 ----------------
    def stats(self) -> dict:
        with self._lock:
            _n = len(self._entries)
        _o = self._stat["hits"] + self._stat["misses"]
        _adm = getattr(self, "_m377_adm", None) or {}
        return {"enabled": self.enabled(), "entries": _n, "capacity": self.capacity(),
                "ttl_sec": self.ttl_sec(), "threshold": self.threshold(),
                "hit_rate": round(self._stat["hits"] / _o, 4) if _o else 0.0,
                "telemetry": self.telemetry(),
                # ★377刀6：准入结果分桶（被拒也要可见，否则"全被挡"与"没请求"同形）
                "admission": {"admitted": dict(_adm.get("admitted") or {}),
                              "rejected": dict(_adm.get("rejected") or {})},
                **self._stat}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: L2 进程内单例（与 L1 单例分离）
_l2_cache: SemanticCacheL2 | None = None
_l2_singleton_lock = threading.Lock()


def get_semantic_cache_l2() -> SemanticCacheL2:
    """进程内 L2 单例（首次调用时创建）。**永不抛出**。"""
    global _l2_cache
    try:
        with _l2_singleton_lock:
            if _l2_cache is None:
                _l2_cache = SemanticCacheL2(cache=get_semantic_cache())
            return _l2_cache
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:get_semantic_cache_l2",
                   level="warning")
        return SemanticCacheL2()


def reset_semantic_cache_l2() -> None:
    """重置 L2 单例（测试用）。"""
    global _l2_cache
    with _l2_singleton_lock:
        _l2_cache = None


def lookup_l2(prompt: str, origin: str = "") -> dict | None:
    """便捷入口：L2 命中查询（单例不可用/开关关闭 → None，绝不抛出）。"""
    try:
        _c = get_semantic_cache_l2()
        return _c.lookup(prompt, origin=origin)
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:lookup_l2", level="debug")
        return None


def store_l2(prompt: str, response: str, origin: str = "") -> bool:
    """便捷入口：写入 L2（不可用/不合法 → False，绝不抛出）。"""
    try:
        _c = get_semantic_cache_l2()
        return _c.store(prompt, response, origin=origin)
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:store_l2", level="debug")
        return False


# ★284批刀1：L2 读侧影子观测（L1 命中后并调 lookup_l2，只记不返回）。
#   依据：烛微283批前置——lookup_l2 生产调用面=0，L1 活跃在写 ⇒ hits/misses 恒 0。
#   影子期由 ENABLE_SEMANTIC_CACHE_L2_SHADOW 总闸包裹（默认 False → None，零副作用）。
#   语义：L1 精准命中优先（完全匹配/高相似），L2 语义相似兜底（刀7 优先级口径）。
#   只记不返回：不改变任何命中判定 / 存储 / 返回，仅 INFO 日志观测「若真接上会命中多少」。
def lookup_l2_shadow(prompt: str, origin: str = "") -> dict | None:
    """L2 读侧影子观测：L1 命中后并调一次 lookup_l2，只记不返回。

    流程（与刀7「L1 优先 L2 兜底」口径一致）：
      1. L1 lookup（只回命中统计，L1 红线：绝不返回内容）
      2. 若 L1 命中 → 并调一次 lookup_l2（L2 语义相似）
      3. 记录 l2_would_hit + 与 L1 结果一致率（仅 INFO 日志）

    返回观测 dict（供隔离测试断言）；影子闸关闭 / L1 未命中 / 异常 → None。
    """
    if not bool(_l2_cfg("ENABLE_SEMANTIC_CACHE_L2_SHADOW", False)):
        return None
    try:
        _l1 = get_semantic_cache()
        _l1_hit = False
        _l1_sim = None
        if _l1 is not None:
            _l1r = _l1.lookup(prompt)
            if _l1r:
                _l1_hit = bool(_l1r.get("hit"))
                _l1_sim = _l1r.get("similarity")
        if not _l1_hit:
            return None  # 只在 L1 命中后并调 L2（任务书口径）
        _l2r = lookup_l2(prompt, origin=origin)
        _l2_would_hit = bool(_l2r and _l2r.get("response"))
        _consistent = (_l1_hit is True) and (_l2_would_hit is True)
        # ★316批刀2：加记 l2_store_size（内存层条目数）——区分「store 空」与「真不一致」。
        #   只读计数，不改路由/开关/不返回内容（守 L1 红线）。
        _l2_store_size = 0
        try:
            _l2 = get_semantic_cache_l2()
            _l2_store_size = _l2.size() if _l2 is not None else 0
        except Exception as e:
            silent_exc(e, "nucleus/llm/semantic_cache.py:lookup_l2_shadow.size", level="debug")
        _L.info(
            "[semantic_cache_l2_shadow] l1_hit=true l2_would_hit=%s "
            "l1_sim=%s l2_sim=%s consistent=%s l2_store_size=%s",
            _l2_would_hit, _l1_sim,
            (_l2r or {}).get("similarity"), _consistent, _l2_store_size,
        )
        return {"l1_hit": True, "l2_would_hit": _l2_would_hit,
                "l1_similarity": _l1_sim,
                "l2_similarity": (_l2r or {}).get("similarity"),
                "consistent": _consistent,
                "l2_store_size": _l2_store_size}
    except Exception as e:
        silent_exc(e, "nucleus/llm/semantic_cache.py:lookup_l2_shadow", level="debug")
        return None
