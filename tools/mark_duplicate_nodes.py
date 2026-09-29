"""mark_duplicate_nodes —— 知识库重复节点扫描与标记工具（只标记不删除）

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月9日

用法::

    # 预演（默认，不写文件）：扫描旧路径下的重复节点
    python tools/mark_duplicate_nodes.py --path-prefix /自我/架构/五维权重 \\
        --legacy-prefix /自我/架构/五维权重

    # 实际标记（自动备份 .bak_batch12）
    python tools/mark_duplicate_nodes.py --path-prefix /自我/架构/五维权重 --apply

    # 全库扫描（较慢，用倒排索引找候选）
    python tools/mark_duplicate_nodes.py --all --apply

为什么必须流式
--------------
主快照 `data/knowledge/pulse_knowledge_snapshot.json` 已 **300MB+**，
`json.load` 一次会吃掉数 GB 内存。本工具用「结构字符跳转」逐节点解析：
只在 `[ ] { } "` 与转义符处停下，正文用正则一次性跳过，
因此内存占用与单个节点大小同量级，与快照总大小无关。

⚠ 落盘前请确认框架已停止：运行中的框架持有内存节点池并按约 10 分钟全量回写，
   在其运行期间写入快照会被下一次保存**整体覆盖**（标记丢失）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from typing import Any
from collections.abc import Iterator

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.knowledge.DuplicateNodeDetector import (  # noqa: E402
    DuplicateNodeDetector,
    FLAG_DUPLICATE,
)

DEFAULT_SNAPSHOT = os.path.join(
    _PROJECT_ROOT, "data", "knowledge", "pulse_knowledge_snapshot.json")

# 结构字符：数组/对象边界、字符串引号、转义符
_STRUCT_RE = re.compile(rb'[\[\]{}"\\]')


def _locate_nodes_array(f) -> int:
    """定位顶层 "nodes" 数组起始的 '[' 之后的位置（字节偏移）。"""
    f.seek(0)
    _buf = f.read(1 << 20)
    _i = _buf.find(b'"nodes"')
    if _i < 0:
        raise ValueError('快照中未找到 "nodes" 字段')
    _j = _buf.find(b"[", _i + 7)
    if _j < 0:
        raise ValueError('"nodes" 后未找到 [')
    return _j + 1


def iter_node_spans(path: str) -> Iterator[tuple[int, int, bytes]]:
    """流式产出每个节点的 (start, end, raw_bytes)。

    只在结构字符处停下判断，正文用正则跳过 —— 324MB 快照也能秒级扫完。
    """
    with open(path, "rb") as f:
        _pos = _locate_nodes_array(f)
        f.seek(_pos)
        _depth = 0
        _in_str = False
        _elem_start = None
        _buf = b""
        _buf_off = _pos          # _buf[0] 对应的绝对偏移
        _i = 0

        while True:
            _chunk = f.read(1 << 22)          # 4MB
            if not _chunk:
                break
            # ★关键：节点可能横跨分片边界。若当前正处于某个节点内部，
            #   必须保留从 _elem_start 起的全部字节并继续从 _i 扫描；
            #   否则可以丢掉已消费的前缀，避免缓冲无限增长。
            if _elem_start is not None:
                _cut = max(0, _elem_start - _buf_off)
                if _cut:
                    _buf = _buf[_cut:] + _chunk
                    _buf_off += _cut
                    _i = max(0, _i - _cut)
                else:
                    _buf = _buf + _chunk
            else:
                _buf = _buf[_i:] + _chunk
                _buf_off += _i
                _i = 0
            _n = len(_buf)
            while _i < _n:
                _m = _STRUCT_RE.search(_buf, _i)
                if _m is None:
                    _i = _n
                    break
                _k = _m.start()
                _ch = _buf[_k:_k + 1]
                if _in_str:
                    if _ch == b"\\":
                        _i = _k + 2          # 跳过被转义的字符
                        continue
                    if _ch == b'"':
                        _in_str = False
                    _i = _k + 1
                    continue
                if _ch == b'"':
                    _in_str = True
                    _i = _k + 1
                    continue
                if _ch in (b"{", b"["):
                    if _ch == b"{" and _depth == 0 and _elem_start is None:
                        _elem_start = _buf_off + _k
                    _depth += 1
                elif _ch in (b"}", b"]"):
                    _depth -= 1
                    if _depth == 0 and _elem_start is not None:
                        yield (_elem_start, _buf_off + _k + 1,
                               _buf[_elem_start - _buf_off:_k + 1])
                        _elem_start = None
                    if _depth < 0:
                        return
                _i = _k + 1


def iter_snapshot_nodes(path: str) -> Iterator[dict[str, Any]]:
    """流式产出节点 dict（单节点解析，内存恒定）。"""
    for _s, _e, _raw in iter_node_spans(path):
        try:
            yield json.loads(_raw.decode("utf-8"))
        except Exception:
            continue


def mark_and_rewrite(path: str, marks: dict[str, dict[str, Any]],
                     backup_suffix: str = ".bak_batch12") -> int:
    """把 marks（node_id → 待写字段）流式写回快照，返回被改写节点数。

    采用「边读边写」：除目标节点重新 dump 外，其余字节原样拷贝，
    避免把 300MB+ 整体载入内存。写完用 os.replace 原子替换。
    """
    if not marks:
        return 0
    _bak = path + backup_suffix
    if not os.path.exists(_bak):
        shutil.copy2(path, _bak)
        print(f"[备份] 已生成 {_bak}")
    else:
        print(f"[备份] 已存在 {_bak}，跳过覆盖")

    _tmp = path + ".tmp_batch12"
    _done = 0
    with open(path, "rb") as _src, open(_tmp, "wb") as _dst:
        _cursor = 0
        for _s, _e, _raw in iter_node_spans(path):
            try:
                _node = json.loads(_raw.decode("utf-8"))
            except Exception:
                continue
            _fields = marks.get(str(_node.get("node_id", "")))
            if not _fields:
                continue
            _dst.write(_src.read(_s - _cursor))     # 拷贝节点前的原字节
            _node.update(_fields)
            _dst.write(json.dumps(_node, ensure_ascii=False).encode("utf-8"))
            _src.seek(_e)
            _cursor = _e
            _done += 1
        _dst.write(_src.read())                      # 拷贝尾部
    os.replace(_tmp, path)
    return _done


def main() -> int:
    p = argparse.ArgumentParser(description="知识库重复节点扫描与标记（只标记不删除）")
    p.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="快照路径")
    p.add_argument("--path-prefix", default="/自我/架构/五维权重",
                   help="待检测（重复方候选）路径前缀")
    p.add_argument("--legacy-prefix", action="append", default=None,
                   help="旧路径前缀（可重复传入）；分组内优先把旧路径节点判为重复方")
    p.add_argument("--all", action="store_true", help="全库扫描（忽略 --path-prefix）")
    p.add_argument("--threshold", type=float, default=0.8, help="Jaccard 重复阈值")
    p.add_argument("--apply", action="store_true",
                   help="实际写回标记（默认仅预演）")
    args = p.parse_args()

    _legacy = args.legacy_prefix or ([args.path_prefix] if args.path_prefix else [])
    _prefix = None if args.all else args.path_prefix

    print("=" * 68)
    print(f"[模式] {'APPLY（写回并备份）' if args.apply else 'DRY-RUN（仅扫描，不写文件）'}")
    print(f"[快照] {args.snapshot}")
    print(f"[范围] {'全库' if args.all else args.path_prefix}")
    print(f"[旧路径] {_legacy}   [阈值] Jaccard >= {args.threshold}")
    print("=" * 68)

    _t0 = time.time()
    _nodes = list(iter_snapshot_nodes(args.snapshot))
    print(f"[扫描] 读到 {len(_nodes)} 个节点，耗时 {time.time() - _t0:.1f}s")

    _det = DuplicateNodeDetector(threshold=args.threshold)
    _groups = _det.find_groups(_nodes, path_prefix=_prefix, legacy_prefixes=_legacy)

    if not _groups:
        print("\n未发现重复节点。")
        return 0

    print(f"\n发现 {len(_groups)} 组重复：")
    _marks: dict[str, dict[str, Any]] = {}
    for _i, g in enumerate(_groups, 1):
        _keep, _dups = g["keep"], g["duplicates"]
        print(f"\n  组{_i}  相似度={g['similarity']}")
        print(f"    保留: {_keep.get('node_id')}  {_keep.get('space_path')}")
        print(f"           {str(_keep.get('value'))[:60]}")
        for d in _dups:
            print(f"    重复: {d.get('node_id')}  {d.get('space_path')}")
            print(f"           {str(d.get('value'))[:60]}")
            _marks[str(d.get("node_id"))] = {
                "quality_flag": FLAG_DUPLICATE,
                "quality_reason": f"D1:重复节点(与 {_keep.get('node_id')} 内容重复)",
                "duplicate_of": str(_keep.get("node_id")),
            }

    if not args.apply:
        print("\n" + "=" * 68)
        print(f"预演完成：{len(_marks)} 个节点待标记（只标记不删除）。")
        print("确认无误后加 --apply 实际写回（会自动备份 .bak_batch12）。")
        print("⚠ 落盘前请先停止框架，否则标记会被运行中的实例下一次保存覆盖。")
        print("=" * 68)
        return 0

    _n = mark_and_rewrite(args.snapshot, _marks)
    print("\n" + "=" * 68)
    print(f"已标记 {_n} 个重复节点（quality_flag={FLAG_DUPLICATE}，节点数不变）。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
