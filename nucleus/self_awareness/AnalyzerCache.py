"""405批刀1：自我认知分析器的分片缓存 + 运行账本。

两块独立能力，互不依赖：

1. :class:`AnalyzerCache` —— 以 ``(文件, mtime_ns, size)`` 为键的分析结果缓存，
   落 ``data/self_awareness/analyzer_cache.json``，TTL 默认 24h。
   文件未变更 ⇒ 直接复用上次的采集快照，跳过 AST 解析。
2. :func:`log_analyzer_run` —— 每次分析器运行落一行 JSON 到
   ``data/self_awareness/analyzer_runs.jsonl``（按天切分，保留 30 天）。

设计红线
--------
本模块**任何**失败都不得影响分析器本身：

* 缓存读不到 = 全未命中（等价于没有缓存）；
* 缓存写不进 = 只记 DEBUG，下次照常重算；
* 账本写不进 = 只记 DEBUG。

绝不因为缓存或账本抛异常而中断自我认知。这条红线是刻意的：缓存是加速手段，
不是正确性依赖。

为什么键里要带 ``size``
------------------------
只看 mtime 会漏掉「同一秒内改文件、长度不变」的原地改写（某些编辑器/脚本
``open(w)`` 会保留 mtime 精度到秒）。``(mtime_ns, size)`` 双键把这类幻觉
压到极低；真出了幻觉也只是**复用一份旧快照**，不会崩——Pass2 仍按当前
``_defs`` 全量重解析，最坏结果是这个文件的边少几条。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_logger = get_module_logger("AnalyzerCache")

#: 缓存文件相对项目根的默认路径
_CACHE_RELPATH = os.path.join("data", "self_awareness", "analyzer_cache.json")
#: 运行账本相对项目根的默认目录
_RUNS_RELDIR = os.path.join("data", "self_awareness")
#: 缓存格式版本（结构变更时递增，旧文件整体作废）
_CACHE_VERSION = 1
#: 默认 TTL：24 小时
_DEFAULT_TTL_SEC = 86400.0
#: 运行账本保留天数
_DEFAULT_KEEP_DAYS = 30


def _project_root() -> str:
    """项目根目录（``nucleus/self_awareness/xxx.py`` 上溯 3 层）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def file_sig(abs_path: str) -> list | None:
    """文件签名 ``[mtime_ns, size]``；取不到 stat 时返回 None（该文件永不缓存）。

    返回 None 的语义是**不缓存也不复用**——stat 失败的文件（权限、瞬时删除）
    每次都老实重算，不给缓存留"猜"的余地。
    """
    try:
        _st = os.stat(abs_path)
        return [int(_st.st_mtime_ns), int(_st.st_size)]
    except OSError as _e:
        silent_exc(_e, where="AnalyzerCache::file_sig", level="debug")
        return None


def tree_sig(paths: list) -> list | None:
    """多路径数据源签名 —— 用于「整份报告」级缓存（如知识质量）。

    与 :func:`file_sig` 的分工：``file_sig`` 管单个 .py 文件的逐文件缓存，
    ``tree_sig`` 管「一组数据源文件没变 ⇒ 整份分析结果照样有效」。

    对目录项递归收集 ``*.parquet``（知识质量的实际数据源形态），对文件项
    直接取签名；把全部 ``(相对名, mtime_ns, size)`` 拼串取 md5。

    Returns:
        ``[md5_hex, 参与文件数]``；一个文件都没取到时返回 None（不缓存）。
    """
    import glob
    import hashlib
    _items: list[tuple[str, int, int]] = []
    for _p in (paths or []):
        if not _p:
            continue
        try:
            if os.path.isdir(_p):
                for _f in sorted(glob.glob(os.path.join(_p, "**", "*.parquet"),
                                           recursive=True)):
                    _s = file_sig(_f)
                    if _s is not None:
                        _items.append((os.path.basename(_f), _s[0], _s[1]))
            elif os.path.isfile(_p):
                _s = file_sig(_p)
                if _s is not None:
                    _items.append((os.path.basename(_p), _s[0], _s[1]))
        except Exception as _e:      # 单个路径出问题不影响其余路径的签名
            silent_exc(_e, where="AnalyzerCache::tree_sig", level="debug")
    if not _items:
        return None
    _items.sort()
    _h = hashlib.md5("|".join("%s:%d:%d" % _x for _x in _items).encode("utf-8"))
    return [_h.hexdigest(), len(_items)]


