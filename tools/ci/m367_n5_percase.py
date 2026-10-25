# -*- coding: utf-8 -*-
"""367 刀3：N5 续 · 分片基线失败用例**逐例**归族器（可复跑）。

与 366 尾的区别：366 是**按文件**归族，本工具是**逐用例**（同一文件内可多因）。

## 四类口径（互斥完备，判定顺序即优先级）

| 类 | 判据 |
|---|---|
| `healed` | **rc==0**（单跑通过）—— 基线记的失败本轮不复现 ⇒ 顺序依赖/共享状态 |
| `env_dependency` | 外部服务/HTTP/超时/缺依赖/文件系统（正则） |
| `test_self` | 夹具/mock 落点/参数签名过时（正则） |
| `pre_existing` | 有 `E` 行或 `FAILED x::y` 的其余失败 |
| `unknown` | 无 `E` 行且 **rc≠0**（需人工看完整输出） |

★`healed` 必须在最前：实测有一批用例在**同文件**里跑会红、**单跑**却绿
（30/135），若把 rc==0 落进 unknown，归族表会凭空多出 30 条"待人工"。

## 用法

```
python -X utf8 tools/ci/m367_n5_percase.py --baseline tools/ci/baselines/m366_full_regression_baseline.json \
                                           --outdir logs/full_regression/n5_percase
```

只读：不改断言、不销既有失败账、不写 `data/`。
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import time

_LOG = logging.getLogger("tools.ci.m367_n5_percase")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 环境依赖族（外部服务/超时/缺依赖/文件系统）
_RE_ENV = re.compile(
    r"(ConnectionRefused|NewConnectionError|Max retries exceeded|"
    r"No route to host|Name or service not known|HTTPConnectionPool|"
    r"SSLError|pymysql|Neo4j|InfluxDB|redis\.|Timeout|timed out|"
    r"ModuleNotFoundError|No module named|FileNotFoundError|"
    r"PermissionError|OSError.*\[Errno)")
#: 测试自身族（夹具/mock 落点/签名过时）
_RE_SELF = re.compile(
    r"(AttributeError.*(mock|Mock|patch|fixture|_logger|cls\.)|"
    r"'NoneType' object has no attribute|"
    r"unexpected keyword argument|takes \d+ positional|"
    r"FixtureLookupError|fixture '.*' not found)")

FAMILIES = ("healed", "env_dependency", "test_self", "pre_existing", "unknown")


def classify(out: str, rc: int) -> str:
    """四类互斥判定（纯函数，便于单测）。

    Args:
        out: pytest 合并输出（stdout+stderr）
        rc:  进程返回码（0=通过）

    Returns:
        `healed` / `env_dependency` / `test_self` / `pre_existing` / `unknown`
    """
    if int(rc) == 0:
        return "healed"
    _o = str(out or "")
    if _RE_ENV.search(_o):
        return "env_dependency"
    if _RE_SELF.search(_o):
        return "test_self"
    if re.search(r"^E\s+", _o, re.M) or re.search(r"^FAILED\s+\S+::", _o, re.M):
        return "pre_existing"
    return "unknown"


def first_error(out: str, cap: int = 300) -> str:
    """首条 `E` 行；无则取输出尾部非空行（供 unknown 判读）。"""
    _o = str(out or "")
    m = re.search(r"^E\s+(.{0,%d})" % cap, _o, re.M)
    if m:
        return m.group(1).strip()
    _tail = [x for x in _o.strip().split("\n") if x.strip()][-6:]
    return " | ".join(x.strip()[:80] for x in _tail)


def load_cases(baseline_path: str) -> list:
    """从分片基线读全部失败用例（去重保序）。"""
    d = json.load(io.open(baseline_path, encoding="utf-8"))
    _seen, _out = set(), []
    for shard, row in (d.get("shards_rows") or {}).items():
        for c in row.get("failed_cases") or []:
            if c in _seen:
                continue
            _seen.add(c)
            _out.append({"case": c, "shard": shard})
    return _out


def run_one(case: str, idx: int = 0, timeout: float = 300.0) -> dict:
    """单跑一条用例，返回归族结果（独立子进程/独立桶）。"""
    _env = dict(os.environ)
    _env["CODEBUDDY_TOOL_CALL_ID"] = "m367pc-%03d" % int(idx)
    _env["PULSE_DATA_TREE_GUARD"] = "0"
    _env["PYTHONDONTWRITEBYTECODE"] = "1"
    _t0 = time.time()
    try:
        pr = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", case, "-q",
                             "--timeout=60", "--timeout-method=thread",
                             "-p", "no:cacheprovider", "--no-header", "--tb=short"],
                            capture_output=True, timeout=timeout, cwd=_ROOT, env=_env)
        _out = ((pr.stdout or b"").decode("utf-8", "replace")
                + (pr.stderr or b"").decode("utf-8", "replace"))
        _rc = pr.returncode
    except subprocess.TimeoutExpired:
        _LOG.warning("[367刀3] 单跑超时（>%.0fs）case=%s ⇒ 记 unknown", timeout, case)
        _out, _rc = "", -9
    _fam = classify(_out, _rc)
    _exc = re.findall(r"(\w+(?:Error|Exception|Exit|Timeout))\b", _out)
    return {"case": case, "rc": _rc, "dur_s": round(time.time() - _t0, 1),
            "family": _fam, "exc_types": sorted(set(_exc))[:4],
            "first_error": first_error(_out), "still_red": _rc != 0}


def run_all(baseline_path: str, outdir: str, limit: int = 0) -> dict:
    """跑完整批并落 `percase.json`。`limit>0` 只跑前 N 条（冒烟用）。"""
    os.makedirs(outdir, exist_ok=True)
    _cases = load_cases(baseline_path)
    if limit > 0:
        _cases = _cases[:int(limit)]
    _rows = []
    _t0 = time.time()
    for i, c in enumerate(_cases, 1):
        _rows.append(run_one(c["case"], i))
        if i % 20 == 0 or i == len(_cases):
            _LOG.info("[367刀3] %d/%d %.0fs", i, len(_cases), time.time() - _t0)
    _res = {"schema": "m367.n5.percase/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "baseline": baseline_path, "total": len(_cases),
            "elapsed_s": round(time.time() - _t0, 1), "rows": _rows}
    io.open(os.path.join(outdir, "percase.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps(_res, ensure_ascii=False, indent=1))
    return _res


def summarize(res: dict) -> dict:
    """族分布 + 仍红/自愈计数（只读）。"""
    import collections
    _c = collections.Counter(r["family"] for r in res.get("rows") or [])
    return {"by_family": dict(_c),
            "still_red": sum(1 for r in res.get("rows") or [] if r["still_red"]),
            "healed": sum(1 for r in res.get("rows") or [] if not r["still_red"]),
            "total": res.get("total", 0)}


def main(argv=None) -> int:
    import argparse
    _ap = argparse.ArgumentParser(description="367刀3 逐例归族器（只读）")
    _ap.add_argument("--baseline", default=os.path.join(
        _ROOT, "tools", "ci", "baselines", "m366_full_regression_baseline.json"))
    _ap.add_argument("--outdir", default=os.path.join(
        _ROOT, "logs", "full_regression", "n5_percase"))
    _ap.add_argument("--limit", type=int, default=0, help=">0 只跑前 N 条（冒烟）")
    _a = _ap.parse_args(argv)
    if not os.path.isfile(_a.baseline):
        print("[367刀3] 基线不存在: %s" % _a.baseline)
        return 2
    _res = run_all(_a.baseline, _a.outdir, limit=_a.limit)
    _s = summarize(_res)
    print("[367刀3] 逐例 %d 条  %.1fs" % (_s["total"], _res["elapsed_s"]))
    for _k, _v in sorted(_s["by_family"].items()):
        print("   %-16s %d" % (_k, _v))
    print("   仍红 %d / 自愈 %d" % (_s["still_red"], _s["healed"]))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
