# -*- coding: utf-8 -*-
"""snapshot_index.py —— 知识快照持久索引（338 批刀1）

问题（338 前置实测）：`LazySnapshotView` 每次 open 都重扫整个 648.7MB 快照来建
`node_id -> (start, end)` 切片表（19087 条，实测 1.1s），而且每次启动还会全量重写
648MB 主档。索引不落盘 ⇒ 每次冷启动都白扫一遍。

本模块把「扫描结果」持久化到 `<snapshot>.index.json`，让打开的代价从
「扫 648MB」降到「读 1MB 索引 + 一次 stat」。

三条不变量（写死，别绕）：

1. **索引恒随主档失效**：主档被全量重写/compaction 后**主动删除索引**
   （`invalidate()`，见 `PulseSnapshot._m67_write_snapshot_streaming`）。
   因此不存在「新主档 + 旧切片」的错配——宁可多扫一次，不可读错偏移。
2. **物理身份免扫描，逻辑身份做审计**：`(file_size, mtime)` 是物理身份（一次 stat 即可判，
   不等就重扫）；`node_list_checksum` 是逻辑身份，落进索引头供审计与显式对表
   （调用方已知期望值时用 `load(expect_checksum=...)`）。二者职责不同，不要拿一个顶另一个。
3. **读不到 ≠ 是 0**：索引不可用时 `health()` 如实报 `status`（`index_missing` /
   `index_stale` / `scan_failed` / `index_hit` / `rescanned`），调用方据此走
   「本轮不判」而不是填 0。

原子写：`.tmp` + `os.replace`；写失败保留证据但不留 `.tmp`。
"""

import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc

#: 索引文件名后缀（紧跟主档名，例：pulse_knowledge_snapshot.json.index.json）
INDEX_SUFFIX = ".index.json"

#: 索引结构版本。结构变更必须升版本 ⇒ 旧索引自动失效（走重扫），不做就地迁移。
INDEX_SCHEMA = "338.idx.v1"

#: 主档 mtime 比对容差（秒）。浮点 mtime 跨文件系统序列化可能丢精度，故不用 ==。
_MTIME_EPS = 1e-6

#: 进程内健康状态登记：abspath -> dict(status=..., reason=..., ts=..., nodes=...)
#: 只记最近一次，供判据/报表读；不做跨进程持久化（那是索引文件自己的事）。
_HEALTH: dict[str, dict[str, Any]] = {}
_HEALTH_LOCK = threading.Lock()


def index_path(snapshot_path: str) -> str:
    """索引文件路径（与主档同目录、同名加后缀）。"""
    return str(snapshot_path) + INDEX_SUFFIX


def _versions() -> tuple[str, str]:
    """取 code_version / criteria_version（best-effort，取不到记 unknown）。

    为什么用 `sys.modules` 而不是 import：本模块在**快照打开的热路径**上，
    `self_inspector` / `CallGraphAnalyzer` 都是重模块，为盖个章去 import 它们
    会把「省下来的 1.1s」再赔回去。已经加载过的模块直接读属性，没加载就记 unknown
    ——章是给审计看的，不是判据，不值得为它付导入代价。
    """
    import sys

    _cv = "unknown"
    _crv = "unknown"
    _cga = sys.modules.get("nucleus.self_awareness.CallGraphAnalyzer")
    if _cga is not None:
        _cv = str(getattr(_cga, "CODE_VERSION", "") or "unknown")
    _si = sys.modules.get("nucleus.self_inspector")
    if _si is not None:
        _crv = str(getattr(_si, "_DEAD_CODE_CRITERIA_VERSION", "") or "unknown")
    return _cv, _crv


def _set_health(snapshot_path: str, status: str, reason: str = "", nodes: int = -1):
    """登记一次索引健康状态（不抛异常、不阻塞）。"""
    _key = os.path.abspath(str(snapshot_path)) if snapshot_path else ""
    if not _key:
        return
    _rec = {
        "status": status,
        "reason": reason,
        "nodes": nodes,
        "ts": time.time(),
        "index_path": index_path(snapshot_path),
    }
    with _HEALTH_LOCK:
        _HEALTH[_key] = _rec


def mark_scan_failed(snapshot_path: str, reason: str = ""):
    """登记「本轮重扫失败」——判据据此走「不判」，**不得填 0**。"""
    _set_health(snapshot_path, "scan_failed", reason or "重扫失败", nodes=-1)


def mark_rescanned(snapshot_path: str, nodes: int, reason: str = ""):
    """登记「本轮重扫成功并已回写索引」。"""
    _set_health(snapshot_path, "rescanned", reason or "索引未命中，已重扫", nodes=int(nodes))


