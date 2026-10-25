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



#: ★366刀N5：基线 schema（同片同例比对口径：片号 → 失败用例集 + 计数）
BASELINE_SCHEMA = "m366.full_regression.baseline/v1"


def _failed_cases(log_path: str) -> list:
    """从 pytest 输出里抠出失败/报错用例（`FAILED x::y` / `ERROR x::y`）。"""
    _out = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for _ln in f:
                _s = _ln.strip()
                if _s.startswith("FAILED ") or _s.startswith("ERROR "):
                    _part = _s.split(None, 1)[1].split(" - ")[0].strip()
                    if _part:
                        _out.append(_part)
    except OSError as _e:
        _LOG.warning("[N5] 分片日志不可读 %s: %s: %s", log_path, type(_e).__name__, _e)
        return []
    return sorted(set(_out))


def build_baseline(summary: dict) -> dict:
    """把一次分片跑的结果压成基线（片号 → 失败用例 + 计数 + 超时例）。"""
    _rows = {}
    for _r in summary.get("rows") or []:
        _rows[str(_r.get("shard"))] = {
            "files": _r.get("files"),
            "rc": _r.get("rc"),
            "summary": _r.get("summary"),
            "hard_hang": bool(_r.get("hard_hang")),
            "timeout_cases": list(_r.get("timeout_cases") or []),
            "failed_cases": _failed_cases(os.path.join(_ROOT, _r.get("log") or "")),
        }
    return {"schema": BASELINE_SCHEMA, "as_of": summary.get("as_of"),
            "shards": summary.get("shards"),
            "per_test_timeout": summary.get("per_test_timeout"),
            "hard_timeout": summary.get("hard_timeout"),
            "shard_size": summary.get("shard_size"), "shards_rows": _rows}


def save_baseline(path: str, summary: dict) -> bool:
    """原子写基线（`.tmp` → `os.replace` → 读回校验）。"""
    _b = build_baseline(summary)
    try:
        _d = os.path.dirname(path)
        if _d:
            os.makedirs(_d, exist_ok=True)
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_b, f, ensure_ascii=False, indent=1)
        os.replace(_tmp, path)
        with open(path, encoding="utf-8") as f:
            _back = json.load(f)
        return bool(_back.get("schema") == BASELINE_SCHEMA)
    except (OSError, ValueError) as _e:
        _LOG.warning("[N5] 基线写盘失败 %s: %s: %s", path, type(_e).__name__, _e)
        return False


def load_baseline(path: str) -> dict | None:
    """读基线；缺失/损坏返回 None（调用方必须显式说明，不静默放行）。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            _b = json.load(f)
    except (OSError, ValueError) as _e:
        _LOG.warning("[N5] 基线不可读 %s: %s: %s", path, type(_e).__name__, _e)
        return None
    return _b if isinstance(_b, dict) and _b.get("schema") == BASELINE_SCHEMA else None


def diff_against_baseline(base: dict, summary: dict) -> dict:
    """同片同例比对：新增红 / 已转绿 / 未变（**只报告**，不阻断、不改断言）。"""
    _new = build_baseline(summary)
    _old_rows = base.get("shards_rows") or {}
    _new_red, _fixed, _same = [], [], 0
    for _shard, _row in (_new.get("shards_rows") or {}).items():
        _o = _old_rows.get(_shard) or {}
        _o_set = set(_o.get("failed_cases") or [])
        _n_set = set(_row.get("failed_cases") or [])
        for _c in sorted(_n_set - _o_set):
            _new_red.append({"shard": _shard, "case": _c, "kind": "new_red"})
        for _c in sorted(_o_set - _n_set):
            _fixed.append({"shard": _shard, "case": _c, "kind": "fixed"})
        _same += len(_o_set & _n_set)
    return {"schema": "m366.full_regression.diff/v1",
            "new_red": _new_red, "fixed": _fixed, "same_n": _same,
            "baseline_as_of": base.get("as_of"), "now_as_of": _new.get("as_of")}


def main():
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--per-test-timeout", type=float, default=60.0)
    _ap.add_argument("--hard-timeout", type=float, default=900.0)
    _ap.add_argument("--shard-size", type=int, default=20)
    _ap.add_argument("--only", nargs="*", default=None)
    _ap.add_argument("--outdir", default="")
    # ★366刀N5：基线与比对（只报告增量，不阻断 CI、不自动改断言）
    _ap.add_argument("--baseline", default="",
                     help="基线 JSON 路径（--update-baseline 时写入，--compare 时读取）")
    _ap.add_argument("--update-baseline", action="store_true")
    _ap.add_argument("--compare", action="store_true")
    _a = _ap.parse_args()

    _shards = [list(_a.only)] if _a.only else discover(_a.shard_size)
    _ts = time.strftime("%Y%m%d_%H%M%S")
    _outdir = _a.outdir or os.path.join(_ROOT, "logs", "full_regression", _ts)
    os.makedirs(_outdir, exist_ok=True)
    _before = {p: _sha(p) for p in SENTINELS}
    _base = load_baseline(_a.baseline) if _a.compare else None

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
    # ★366刀N5：基线比对（同片同例；只报告增量）
    if _a.compare:
        if _base is None:
            print("[N5] 基线缺失或不可读：%s ⇒ 不比对（不静默放行）"
                  % (_a.baseline or "<未给 --baseline>"))
        else:
            _diff = diff_against_baseline(_base, _summary)
            _summary["baseline_diff"] = _diff
            with open(os.path.join(_outdir, "baseline_diff.json"), "w",
                      encoding="utf-8") as f:
                json.dump(_diff, f, ensure_ascii=False, indent=1)
            print("[N5] 基线比对：新增红=%d 已转绿=%d 未变=%d（只报告，不阻断）"
                  % (len(_diff["new_red"]), len(_diff["fixed"]), _diff["same_n"]))
    if _a.update_baseline:
        _p = _a.baseline or os.path.join(_HERE, "baselines",
                                         "m366_full_regression_baseline.json")
        _ok = save_baseline(_p, _summary)
        print("[N5] 基线已写入 %s ok=%s" % (_p, _ok))
    print("[T1] 汇总：%s" % os.path.join(_outdir, "summary.md"))
    print("[T1] 通过 %d/%d 片；卡死片=%s；side_effects=%d"
          % (_summary["ok_shards"], len(_shards),
             _summary["hang_shards"] or "无", len(_side)))
    return 0 if not _bad else 1


if __name__ == "__main__":
    sys.exit(main())
