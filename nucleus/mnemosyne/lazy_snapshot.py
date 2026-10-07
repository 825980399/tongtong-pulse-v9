# -*- coding: utf-8 -*-
"""
lazy_snapshot.py —— 懒加载快照

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 知识快照的懒加载与增量读取
机制: 基于LazySnapshotView类实现，包含10个核心方法
定位: 记忆性能层
"""

import json
import mmap
from collections import OrderedDict
from typing import Any, Optional
from collections.abc import Iterator

from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus._silent_except import silent_exc



def _skip_ws(mm: mmap.mmap, i: int, n: int) -> int:
    while i < n and mm[i:i + 1].isspace():
        i += 1
    return i


def _find_value_span(mm: mmap.mmap, i: int, n: int):
    """返回覆盖一个完整 JSON 值的 (start, end_exclusive) 字节区间。

    支持字符串（含转义）、对象/数组（括号深度配对，尊重字符串内字符）、
    以及标量（数字 / true / false / null）。"""
    i = _skip_ws(mm, i, n)
    if i >= n:
        return None
    c = mm[i:i + 1]
    if c == b'"':
        # 字符串：扫描到未转义的闭合引号
        j = i + 1
        while j < n:
            d = mm[j:j + 1]
            if d == b'\\':
                j += 2
                continue
            if d == b'"':
                j += 1
                break
            j += 1
        return (i, j)
    if c in (b'{', b'['):
        open_ch = c
        close_ch = b'}' if c == b'{' else b']'
        depth = 0
        j = i
        instr = False
        esc = False
        while j < n:
            d = mm[j:j + 1]
            if esc:
                esc = False
            elif d == b'\\':
                esc = True
            elif d == b'"':
                instr = not instr
            elif not instr:
                if d == open_ch:
                    depth += 1
                elif d == close_ch:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        return (i, j)
            j += 1
        return (i, n)
    # 标量（数字 / true / false / null）：标准 JSON 中标量段连续无空白，
    # 扫描到结构分隔符为止（已 _skip_ws，不会以引号起始）。
    j = i
    while j < n and mm[j:j + 1] not in (b',', b'}', b']'):
        j += 1
    return (i, j)


def _iter_snapshot(mm: mmap.mmap, array_key: bytes = b"nodes"):
    """单次扫描顶层 JSON 对象。

    Returns:
        (metadata: dict, spans: list[(nid_or_None, (start, end))], index: dict[nid->(start,end)])
      - metadata：除 nodes 外的所有顶层键值（version / node_count_at_save /
        node_list_checksum / inference_cache / extra_state 等，无论位于 nodes 前后）。
      - spans：按文件顺序排列的节点切片区间；nid 为 None 表示节点缺 node_id。
      - index：node_id -> 切片区间，供 get_node 直接定位。
    """
    n = len(mm)
    i = _skip_ws(mm, 0, n)
    if mm[i:i + 1] != b'{':
        raise ValueError("快照顶层不是 JSON 对象")
    i = i + 1  # 进入顶层对象
    metadata: dict[str, Any] = {}
    spans: list[tuple[Optional[str], tuple[int, int]]] = []
    index: OrderedDict[str, tuple[int, int]] = OrderedDict()

    while True:
        i = _skip_ws(mm, i, n)
        if i >= n or mm[i:i + 1] == b'}':
            break
        if mm[i:i + 1] != b'"':
            # 容错：遇到非字符串键，停止解析避免误吞
            break
        key_span = _find_value_span(mm, i, n)
        key = mm[key_span[0] + 1:key_span[1] - 1].decode("utf-8", "replace")
        i = key_span[1]
        i = _skip_ws(mm, i, n)
        if mm[i:i + 1] == b':':
            i += 1
        i = _skip_ws(mm, i, n)

        if key.encode("utf-8") == array_key:
            if mm[i:i + 1] != b'[':
                # 非数组（异常格式），按普通值跳过
                vspan = _find_value_span(mm, i, n)
                i = vspan[1]
            else:
                i += 1  # 进入数组
                while True:
                    i = _skip_ws(mm, i, n)
                    if i >= n or mm[i:i + 1] == b']':
                        if mm[i:i + 1] == b']':
                            i += 1
                        break
                    vspan = _find_value_span(mm, i, n)
                    raw = mm[vspan[0]:vspan[1]].decode("utf-8", "replace")
                    try:
                        node_dict = json.loads(raw)
                        nid = node_dict.get("node_id")
                    except Exception:
                        nid = None
                        node_dict = {}
                    nid_key = nid if isinstance(nid, str) else None
                    spans.append((nid_key, (vspan[0], vspan[1])))
                    if nid_key is not None and nid_key not in index:
                        index[nid_key] = (vspan[0], vspan[1])
                    i = vspan[1]
                    i = _skip_ws(mm, i, n)
                    if mm[i:i + 1] == b',':
                        i += 1
                        continue
                    if mm[i:i + 1] == b']':
                        i += 1
                        break
        else:
            vspan = _find_value_span(mm, i, n)
            try:
                metadata[key] = json.loads(
                    mm[vspan[0]:vspan[1]].decode("utf-8", "replace"))
            except Exception:
                metadata[key] = None
            i = vspan[1]

        i = _skip_ws(mm, i, n)
        if mm[i:i + 1] == b',':
            i += 1
    return metadata, spans, index


