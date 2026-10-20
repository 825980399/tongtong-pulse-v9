#!/usr/bin/env python
"""★320批刀3：验证产物一键剔除/标记工具（期望台账·只动 ledger 一个文件）。

背景（烛微320§0-2 第四例）：施工/压测的**验证运行**会加载生产版模块，把产物写进
生产 ``data/``（ledger 114 条 + R3 样本 2 份 + 合成图 3 次），时间戳与提交同窗 ⇒
压测 L 圈会把"验证运行产出"当成"活体产能"。

硬规（320 批起）：
  1. 验证运行一律 **隔离根** ``tmp/p3xx_root/``（``TT_LEDGER_PATH`` 环境变量指向它）
  2. 若不得不写生产目录，每条必须带 ``source=verification``
  3. 交付报告须申报隔离根路径；**再出现"验证产物落生产 data/"即为违规**

用法：
    # 1) 只看（默认 dry-run，绝不写）
    python tools/stress/purge_verification_data.py

    # 2) 剔除带 source=verification 的条目（先自动备份到隔离根）
    python tools/stress/purge_verification_data.py --apply

    # 3) 旧数据没有 source 字段 —— 按时间窗识别为验证产物并"标记"（不删，更安全）
    python tools/stress/purge_verification_data.py --mark-legacy \
        --window "2026-10-20 00:00,2026-10-20 00:40" --apply

    # 4) 同上但直接剔除（慎用；仍会备份）
    python tools/stress/purge_verification_data.py --purge-legacy \
        --window "2026-10-20 00:00,2026-10-20 00:40" --apply

安全：
  * 只操作 `data/world_model/expectation_ledger.jsonl`，不碰任何其它文件
  * 任何写操作前先把原文件整份备份到 `tmp/p320_root/purge_backup_<ts>.jsonl`
  * 不删除文件本身，只按行重写
"""
import argparse
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

_REL = os.path.join("data", "world_model", "expectation_ledger.jsonl")
_ISO = "%Y-%m-%d %H:%M"


def _ledger_file() -> str:
    return os.environ.get("TT_LEDGER_PATH") or os.path.join(_ROOT, _REL)


def _parse_window(text: str) -> tuple[float, float]:
    if not text:
        return (0.0, 0.0)
    _a, _b = [x.strip() for x in text.split(",", 1)]
    return (time.mktime(time.strptime(_a, _ISO)),
            time.mktime(time.strptime(_b, _ISO)))


def _in_window(row: dict, win: tuple[float, float]) -> bool:
    if not win or not any(win):
        return False
    _ts = float((row.get("measured") or {}).get("observed_at") or 0)
    return bool(_ts) and win[0] <= _ts <= win[1]


def main() -> int:
    _ap = argparse.ArgumentParser(description="验证产物剔除/标记（只动 ledger）")
    _ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run）")
    _ap.add_argument("--window", default="", help='时间窗 "YYYY-MM-DD HH:MM,YYYY-MM-DD HH:MM"')
    _ap.add_argument("--mark-legacy", action="store_true",
                     help="把窗口内**无 source** 的旧条目标记为 verification（不删）")
    _ap.add_argument("--purge-legacy", action="store_true",
                     help="把窗口内**无 source** 的旧条目直接剔除（仍备份）")
    _a = _ap.parse_args()

    _p = _ledger_file()
    if not os.path.exists(_p):
        print(f"[ERR] 台账不存在: {_p}")
        return 2
    _win = _parse_window(_a.window)

    _rows: list[dict] = []
    _bad = 0
    with open(_p, encoding="utf-8", errors="ignore") as _f:
        for _ln in _f:
            _ln = _ln.strip()
            if not _ln:
                continue
            try:
                _rows.append(json.loads(_ln))
            except ValueError as _e:
                _bad += 1
                silent_exc(_e, where="tools.stress.purge_verification_data.main.parse")

    _keep: list[dict] = []
    _n_verif = _n_legacy_mark = _n_legacy_purge = 0
    for _r in _rows:
        # 来源进程看 `source_process`（320 起）；旧数据回退看 `source` 的两态值
        _src = str(_r.get("source_process") or "")
        if not _src:
            _s2 = str(_r.get("source") or "")
            _src = _s2 if _s2 in ("live", "verification") else ""
        _is_verif = (_src.startswith("verif"))
        _is_legacy = (not _src) and _in_window(_r, _win)
        if _is_verif:
            _n_verif += 1
            continue                      # 带标记的验证产物：剔除
        if _is_legacy and _a.mark_legacy:
            _r["source"] = "verification"
            _r["source_process"] = "verification"
            _r["purge_note"] = "marked_by_tools_stress_purge_320"
            _n_legacy_mark += 1
            _keep.append(_r)
            continue
        if _is_legacy and _a.purge_legacy:
            _n_legacy_purge += 1
            continue
        _keep.append(_r)

    print("=" * 62)
    print("台账: %s" % _p)
    print("总行 %d ｜ 解析失败 %d ｜ 时间窗 %s" % (len(_rows), _bad, _a.window or "-"))
    print("带 source=verification 将剔除: %d" % _n_verif)
    print("无 source 且落窗内 标记为 verification: %d" % _n_legacy_mark)
    print("无 source 且落窗内 直接剔除: %d" % _n_legacy_purge)
    print("保留: %d" % len(_keep))
    if not _a.apply:
        print("\n[dry-run] 未写盘。确认后加 --apply。")
        return 0

    _bak_dir = os.path.join(_ROOT, "tmp", "p320_root")
    os.makedirs(_bak_dir, exist_ok=True)
    _bak = os.path.join(_bak_dir, "purge_backup_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(_p, _bak)
    print("已备份 -> %s" % _bak)

    with open(_p, "w", encoding="utf-8", newline="\n") as _f:
        for _r in _keep:
            _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
    print("已写回，剩余 %d 条" % len(_keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
