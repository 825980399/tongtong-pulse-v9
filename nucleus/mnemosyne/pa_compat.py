# -*- coding: utf-8 -*-
"""pa_compat.py —— pyarrow 跨版本兼容垫片

版本: v10 PulseNet
日期: 2026年9月27日（往期批次 相关任务）

背景:
    pyarrow 25.0.1 移除了 ``pa.Table.from_pylist``（与 ``pa.Table.from_batches``）。
    历史上项目多处直接调用 ``pa.Table.from_pylist(rows[, schema=...])``，在升级后的
    pyarrow 上启动即抛 ``AttributeError: type object 'Table' has no attribute
    'from_pylist'``，导致 Parquet 快照无法保存。

方案:
    提供 ``table_from_rows(rows, schema=None)`` 统一入口，内部按可用 API 降级：
      1. ``pa.Table.from_pylist``   —— 旧版 pyarrow 直接可用（语义最贴合）。
      2. ``pa.RecordBatch.from_pylist(rows, schema=...)`` + ``pa.table(rb)``
         —— 25.x 起唯一保留的 pylist 构造函数。
      3. ``pa.table(dict_of_columns, schema=...)`` —— 最终兜底（按列转置）。
    空 ``rows`` 且未给 schema 时返回空表（保持旧行为：``from_pylist([])`` → 0 列 0 行）。
"""

from __future__ import annotations

from typing import Any

from nucleus._silent_except import silent_exc


def table_from_rows(rows: list[dict[str, Any]], schema: Any = None) -> Any:
    """把 ``list[dict]`` 转成 ``pa.Table``，跨 pyarrow 版本兼容。

    Args:
        rows: 行字典列表；``[]`` 表示空表。
        schema: 可选 ``pa.Schema``。传入时按 schema 对齐（缺失列补 null）。

    Returns:
        ``pyarrow.Table``

    Raises:
        Exception: 三个降级通道全部失败时抛出最后一个异常（调用方按需捕获）。
    """
    import pyarrow as pa

    # 通道 1：旧版 pyarrow 原生 API（语义最贴合，优先）
    _from_pylist = getattr(pa.Table, "from_pylist", None)
    if _from_pylist is not None:
        if schema is not None:
            return _from_pylist(rows, schema=schema)
        return _from_pylist(rows)

    # 空行 + 无 schema：旧行为是「0 列 0 行」表
    if not rows and schema is None:
        return pa.table({})

    # 通道 2：25.x 起唯一保留的 pylist 构造（RecordBatch → Table）
    _last_err: Exception | None = None
    try:
        _rb_from_pylist = pa.RecordBatch.from_pylist
        if schema is not None:
            _rb = _rb_from_pylist(rows, schema=schema)
        else:
            _rb = _rb_from_pylist(rows)
        return pa.table(_rb)
    except Exception as _e:  # noqa: BLE001 - 降级通道，记录后继续
        silent_exc(_e, "pa_compat.py:table_from_rows:channel2", level="debug")
        _last_err = _e

    # 通道 3：按列转置兜底（仅适用于非空、列键一致的常规行）
    try:
        _keys: list[str] = []
        for _r in rows:
            for _k in _r:
                if _k not in _keys:
                    _keys.append(_k)
        _cols = {_k: [_r.get(_k) for _r in rows] for _k in _keys}
        if schema is not None:
            return pa.table(_cols, schema=schema)
        return pa.table(_cols)
    except Exception as _e:  # noqa: BLE001 - 末次降级，抛出原始异常链
        if _last_err is not None:
            raise _last_err from _e
        raise