def judge_available(snapshot_path: str) -> tuple[bool, str]:
    """判据可用性闸门：`(可判?, 原因)`。

    调用方拿到 `False` 时必须按「本轮不判」处理（沿用 330刀2 的拒绝路径），
    **不得**把不可用读成 0——「没读到」和「真的是 0」是两件事。
    """
    _h = health(snapshot_path)
    return bool(_h.get("available")), str(_h.get("status")) + ":" + str(_h.get("reason"))


def health(snapshot_path: str) -> dict[str, Any]:
    """读最近一次索引健康状态。

    Returns:
        dict: `{status, reason, nodes, ts, index_path, available}`。
        `available=False` 表示**本轮拿不到可信切片表**，判据应走「不判」而不是填 0。
        从未登记过 ⇒ `status="unknown"` / `available=False`（不猜成可用）。
    """
    _key = os.path.abspath(str(snapshot_path)) if snapshot_path else ""
    with _HEALTH_LOCK:
        _rec = dict(_HEALTH.get(_key) or {})
    if not _rec:
        return {"status": "unknown", "reason": "尚未发生过索引构建/加载",
                "nodes": -1, "ts": 0.0,
                "index_path": index_path(snapshot_path), "available": False}
    _rec["available"] = _rec.get("status") in ("index_hit", "rescanned")
    return _rec


def build_payload(snapshot_path: str, metadata: dict, spans: list) -> dict:
    """把一次扫描的结果组装成索引 payload（纯函数，不做 IO）。

    Args:
        snapshot_path: 主档路径（用于取 stat 与 basename）。
        metadata: `_iter_snapshot` 返回的顶层元数据（含 `node_list_checksum`）。
        spans: `[(nid_or_None, (start, end)), ...]`，保持文件顺序。
    """
    _st = os.stat(snapshot_path)
    _cv, _crv = _versions()
    _md = metadata or {}
    # 只存 `[nid, start, end]` 三元组，不另存 nid->span 字典：
    # 19087 条的字典复制到磁盘约多一倍字节，而内存里重建一遍只要几毫秒。
    _spans = []
    for _nid, _span in (spans or []):
        try:
            _spans.append([_nid, int(_span[0]), int(_span[1])])
        except (TypeError, IndexError, ValueError) as _e:
            silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::build_payload 切片编码")
    return {
        "schema": INDEX_SCHEMA,
        "snapshot_basename": os.path.basename(str(snapshot_path)),
        "file_size": int(_st.st_size),
        "mtime": float(_st.st_mtime),
        "node_list_checksum": str(_md.get("node_list_checksum", "") or ""),
        "code_version": _cv,
        "criteria_version": _crv,
        "node_count": len(_spans),
        "built_at": time.time(),
        "spans": _spans,
        # 元数据整份带上：命中索引时无需再扫文件即可回答 metadata 属性
        # （inference_cache / extra_state / node_list_checksum 都在里面）。
        "metadata": _md,
    }


def save(snapshot_path: str, payload: dict) -> bool:
    """原子写索引：`.tmp` + `os.replace`。失败只留痕不抛。"""
    _ip = index_path(snapshot_path)
    _tmp = _ip + ".tmp"
    try:
        _d = os.path.dirname(_ip)
        if _d and not os.path.exists(_d):
            os.makedirs(_d, exist_ok=True)
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(payload, _f, ensure_ascii=False, separators=(",", ":"))
            _f.flush()
            os.fsync(_f.fileno())
        os.replace(_tmp, _ip)
        return True
    except (OSError, TypeError, ValueError) as _e:
        silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::save 原子写")
        # 清残留 .tmp：验收⑤要求"无 .tmp 残留"，写失败也不许留。
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except OSError as _e2:
            silent_exc(_e2, where="nucleus.mnemosyne.snapshot_index::save 清理 .tmp")
        return False


def invalidate(snapshot_path: str, reason: str = "") -> bool:
    """删除索引（主档即将/已经变更时调用）。

    这是**正确性的关键一步**：主档重写后索引里的偏移全部失效，
    留着它就会拿旧偏移读新文件——静默读到错节点比慢 1.1 秒危险得多。
    """
    _ip = index_path(snapshot_path)
    _removed = False
    try:
        if os.path.exists(_ip):
            os.remove(_ip)
            _removed = True
    except OSError as _e:
        silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::invalidate 删除索引")
    _set_health(snapshot_path, "index_missing",
                reason or "索引已失效（主档变更）", nodes=-1)
    return _removed


