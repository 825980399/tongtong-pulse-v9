# -*- coding: utf-8 -*-
"""★365刀T1：全量回归**分片可跑通**基建（带超时定位卡死例）。

背景（364 交接件 §三 + `8e3c48f9` 订正）：`python -m pytest tests/ -q`（6347/6352 例）
两次卡死——第一次 54%（RSS 8831MB、CPU 停转），第二次 30%，**位置不同 ⇒ 非固定用例**；
`pytest-timeout` 未装、`faulthandler` 堆栈被 pytest capture 吞掉。替代证据只有
受影响子集 226 例全绿，全量结论一直缺。

本工具只做三件事（不改任何既有测试行为、不放宽任何断言）：
  1. 按 `tests/test_*.py` 分片，每片一个独立子进程 —— 卡死只死一片，不拖垮全局；
  2. 每片注入**独立 `CODEBUDDY_TOOL_CALL_ID`**（铁律126：safe-delete 批量守卫跨进程共享桶，
     同一 Bash 调用内连跑多片会让后续删除型用例成片假失败）；
  3. 每片带 `--timeout=<单例秒>`（pytest-timeout，thread 方法）＋子进程级硬超时，
     超时片记录**卡死例名与堆栈**，写进 `卡死例清单`。

产物（默认 `logs/full_regression/<ts>/`，不进 `data/`）：
  `shard_XX.log` / `summary.json` / `summary.md`

用法：
    python tools/ci/m365_full_regression_runner.py --per-test-timeout 60 --shard-size 20
    python tools/ci/m365_full_regression_runner.py --only tests/test_m365_a1_o3_review_items.py
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # tools/ci ⇒ 仓库根（两级）
PY = sys.executable

#: 片级超时/异常的留痕器（cw2 口径：except 体不得只有纯 assign）
_LOG = logging.getLogger("tools.ci.m365_full_regression_runner")

#: 生产数据哨兵（全量回归可能改写 data/，跑前跑后比字节 —— 只记录，不改）
SENTINELS = (
    os.path.join("data", "patches", "pending_patches.json"),
    os.path.join("data", "patches", "patch_history.json"),
    os.path.join("data", "evolution", "effect_verify.json"),
)


def _sha(path: str) -> str:
    _p = os.path.join(_ROOT, path)
    if not os.path.isfile(_p):
        return "absent"
    _h = hashlib.sha256()
    with open(_p, "rb") as f:
        for _b in iter(lambda: f.read(1 << 20), b""):
            _h.update(_b)
    return _h.hexdigest()[:16]


def discover(shard_size: int) -> list:
    _files = sorted(glob.glob(os.path.join(_ROOT, "tests", "test_*.py")))
    _rels = [os.path.relpath(p, _ROOT).replace("\\", "/") for p in _files]
    return [_rels[i:i + shard_size] for i in range(0, len(_rels), shard_size)]


def _summary_line(out: str) -> str:
    for _ln in reversed(out.strip().splitlines()):
        if re.search(r"\d+ (passed|failed|error)", _ln) or "no tests ran" in _ln:
            return _ln.strip()
    return out.strip().splitlines()[-1].strip() if out.strip() else "(无输出)"


def run_shard(idx: int, files: list, per_test_timeout: float, hard_timeout: float,
              outdir: str) -> dict:
    _log = os.path.join(outdir, "shard_%02d.log" % idx)
    _env = dict(os.environ)
    # ★铁律126：每片独立桶，避免跨片累计 safe-delete 计数
    _env["CODEBUDDY_TOOL_CALL_ID"] = "m365_t1_shard_%02d" % idx
    _env.setdefault("PYTHONIOENCODING", "utf-8")
    _cmd = [PY, "-X", "utf8", "-m", "pytest", *files, "-q",
            "--timeout", str(per_test_timeout), "--timeout-method", "thread",
            "-p", "no:cacheprovider"]
    _t0 = time.time()
    try:
        _r = subprocess.run(_cmd, cwd=_ROOT, env=_env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=hard_timeout)
        _out, _rc, _hang = (_r.stdout or "") + (_r.stderr or ""), _r.returncode, False
    except subprocess.TimeoutExpired as _te:
        # 片级硬超时：必须留痕（否则「这片没跑完」和「这片挂了」分不清）
        _LOG.warning("[T1] 片%d 硬超时（>%.0fs），按卡死处理", idx, hard_timeout)
        _out = ((_te.stdout or b"").decode("utf-8", "replace")
                if isinstance(_te.stdout, bytes) else (_te.stdout or ""))
        _rc, _hang = -9, True
    _dur = round(time.time() - _t0, 1)
    with open(_log, "w", encoding="utf-8") as f:
        f.write(_out)
    _hung = [ln.strip() for ln in _out.splitlines()
             if re.search(r"Timeout \(>?\d|timed out after|Timeout >", ln)][:20]
    return {"shard": idx, "files": len(files), "rc": _rc, "dur_s": _dur,
            "hard_hang": _hang, "summary": _summary_line(_out),
            "timeout_cases": _hung, "log": os.path.relpath(_log, _ROOT)}


def main():
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--per-test-timeout", type=float, default=60.0)
    _ap.add_argument("--hard-timeout", type=float, default=900.0)
    _ap.add_argument("--shard-size", type=int, default=20)
    _ap.add_argument("--only", nargs="*", default=None)
    _ap.add_argument("--outdir", default="")
    _a = _ap.parse_args()

    _shards = [list(_a.only)] if _a.only else discover(_a.shard_size)
    _ts = time.strftime("%Y%m%d_%H%M%S")
    _outdir = _a.outdir or os.path.join(_ROOT, "logs", "full_regression", _ts)
    os.makedirs(_outdir, exist_ok=True)
    _before = {p: _sha(p) for p in SENTINELS}

    print("[T1] 分片数=%d 每片≤%d 文件 单例超时=%.0fs 片级硬超时=%.0fs"
          % (len(_shards), _a.shard_size, _a.per_test_timeout, _a.hard_timeout))
    _rows = []
    for _i, _files in enumerate(_shards, 1):
        _r = run_shard(_i, _files, _a.per_test_timeout, _a.hard_timeout, _outdir)
        _rows.append(_r)
        print("[T1] 片%02d/%02d rc=%s dur=%.1fs %s"
              % (_i, len(_shards), _r["rc"], _r["dur_s"], _r["summary"][:90]))

    _after = {p: _sha(p) for p in SENTINELS}
    _side = {p: (_before[p], _after[p]) for p in _before if _before[p] != _after[p]}
    _bad = [r for r in _rows if r["rc"] != 0]
    _hangs = [r for r in _rows if r["hard_hang"] or r["timeout_cases"]]
    _summary = {
        "schema": "m365.full_regression.runner/v1",
        "as_of": _ts, "shards": len(_shards), "per_test_timeout": _a.per_test_timeout,
        "hard_timeout": _a.hard_timeout, "shard_size": _a.shard_size,
        "ok_shards": len(_rows) - len(_bad), "bad_shards": len(_bad),
        "hang_shards": [r["shard"] for r in _hangs],
        "side_effects": _side, "rows": _rows,
    }
    with open(os.path.join(_outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(_summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(_outdir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("# 全量回归分片结果（%s）\n\n" % _ts)
        f.write("片数 %d ／ 通过 %d ／ 失败 %d ／ 卡死片 %s\n\n"
                % (len(_shards), _summary["ok_shards"], len(_bad),
                   _hangs and [r["shard"] for r in _hangs] or "无"))
        f.write("| 片 | 文件数 | rc | 耗时(s) | 结论 |\n|---|---|---|---|---|\n")
        for _r in _rows:
            f.write("| %02d | %d | %s | %.1f | %s |\n"
                    % (_r["shard"], _r["files"], _r["rc"], _r["dur_s"],
                       _r["summary"][:80].replace("|", "/")))
        if _hangs:
            f.write("\n## 卡死/超时用例\n\n")
            for _r in _hangs:
                f.write("- 片%02d（%s）\n" % (_r["shard"], _r["log"]))
                for _c in _r["timeout_cases"]:
                    f.write("  - `%s`\n" % _c.replace("|", "/")[:200])
        if _side:
            f.write("\n## 生产数据副作用（仅记录）\n\n")
            for _p, (_b, _a2) in _side.items():
                f.write("- `%s` %s → %s\n" % (_p, _b, _a2))
    print("[T1] 汇总：%s" % os.path.join(_outdir, "summary.md"))
    print("[T1] 通过 %d/%d 片；卡死片=%s；side_effects=%d"
          % (_summary["ok_shards"], len(_shards),
             _summary["hang_shards"] or "无", len(_side)))
    return 0 if not _bad else 1


if __name__ == "__main__":
    sys.exit(main())
