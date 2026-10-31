"""m409_mark_legacy_criteria.py —— 给老 30 条策略打 `missing_criterion=True`（第409批 刀1）。

## 为什么单独做一个工具

`_m335_persist_batch` 只在**下次 absorb 时**才会给老条目标记，而 absorb 是低频事件
（上一次写盘在 10-30）。任务书的验收标准「老 30 条标 missing_criterion」等不起 ——
今天就得落。

## 口径

* **只标记、不改写、不删除**：老条目补判据需要知道当初想验什么，程序猜不出来。
* 幂等：已标记的跳过，不重复计数。
* 默认 dry-run，`--apply` 才落盘；落盘前自动备份（`<file>.bak_m409_<ts>`）。
* 行尾按原文件走（当前是 CRLF）—— 整文件行尾翻转会让 diff 变成"每行都改"。

## 用法

```bash
python tools/m409_mark_legacy_criteria.py            # 只看会标多少条
python tools/m409_mark_legacy_criteria.py --apply    # 真落盘
python tools/m409_mark_legacy_criteria.py --stats    # 只看当前盘账结构
```
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution import hypothesis_criteria as hc  # noqa: E402

REL_PATH = os.path.join("data", "evolution", "lesson_strategies.json")


def _path(root: str | None = None) -> str:
    return os.path.join(root or _ROOT, REL_PATH)


def load(path: str) -> tuple[dict, bool]:
    """读盘账，返回 (数据, 是否 CRLF)。"""
    raw = io.open(path, "rb").read()
    crlf = raw.count(b"\r\n") > 0
    return json.loads(raw.decode("utf-8")), crlf


def dump(path: str, data: dict, crlf: bool) -> str:
    """原子写（同目录 tmp + os.replace），按原行尾落盘。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if crlf:
        text = text.replace("\n", "\r\n")
    tmp = path + ".tmp409"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="第409批刀1：老条目标 missing_criterion")
    ap.add_argument("--apply", action="store_true", help="真落盘（默认 dry-run）")
    ap.add_argument("--stats", action="store_true", help="只打印当前盘账结构")
    ap.add_argument("--root", default=None, help="项目根（测试用）")
    args = ap.parse_args(argv)

    p = _path(args.root)
    if not os.path.isfile(p):
        print(f"[FAIL] 盘账不存在: {p}")
        return 2
    data, crlf = load(p)
    strategies = data.get("strategies")
    if not isinstance(strategies, dict):
        print(f"[FAIL] strategies 结构不符: {type(strategies).__name__}")
        return 2

    before = hc.summarize(strategies)
    print(f"[盘账] {p}  (CRLF={crlf})")
    print(f"[契约] hypothesis_criteria v{hc.version()}  开关={'ON' if hc.enabled() else 'OFF'}")
    print(f"[改前] total={before['total']} 有判据={before['has_criteria']} "
          f"已标记={before['missing_criterion']} "
          f"漏网={before['no_criteria_and_no_flag']} "
          f"已对账={before['settled']}")

    if args.stats:
        print("[判据分布] " + json.dumps(before["by_kind"], ensure_ascii=False))
        print("[结果分布] " + json.dumps(before["by_result"], ensure_ascii=False))
        return 0

    rep = hc.mark_legacy_entries(strategies)
    after = hc.summarize(strategies)
    print(f"[标记] 新标={rep['marked']} 原本已标={rep['already']} "
          f"有判据={rep['has_criteria']} 合计={rep['total']}")
    print(f"[改后] total={after['total']} 有判据={after['has_criteria']} "
          f"已标记={after['missing_criterion']} "
          f"漏网={after['no_criteria_and_no_flag']}")

    if rep.get("skipped"):
        print(f"[SKIP] 开关关闭（{rep['skipped']}）⇒ 不落盘")
        return 0
    if not rep["marked"]:
        print("[OK] 无新增标记，盘账无需改写")
        return 0
    if not args.apply:
        print("[DRY-RUN] 未落盘；加 --apply 执行")
        return 0

    bak = p + ".bak_m409_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(p, bak)
    dump(p, data, crlf)
    # 读回校验
    back, back_crlf = load(p)
    _v = hc.summarize(back.get("strategies") or {})
    print(f"[备份] {os.path.basename(bak)}")
    print(f"[落盘] CRLF={back_crlf} total={_v['total']} "
          f"已标记={_v['missing_criterion']} 漏网={_v['no_criteria_and_no_flag']}")
    _bad = _v["no_criteria_and_no_flag"]
    print("[VERIFY] " + ("PASS" if _bad == 0 else f"FAIL 仍有 {_bad} 条漏网"))
    return 0 if _bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