class AnalyzerCache:
    """``(文件, mtime_ns, size)`` → 分析结果的进程内缓存，可落盘复用。

    线程安全：内部用一把 ``RLock`` 保护 ``_entries``。分析器可能跑在
    2 个 worker 的线程池里，缓存必须能并发读写。
    """

    def __init__(self, path: str | None = None,
                 ttl_sec: float | None = None) -> None:
        self._lock = threading.RLock()
        self._path = path or os.path.join(_project_root(), _CACHE_RELPATH)
        self._ttl = float(ttl_sec if ttl_sec is not None
                          else _cache_ttl_sec(_DEFAULT_TTL_SEC))
        #: key -> {"sig": [mtime_ns, size], "ts": float, "value": Any}
        self._entries: dict[str, dict[str, Any]] = {}
        self._hit = 0
        self._miss = 0
        self._dirty = False
        self._loaded = False

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def get(self, key: str, sig: list | None) -> Any:
        """取缓存值；``sig`` 不匹配 / 过期 / 未命中 均返回 None。"""
        if sig is None:
            return None
        with self._lock:
            _ent = self._entries.get(key)
            if not isinstance(_ent, dict):
                self._miss += 1
                return None
            if list(_ent.get("sig") or []) != list(sig):
                self._miss += 1
                return None
            _ts = float(_ent.get("ts") or 0.0)
            # 时钟回拨（NTP 校正 / 手动改表）会把 age 算成负数 ⇒ 按过期处理，
            # 宁可重算一次，也不要永远复用一份未来时间戳的快照。
            _age = time.time() - _ts
            if _age < 0 or _age > self._ttl:
                self._miss += 1
                return None
            self._hit += 1
            return _ent.get("value")

    def put(self, key: str, sig: list | None, value: Any) -> bool:
        """写入缓存条目；``sig`` 或 ``value`` 为 None 时不写（返回 False）。"""
        if sig is None or value is None:
            return False
        with self._lock:
            self._entries[key] = {"sig": list(sig), "ts": time.time(),
                                  "value": value}
            self._dirty = True
        return True

    def stats(self) -> dict[str, Any]:
        """命中统计与条目数（供 analyzer_runs 账本记录）。"""
        with self._lock:
            _total = self._hit + self._miss
            return {
                "hit": self._hit,
                "miss": self._miss,
                "entries": len(self._entries),
                "hit_rate": round(self._hit / _total, 4) if _total else 0.0,
                "ttl_sec": self._ttl,
            }

    def reset_stats(self) -> None:
        """归零命中计数（跨轮累计会失去"这一轮命中多少"的可读性）。"""
        with self._lock:
            self._hit = 0
            self._miss = 0

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def load(self, force: bool = False) -> int:
        """从磁盘加载缓存；返回加载的条目数（失败/不存在返回 0）。"""
        with self._lock:
            if self._loaded and not force:
                return len(self._entries)
            self._loaded = True
        try:
            if not os.path.isfile(self._path):
                return 0
            with open(self._path, encoding="utf-8") as _f:
                _raw = json.load(_f)
        except Exception as _e:  # 缓存坏了就当没有——绝不阻断分析器
            _logger.warning("[405刀1] 分析器缓存读取失败（按全未命中处理）: %s: %s",
                            type(_e).__name__, _e)
            return 0
        if not isinstance(_raw, dict) or _raw.get("version") != _CACHE_VERSION:
            _logger.debug("[405刀1] 分析器缓存版本不符（%s != %s），整体作废",
                          (_raw or {}).get("version") if isinstance(_raw, dict)
                          else type(_raw).__name__, _CACHE_VERSION)
            return 0
        _ents = _raw.get("entries")
        if not isinstance(_ents, dict):
            return 0
        _now = time.time()
        _kept = 0
        with self._lock:
            for _k, _v in _ents.items():
                if not isinstance(_v, dict):
                    continue
                _ts = float(_v.get("ts") or 0.0)
                _age = _now - _ts
                if _age < 0 or _age > self._ttl:
                    continue        # 过期条目直接丢弃，不进内存
                self._entries[str(_k)] = _v
                _kept += 1
        if _kept:
            _logger.info("[405刀1] 分析器缓存已加载: %d 条（共 %d，丢弃过期 %d）",
                         _kept, len(_ents), len(_ents) - _kept)
        return _kept

    def save(self, force: bool = False) -> bool:
        """写回磁盘（原子替换）；无变更且非 force 时跳过。"""
        with self._lock:
            if not self._dirty and not force:
                return True
            _payload = {
                "version": _CACHE_VERSION,
                "saved_at": time.time(),
                "ttl_sec": self._ttl,
                "entries": dict(self._entries),
            }
            self._dirty = False
        try:
            _dir = os.path.dirname(self._path)
            if _dir and not os.path.isdir(_dir):
                os.makedirs(_dir, exist_ok=True)
            _tmp = self._path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_payload, _f, ensure_ascii=False)
            os.replace(_tmp, self._path)
            _logger.info("[405刀1] 分析器缓存已落盘: %d 条 / %.1f MB → %s",
                         len(_payload["entries"]),
                         os.path.getsize(self._path) / 1024.0 / 1024.0,
                         self._path)
            return True
        except Exception as _e:
            _logger.warning("[405刀1] 分析器缓存落盘失败（不影响本次分析）: %s: %s",
                            type(_e).__name__, _e)
            with self._lock:
                self._dirty = True      # 失败后保持脏标记，下次再试
            return False

    def prune(self) -> int:
        """丢弃已过期条目；返回丢弃条数。"""
        _now = time.time()
        _drop = 0
        with self._lock:
            for _k in list(self._entries):
                _ts = float(self._entries[_k].get("ts") or 0.0)
                _age = _now - _ts
                if _age < 0 or _age > self._ttl:
                    self._entries.pop(_k, None)
                    _drop += 1
            if _drop:
                self._dirty = True
        return _drop


