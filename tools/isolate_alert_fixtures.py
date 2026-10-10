# -*- coding: utf-8 -*-
"""B156-6 落盘隔离：标记 alerts 历史测试夹具（不删、标隔离、可复跑）。

背景（任务书 B156-6 + 前置分析 §三-D-②）：
  ``data/reports/alerts.jsonl`` 含并发 pytest 期「双写」污染产生的测试夹具，
  需「行标清理票、不删、标隔离」——即给这些行追加 ``_isolated`` /
  ``_cleanup_ticket`` 字段，使其可被下游告警分析排除，但不删除（保留追溯）。

安全约束：
  * 操作前先备份到 ``data/reports/_isolation_backup/alerts_<时间戳>.jsonl``；
  * 仅追加字段，不改写既有字段；
  * 幂等：已标记行不再重复标记；
  * 默认仅标记**无歧义的纯探针夹具** ``{"probe": true}``（无任何告警字段）。
    任务书称「42 条」的更宽口径需前置分析 §三-D-② 的精确指纹，
    故本工具按 --criterion 参数化，便于人工补全其余口径而不误伤真实生产告警。

用法：
  python tools/isolate_alert_fixtures.py            # 默认标记 probe 夹具（dry-run 预览）
  python tools/isolate_alert_fixtures.py --apply    # 真正写回
  python tools/isolate_alert_fixtures.py --criterion probe --apply
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

from nucleus._silent_except import silent_exc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_FILE = os.path.join(ROOT, "data", "reports", "alerts.jsonl")
BACKUP_DIR = os.path.join(ROOT, "data", "reports", "_isolation_backup")
CLEANUP_TICKET = "B156-6"


def _is_probe_fixture(rec: dict) -> bool:
    """无歧义测试夹具：整行仅含 ``{"probe": ...}``，无任何告警字段。"""
    return set(rec.keys()) == {"probe"}


_CRITERIA = {
    "probe": _is_probe_fixture,
}


def load_records(path: str):
    _out = []
    if not os.path.isfile(path):
        return _out
    with open(path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line:
                continue
            try:
                _out.append(json.loads(_line))
            except ValueError as _e:
                silent_exc(_e, where="tools.isolate_alert_fixtures.load_records")
                # 损坏行保留不动
                _out.append({"_unparseable": _line})
    return _out


def mark(records, criterion_name: str):
    _pred = _CRITERIA.get(criterion_name)
    if _pred is None:
        raise SystemExit("未知 criterion: {}（可选: {}）".format(criterion_name, ", ".join(_CRITERIA)))
    _marked, _skipped = 0, 0
    for _r in records:
        if not isinstance(_r, dict) or "_unparseable" in _r:
            _skipped += 1
            continue
        if _r.get("_isolated"):
            continue
        if _pred(_r):
            _r["_isolated"] = True
            _r["_cleanup_ticket"] = CLEANUP_TICKET
            _marked += 1
    return _marked


def main(argv=None):
    _argv = argv if argv is not None else sys.argv[1:]
    _apply = "--apply" in _argv
    _criterion = "probe"
    for _a in _argv:
        if _a.startswith("--criterion="):
            _criterion = _a.split("=", 1)[1]
    _records = load_records(ALERT_FILE)
    _before = sum(1 for _r in _records
                  if isinstance(_r, dict) and _r.get("_isolated"))
    _marked = mark(_records, _criterion)
    print("[isolate_alert_fixtures] 文件: {}".format(ALERT_FILE))
    print("[isolate_alert_fixtures] criterion=%s, 总行=%d, 已隔离前=%d, 本次新标记=%d"
          % (_criterion, len(_records), _before, _marked))
    if not _apply:
        print("[isolate_alert_fixtures] dry-run（未写回）。加 --apply 真正执行。")
        return 0
    if _marked == 0:
        print("[isolate_alert_fixtures] 无可标记行，跳过写回。")
        return 0
    os.makedirs(BACKUP_DIR, exist_ok=True)
    _stamp = time.strftime("%Y%m%d_%H%M%S")
    _bak = os.path.join(BACKUP_DIR, "alerts_{}.jsonl".format(_stamp))
    shutil.copy2(ALERT_FILE, _bak)
    with open(ALERT_FILE, "w", encoding="utf-8") as _f:
        for _r in _records:
            _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
    print("[isolate_alert_fixtures] 已备份原文件 -> {}".format(_bak))
    print("[isolate_alert_fixtures] 已写回（新增隔离标记 %d 行）。" % _marked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