def load(snapshot_path: str, expect_checksum: str | None = None) -> dict | None:
    """加载并校验索引；不可用返回 None（调用方据此重扫）。

    校验顺序（先便宜后贵）：
        1. 文件存在 / 可解析 / schema 版本一致；
        2. **物理身份**：file_size 完全相等 且 mtime 在容差内；
        3. **逻辑身份**（可选）：调用方给了 expect_checksum 时必须相等。

    任一步不过 ⇒ 记 health 并返回 None。**不回退猜测、不用旧索引硬读。**
    """
    _ip = index_path(snapshot_path)
    if not os.path.exists(_ip):
        _set_health(snapshot_path, "index_missing", "索引文件不存在", nodes=-1)
        return None
    _payload = None
    try:
        with open(_ip, "r", encoding="utf-8") as _f:
            _payload = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::load 解析索引")
        _set_health(snapshot_path, "index_stale", "索引解析失败", nodes=-1)
        return None
    if not isinstance(_payload, dict):
        _set_health(snapshot_path, "index_stale", "索引结构非对象", nodes=-1)
        return None
    if str(_payload.get("schema", "")) != INDEX_SCHEMA:
        _set_health(snapshot_path, "index_stale",
                    "索引结构版本不符（%r ≠ %r）"
                    % (_payload.get("schema"), INDEX_SCHEMA), nodes=-1)
        return None
    try:
        _st = os.stat(snapshot_path)
    except OSError as _e:
        silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::load stat 主档")
        _set_health(snapshot_path, "scan_failed", "主档 stat 失败", nodes=-1)
        return None
    if int(_payload.get("file_size", -1)) != int(_st.st_size):
        _set_health(snapshot_path, "index_stale",
                    "file_size 不符（索引 %s ≠ 现档 %s）"
                    % (_payload.get("file_size"), _st.st_size), nodes=-1)
        return None
    if abs(float(_payload.get("mtime", 0.0) or 0.0) - float(_st.st_mtime)) > _MTIME_EPS:
        _set_health(snapshot_path, "index_stale",
                    "mtime 不符（索引 %.3f ≠ 现档 %.3f）"
                    % (float(_payload.get("mtime", 0.0) or 0.0), float(_st.st_mtime)),
                    nodes=-1)
        return None
    if expect_checksum:
        _got = str(_payload.get("node_list_checksum", "") or "")
        if _got != str(expect_checksum):
            _set_health(snapshot_path, "index_stale",
                        "node_list_checksum 不符（索引 %r ≠ 期望 %r）"
                        % (_got, expect_checksum), nodes=-1)
            return None
    _n = int(_payload.get("node_count", 0) or 0)
    _set_health(snapshot_path, "index_hit", "物理身份与 schema 均匹配", nodes=_n)
    return _payload


def spans_of(payload: dict) -> tuple[list, "object"]:
    """把 payload 还原成 `(spans, index)`，供 LazySnapshotView 直接使用。

    Returns:
        spans: `[(nid_or_None, (start, end)), ...]`（文件顺序）
        index: `OrderedDict[nid, (start, end)]`（仅收非 None 且首次出现的 nid，
               与 `_iter_snapshot` 的同名去重语义保持一致）
    """
    from collections import OrderedDict

    _spans: list = []
    _index: OrderedDict = OrderedDict()
    for _row in (payload.get("spans") or []):
        try:
            _nid, _s, _e = _row[0], int(_row[1]), int(_row[2])
        except (TypeError, IndexError, ValueError) as _e:
            silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::spans_of 解码")
            continue
        _nid_key = _nid if isinstance(_nid, str) else None
        _spans.append((_nid_key, (_s, _e)))
        if _nid_key is not None and _nid_key not in _index:
            _index[_nid_key] = (_s, _e)
    return _spans, _index


def stats(snapshot_path: str) -> dict[str, Any]:
    """只读统计：索引/主档体量、是否命中、可省下多少扫描量。供报表与验收取证用。"""
    _ip = index_path(snapshot_path)
    _sz = -1
    _exists = os.path.exists(_ip)
    if _exists:
        try:
            _sz = os.path.getsize(_ip)
        except OSError as _e:
            silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::stats 取索引体量")
    _ssz = -1
    try:
        if os.path.exists(snapshot_path):
            _ssz = os.path.getsize(snapshot_path)
    except OSError as _e:
        silent_exc(_e, where="nucleus.mnemosyne.snapshot_index::stats 取主档体量")
    _h = health(snapshot_path)
    return {
        "snapshot_path": str(snapshot_path),
        "snapshot_size": _ssz,
        "index_path": _ip,
        "index_exists": _exists,
        "index_size": _sz,
        "index_ratio": (round(_sz / _ssz, 6) if (_exists and _ssz > 0) else None),
        "status": _h.get("status"),
        "available": _h.get("available"),
        "nodes": _h.get("nodes"),
    }