def _cache_ttl_sec(default: float) -> float:
    """读 ``config.ANALYZER_CACHE_TTL_SEC``；读不到用默认值。"""
    try:
        import config
        return float(getattr(config, "ANALYZER_CACHE_TTL_SEC", default))
    except Exception as _e:
        silent_exc(_e, where="AnalyzerCache::_cache_ttl_sec", level="debug")
        return default


# ----------------------------------------------------------------------
# 单例
# ----------------------------------------------------------------------
_CACHE_SINGLETON: AnalyzerCache | None = None
_CACHE_LOCK = threading.RLock()


def get_analyzer_cache() -> AnalyzerCache:
    """进程级缓存单例（首次取用时自动 ``load()``）。"""
    global _CACHE_SINGLETON
    with _CACHE_LOCK:
        if _CACHE_SINGLETON is None:
            _CACHE_SINGLETON = AnalyzerCache()
            _CACHE_SINGLETON.load()
        return _CACHE_SINGLETON


def peek_analyzer_cache() -> AnalyzerCache | None:
    """取**已存在**的缓存单例；没有则返回 None（**不创建、不加载**）。

    ★为什么要这个：缓存文件实测 13.7 MB，加载一次约 1s。若每轮
    `run_all_analyses` 都无条件 `get_analyzer_cache()`，那么**不含
    call_graph 的轮次**也要白付这笔加载开销。本函数供「只想看命中统计」
    的调用方使用——没有缓存就是没有，不替它造一个。
    """
    with _CACHE_LOCK:
        return _CACHE_SINGLETON


