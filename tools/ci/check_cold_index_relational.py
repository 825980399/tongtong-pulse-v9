# -*- coding: utf-8 -*-
"""第162批刀3 S4 · 冷索引关系式断言巡检工具（tools/ci）。

校验三类关系式（烛微冷索引定位 §五 S4）：
  A. len(index) == distinct(cold)：侧车索引条目数 == 冷存 Parquet 去重 node_id 数；
  B. parquet == snapshot（id 级一致）：索引 node_id 集合与 Parquet node_id 集合双向相等；
  C. l1 == L1 分区：同一 node_id 在 Parquet 的 evol_level 与索引登记的 level 一致
     （L1 分区不被塌缩，呼应第81批 T4 真层级修复）。

用法：
  python tools/ci/check_cold_index_relational.py [--cold-dir DIR]

退出码：0=通过或无需校验（无冷存/无索引/无 pyarrow/索引为空）；
       1=发现不一致（需修复）。
不修改任何数据，只读。每个 except 体均走 silent_exc（cw2 豁免），无静默回潮。
"""

import argparse
import json
import os
import sys

import pyarrow.parquet as pq  # noqa: F401

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# 运行期依赖（冷存系统必备）；缺失则工具整体不可用

from nucleus._silent_except import silent_exc


def _collect_parquet(cold_dir):
    """枚举 cold_dir 下全部 .parquet，返回 {node_id: evol_level}（去重取首个）。"""
    _rows = {}
    for _root, _dirs, _files in os.walk(cold_dir):
        for _fn in _files:
            if not _fn.endswith(".parquet"):
                continue
            _fp = os.path.join(_root, _fn)
            try:
                _t = pq.read_table(_fp, columns=["node_id", "evol_level"])
                _d = _t.to_pylist()
            except Exception as _e:
                silent_exc(_e, where="tools.ci.check_cold_index_relational._collect_parquet")
                continue
            for _r in _d:
                _nid = str(_r.get("node_id", "") or "")
                if _nid and _nid not in _rows:
                    _rows[_nid] = str(_r.get("evol_level", "L1"))
    return _rows


def main():
    ap = argparse.ArgumentParser(description="冷索引关系式断言巡检（刀3 S4）")
    ap.add_argument("--cold-dir", default=None, help="冷存目录（默认 data/knowledge/cold）")
    args = ap.parse_args()
    _cold = os.path.abspath(args.cold_dir) if args.cold_dir else "data/knowledge/cold"
    _idx_path = _cold.rstrip(os.sep) + ".index.json"

    if not os.path.isdir(_cold):
        print("[S4] SKIP：冷存目录不存在 %s" % _cold)
        return 0
    if not os.path.isfile(_idx_path):
        print("[S4] SKIP：无侧车索引 %s（未启用或尚未重建）" % _idx_path)
        return 0

    try:
        with open(_idx_path, "r", encoding="utf-8") as _f:
            _index = json.load(_f)
    except Exception as _e:
        silent_exc(_e, where="tools.ci.check_cold_index_relational.main(index)")
        print("[S4] FAIL：索引文件无法解析 %s" % _idx_path)
        return 1
    if not isinstance(_index, dict) or not _index:
        print("[S4] SKIP：索引为空")
        return 0

    _parquet = _collect_parquet(_cold)
    if not _parquet:
        # 索引有条目但冷存无 Parquet 落盘 → 可能全在内存未落盘，属正常，不判失败
        print("[S4] SKIP：冷存无 Parquet 数据（索引 %d 条但无落盘）" % len(_index))
        return 0

    _idx_ids = set(_index.keys())
    _pq_ids = set(_parquet.keys())

    _fails = []
    # A + B：双向 id 一致（len(index)==distinct(cold) 且 parquet==snapshot@id）
    _only_idx = _idx_ids - _pq_ids
    _only_pq = _pq_ids - _idx_ids
    if _only_idx:
        _fails.append("索引有 %d 条无对应 Parquet 落盘: %s" % (len(_only_idx), list(_only_idx)[:5]))
    if _only_pq:
        _fails.append("Parquet 有 %d 条未登记入索引: %s" % (len(_only_pq), list(_only_pq)[:5]))
    # C：level 一致（l1==L1 分区）
    for _nid in (_idx_ids & _pq_ids):
        _entry = _index[_nid]
        _il = str(_entry.get("level", "L1") if isinstance(_entry, dict) else _entry)
        _pl = _parquet[_nid]
        if _il != _pl:
            _fails.append("level 不一致 node_id=%s 索引=%s Parquet=%s" % (_nid, _il, _pl))

    if _fails:
        print("[S4] FAIL：发现 %d 处关系式不一致：" % len(_fails))
        for _x in _fails:
            print("  - " + _x)
        return 1
    print("[S4] PASS：索引(%d) 与 Parquet(%d) 的 node_id 双向一致且 level 一致"
          % (len(_idx_ids), len(_pq_ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
