# -*- coding: utf-8 -*-
"""tools/ci/size_ratchet.py —— 441 刀4：体量棘轮（**只告警，不阻断**）。

【为什么】（任务书 440/441 原文）
48 万行 / 13 天涨 50%；ruff 非 F 类 4955 条。没有「密度」读数时，
「涨了」和「涨得健康」分不开——总量涨但密度降，其实是变好的。

【两条尺子】
  ① **ruff 非 F 密度** = 非 F 类告警数 / 千行（F 类是真 bug，不算风格债）
  ② **data 目录日增长率** = 今日新增字节 / 昨日末总字节（超阈值告警）

【纪律】
  · 只告警（退出码恒 0），不阻断 pre-commit —— 本批是"第一步"，先让数字可见；
  · 基准写 `data/monitor/size_ratchet_baseline.json`，第一次跑自动生成；
  · 读不到就不填 0（铁律 156）：缺数据时对应项记 `unavailable`。

用法：
    python tools/ci/size_ratchet.py            # 告警
    python tools/ci/size_ratchet.py --update   # 把当前值写回基准
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BASE = os.path.join(_ROOT, "data", "monitor", "size_ratchet_baseline.json")

#: 超过基准的这么多倍就告警（密度涨 20%、data 日增 5%）
DENSITY_WARN_RATIO = 1.20
DATA_GROWTH_WARN_RATIO = 0.05

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".bak_batch",
              "tmp", "data"}


def _iter_py():
    for _dp, _dn, _fn in os.walk(_ROOT):
        _dn[:] = [d for d in _dn if d not in _SKIP_DIRS and not d.startswith(".bak_batch")]
        for _f in _fn:
            if _f.endswith(".py"):
                yield os.path.join(_dp, _f)


def count_loc() -> dict:
    """源码行数（跳过 tests/tmp/data）。返回 {files, lines}。"""
    _files = 0
    _lines = 0
    for _p in _iter_py():
        _rel = os.path.relpath(_p, _ROOT)
        if _rel.startswith(("tests" + os.sep, "tmp" + os.sep)):
            continue
        try:
            with open(_p, "rb") as _f:
                _lines += _f.read().count(b"\n")
            _files += 1
        except OSError as _e:
            print(f"[size_ratchet] 读文件失败 {_rel}: {_e}")
    return {"files": _files, "lines": _lines}


def ruff_non_f() -> dict:
    """ruff 非 F 类条数（F 类是真 bug，另算）。读不到 ⇒ unavailable。"""
    _exe = sys.executable
    try:
        _r = subprocess.run([_exe, "-m", "ruff", "check", ".", "--output-format", "concise"],
                            cwd=_ROOT, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as _e:
        logging.getLogger("pulse.ci.size_ratchet").debug("ruff 调用失败: %s", _e)
        return {"available": False, "why": f"{type(_e).__name__}: {_e}"}
    _non_f = 0
    _f = 0
    for _ln in (_r.stdout or "").splitlines():
        _code = _ln.split(": ", 1)[-1].split(" ", 1)[0].strip() if ": " in _ln else ""
        if not _code or not _code[0].isalpha():
            continue
        if _code.startswith("F"):
            _f += 1
        else:
            _non_f += 1
    return {"available": True, "non_f": _non_f, "f": _f}


def data_size() -> dict:
    _total = 0
    _n = 0
    _data = os.path.join(_ROOT, "data")
    for _dp, _dn, _fn in os.walk(_data):
        for _f in _fn:
            try:
                _total += os.path.getsize(os.path.join(_dp, _f))
                _n += 1
            except OSError as _e:
                print(f"[size_ratchet] stat 失败: {_e}")
    return {"bytes": _total, "files": _n}


def measure() -> dict:
    _loc = count_loc()
    _ru = ruff_non_f()
    _ds = data_size()
    _density = (round(_ru.get("non_f", 0) / max(_loc["lines"], 1) * 1000.0, 3)
                if _ru.get("available") else None)
    return {
        "at": time.time(),
        "at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "loc_files": _loc["files"],
        "loc_lines": _loc["lines"],
        "ruff_non_f": _ru.get("non_f"),
        "ruff_f": _ru.get("f"),
        "ruff_available": bool(_ru.get("available")),
        "ruff_density_per_kloc": _density,
        "data_bytes": _ds["bytes"],
        "data_mb": round(_ds["bytes"] / 1048576.0, 1),
        "data_files": _ds["files"],
    }


def _load_base() -> dict:
    try:
        with open(_BASE, encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, dict) else {}
    except (OSError, ValueError) as _e:
        print(f"[size_ratchet] 基准读取失败（按首次处理）: {_e}")
        return {}


def evaluate(cur: dict, base: dict) -> list[dict]:
    """返回告警列表（空 = 全在阈值内）。**纯函数**，便于单测。"""
    _alerts = []
    if not cur.get("ruff_available"):
        _alerts.append({"kind": "ruff_unavailable", "msg": "ruff 读不到，非 F 密度不可判"})
    elif base.get("ruff_density_per_kloc") and cur.get("ruff_density_per_kloc"):
        _ratio = cur["ruff_density_per_kloc"] / float(base["ruff_density_per_kloc"])
        if _ratio > DENSITY_WARN_RATIO:
            _alerts.append({
                "kind": "ruff_density",
                "msg": (f"ruff 非 F 密度 {cur['ruff_density_per_kloc']}/千行 "
                        f"= 基准 {base['ruff_density_per_kloc']} 的 {_ratio:.2f}×"
                        f"（阈值 {DENSITY_WARN_RATIO}×）"),
                "value": cur["ruff_density_per_kloc"],
                "base": base["ruff_density_per_kloc"],
            })
    if base.get("data_bytes") and cur.get("data_bytes"):
        _g = (cur["data_bytes"] - float(base["data_bytes"])) / float(base["data_bytes"])
        if _g > DATA_GROWTH_WARN_RATIO:
            _alerts.append({
                "kind": "data_growth",
                "msg": (f"data 目录 {cur['data_mb']} MB，比基准 "
                        f"{round(float(base['data_bytes']) / 1048576.0, 1)} MB "
                        f"涨 {_g:.1%}（阈值 {DATA_GROWTH_WARN_RATIO:.0%}）"),
                "growth": round(_g, 4),
            })
    return _alerts


def main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="441 刀4 体量棘轮（只告警）")
    _ap.add_argument("--update", action="store_true", help="把当前读数写回基准")
    _ap.add_argument("--json", default="")
    _a = _ap.parse_args(argv)

    _cur = measure()
    _base = _load_base()
    _alerts = evaluate(_cur, _base)

    print("=" * 70)
    print(f"源码 {_cur['loc_files']} 文件 / {_cur['loc_lines']} 行 | "
          f"ruff 非F={_cur['ruff_non_f']} F={_cur['ruff_f']} | "
          f"密度={_cur['ruff_density_per_kloc']}/千行 | data={_cur['data_mb']} MB")
    if _alerts:
        for _x in _alerts:
            print(f"  [WARN] {_x['kind']}: {_x['msg']}")
    else:
        print("  [OK] 两项均在阈值内")
    print("=" * 70)

    if _a.update or not _base:
        os.makedirs(os.path.dirname(_BASE), exist_ok=True)
        with open(_BASE, "w", encoding="utf-8") as _f:
            json.dump(_cur, _f, ensure_ascii=False, indent=1)
        print(f"基准已{'更新' if _a.update else '初始化'}: {_BASE}")
    if _a.json:
        with open(_a.json, "w", encoding="utf-8") as _f:
            json.dump({"current": _cur, "alerts": _alerts}, _f, ensure_ascii=False, indent=1)
    return 0   # ★只告警：恒 0，不阻断


if __name__ == "__main__":
    raise SystemExit(main())