def reset_analyzer_cache(cache: AnalyzerCache | None = None) -> None:
    """测试用：替换/清空单例。传 None 表示下次重新创建。"""
    global _CACHE_SINGLETON
    with _CACHE_LOCK:
        _CACHE_SINGLETON = cache


# ----------------------------------------------------------------------
# 运行账本
# ----------------------------------------------------------------------
def _runs_dir() -> str:
    """运行账本目录（读 ``config.SELF_AWARENESS_OUTPUT_DIR``）。"""
    try:
        import config
        return os.path.join(
            _project_root(),
            str(getattr(config, "SELF_AWARENESS_OUTPUT_DIR",
                        _RUNS_RELDIR)).replace("/", os.sep))
    except Exception as _e:
        silent_exc(_e, where="AnalyzerCache::_runs_dir", level="debug")
        return os.path.join(_project_root(), _RUNS_RELDIR)


def log_analyzer_run(name: str, elapsed_ms: float, shards: int = 0,
                     cache_hit: bool = False, ok: bool = True,
                     extra: dict | None = None) -> bool:
    """落一行运行账本到 ``analyzer_runs.jsonl``（按天切分）。

    行格式（任务书 405 刀1 要求的最小子集 + 若干补充字段）：:

        {"ts": 1793..., "date": "20261031", "name": "call_graph",
         "elapsed_ms": 18234.5, "shards": 4, "cache_hit": false, "ok": true, ...}

    写失败只记 DEBUG —— 账本是观测手段，不能反过来成为故障源。
    """
    try:
        _d = _runs_dir()
        if not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        _now = time.time()
        _row = {
            "ts": round(_now, 3),
            "date": time.strftime("%Y%m%d", time.localtime(_now)),
            "name": str(name or ""),
            "elapsed_ms": round(float(elapsed_ms), 2),
            "shards": int(shards or 0),
            "cache_hit": bool(cache_hit),
            "ok": bool(ok),
        }
        if extra:
            for _k, _v in extra.items():
                if _k not in _row:
                    _row[_k] = _v
        _fp = os.path.join(_d, "analyzer_runs.jsonl")
        with open(_fp, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="AnalyzerCache::log_analyzer_run", level="debug")
        return False


def prune_analyzer_runs(keep_days: int | None = None) -> int:
    """清理 ``analyzer_runs.jsonl`` 中超过 ``keep_days`` 的行。

    注意：本函数只**删行**，不删文件；任务书「保留 30 天」指的是数据保留，
    文件本身按月累积无所谓。返回删除的行数。
    """
    _days = int(keep_days if keep_days is not None
                else _keep_days(_DEFAULT_KEEP_DAYS))
    try:
        _fp = os.path.join(_runs_dir(), "analyzer_runs.jsonl")
        if not os.path.isfile(_fp):
            return 0
        _cut = time.strftime("%Y%m%d",
                             time.localtime(time.time() - _days * 86400.0))
        _kept: list[str] = []
        _drop = 0
        with open(_fp, encoding="utf-8") as _f:
            for _line in _f:
                _s = _line.strip()
                if not _s:
                    continue
                try:
                    _row = json.loads(_s)
                except Exception as _e:     # 坏行保留原样，不因一行坏整个文件
                    silent_exc(_e, where="AnalyzerCache::prune_analyzer_runs",
                               level="debug")
                    _kept.append(_s)
                    continue
                if str(_row.get("date") or "") < _cut:
                    _drop += 1
                else:
                    _kept.append(_s)
        if _drop:
            with open(_fp, "w", encoding="utf-8") as _f:
                _f.write("\n".join(_kept) + ("\n" if _kept else ""))
        return _drop
    except Exception as _e:
        silent_exc(_e, where="AnalyzerCache::prune_analyzer_runs", level="debug")
        return 0


def _keep_days(default: int) -> int:
    """读 ``config.ANALYZER_RUNS_KEEP_DAYS``。"""
    try:
        import config
        return int(getattr(config, "ANALYZER_RUNS_KEEP_DAYS", default))
    except Exception as _e:
        silent_exc(_e, where="AnalyzerCache::_keep_days", level="debug")
        return default