class LazySnapshotView:
    """知识快照的惰性视图：流式读取 + LRU 缓存。

    默认 max_cache=1000 个反序列化节点。get_node 按需 seek 单节点反序列化，
    iter_nodes 顺序流式产出全部节点（同样受 LRU 上限约束，超出部分逐出）。
    """

    DEFAULT_MAX_CACHE = 1000

    def __init__(self, snapshot_path: str, max_cache: int = DEFAULT_MAX_CACHE):
        self.path = snapshot_path
        self.max_cache = max(1, int(max_cache))
        self._metadata: dict[str, Any] = {}
        self._spans: list[tuple[Optional[str], tuple[int, int]]] = []
        self._index: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._cache: OrderedDict[str, PulseNode] = OrderedDict()
        self._file = None
        self._mm: Optional[mmap.mmap] = None
        self._build()

    def _build(self):
        # ★主线第30批 T4：此处**有意不使用 with** —— 句柄需与 mmap 同生命周期存活，
        #   由 `_close_mm()` 统一释放；改为 with 会在 `_build()` 返回时关闭文件使 mmap 失效。
        self._file = open(self.path, "rb")  # noqa: SIM115 - 见上，句柄需长于本函数
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            md, spans, index = _iter_snapshot(self._mm, b"nodes")
        except Exception:
            self._close_mm()
            raise
        self._metadata = md
        self._spans = spans
        self._index = index

    def _close_mm(self):
        try:
            if self._mm is not None:
                self._mm.close()
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.lazy_snapshot::_close_mm L205")
        try:
            if self._file is not None:
                self._file.close()
        except Exception as e:
            silent_exc(e, where="nucleus.mnemosyne.lazy_snapshot::_close_mm L210")
        self._mm = None
        self._file = None

    def __del__(self):
        self._close_mm()

    def close(self):
        """释放 mmap 与文件句柄。Windows 下需先 close 才能删除快照文件。"""
        self._cache.clear()
        self._close_mm()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _parse_span(self, span: tuple[int, int]) -> PulseNode:
        start, end = span
        if self._mm is not None:
            raw = self._mm[start:end].decode("utf-8", "replace")
        else:
            with open(self.path, "rb") as fh:
                mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    raw = mm[start:end].decode("utf-8", "replace")
                finally:
                    mm.close()
        # ★主线第10批 T4.3：单个节点 JSON 损坏不应炸掉整个惰性视图（降级返回 None）
        try:
            return PulseNode.from_dict(json.loads(raw))
        except (ValueError, OSError) as _je:
            print(f"[WARNING] lazy_snapshot 节点解析失败(span={span}): "
                  f"{type(_je).__name__}: {_je}")
            return None

    def get_node(self, nid: str) -> Optional[PulseNode]:
        """按 node_id 取单个节点；命中 LRU 直接返回，未命中按需反序列化并缓存。"""
        if nid in self._cache:
            self._cache.move_to_end(nid)
            return self._cache[nid]
        span = self._index.get(nid)
        if span is None:
            return None
        node = self._parse_span(span)
        if node is None:
            # 损坏节点不缓存（避免占 LRU），返回 None 由调用方按缺失处理
            return None
        self._cache[nid] = node
        self._cache.move_to_end(nid)
        while len(self._cache) > self.max_cache:
            self._cache.popitem(last=False)
        return node

    def iter_nodes(self) -> Iterator[PulseNode]:
        """顺序流式产出全部节点（受 LRU 上限约束，逐出最久未用）。"""
        for _nid, span in self._spans:
            node = self._parse_span(span)
            if node is None:
                # 损坏节点跳过（不缓存、不产出），保持迭代可继续
                continue
            if _nid is not None:
                self._cache[_nid] = node
                self._cache.move_to_end(_nid)
                while len(self._cache) > self.max_cache:
                    self._cache.popitem(last=False)
            yield node

    def __iter__(self) -> Iterator[PulseNode]:
        """★167批 C1：补齐迭代协议，使 ``for node in view`` 可用（与 iter_nodes 等价）。
        不改动 metadata() / node_ids() 语义。
        """
        return self.iter_nodes()

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    @property
    def node_ids(self) -> list[str]:
        return list(self._index.keys())

    def __len__(self) -> int:
        return len(self._spans)

    def to_list(self) -> list[PulseNode]:
        """惰性物化：逐节点流式反序列化并收集为列表（无 318MB 中间对象）。"""
        return list(self.iter_nodes())


def stream_nodes(path: str) -> Iterator[PulseNode]:
    """独立生成器：逐节点流式产出（不建索引、不缓存），适合一次性遍历大快照。"""
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            _md, spans, _idx = _iter_snapshot(mm, b"nodes")
        finally:
            mm.close()
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for _nid, (s, e) in spans:
                yield PulseNode.from_dict(
                    json.loads(mm[s:e].decode("utf-8", "replace")))
        finally:
            mm.close()
