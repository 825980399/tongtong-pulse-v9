# -*- coding: utf-8 -*-
"""415 刀3：明日 04:00 deferred 五项**一键验证**（纯只读，不写任何数据面）

背景：412/413/414 三批的 deferred 项都挂在 04:00 夜间编排上，但 11-01 04:00
早于 11:04 的重启 ⇒ 今日**一次都没真跑**。本脚本把「明天 04:00 之后该看什么」
固化成五条判据，避免又靠人肉翻日志。

五项（全部只读，不调 `_save()`、不碰运行进程持有的文件 —— 铁律 22）：
  1 narrative_persist : `data/narrative/narrative_<day>.jsonl` 是否落盘，
                        且每行 headline / intent 非空、delta 至少一格有数
  2 experience_template: `data/experience/experience_pool.json` 里 is_template 条数
  3 o3_in_digest      : `data/reports/digest_<day>.md` 是否有第八段 + eligible 数字
  4 perf_delta        : 日志里有没有 `[412刀3] perf_delta before=...` 实跑痕迹
  5 narrative_replay  : 重启后 `narrative_store.load_recent()` 能不能回放出条目

用法：
  python tools/m415_deferred_verify.py                    # 今天
  python tools/m415_deferred_verify.py --day 20261102      # 指定日
  python tools/m415_deferred_verify.py --json               # 机器可读
  python tools/m415_deferred_verify.py --root D:/tmp/fake   # 单测/演练用

退出码：0 全 PASS ｜ 1 有 FAIL ｜ 2 脚本自身异常
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys
import time
from typing import Any, Callable, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc  # noqa: E402

O3_HEAD = "## 八、O3 删死代码 dry-run"
PERF_MARK = "[412刀3] perf_delta"
NARR_PREFIX = "narrative_"
NARR_SUFFIX = ".jsonl"
_RE_ELIGIBLE = re.compile(r"若开闸将放行[：:]\s*\*{0,2}([0-9]+)\*{0,2}\s*条")


# ---------------------------------------------------------------- 工具
def _read_text(path: str) -> str:
    """读文本；失败返回空串（由调用方判 FAIL，不静默当成功）。"""
    try:
        with io.open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace").replace("\r", "")
    except OSError as _e:
        silent_exc(_e, where="m415_deferred_verify._read_text", level="debug")
        return ""


def _result(name: str, ok: bool, value: Any, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "value": value, "detail": detail}


def _walk_logs(logs_dir: str, max_depth: int = 3) -> list[str]:
    """收集 logs 下的 .log（限制深度，跳过全量回归产物）。"""
    out: list[str] = []
    base = os.path.abspath(logs_dir)
    for dp, dns, fns in os.walk(base):
        depth = os.path.relpath(dp, base).count(os.sep)
        if depth >= max_depth:
            dns[:] = []
        dns[:] = [d for d in dns if d not in ("full_regression", "__pycache__")]
        for f in fns:
            if f.endswith(".log"):
                out.append(os.path.join(dp, f))
    return sorted(out)


# ---------------------------------------------------------------- 五项判据
def check_narrative_persist(narr_dir: str, day: str) -> dict:
    """① 叙事落盘：文件在 + 三字段非空。

    ★字段可能在 `extra` 里（`row_of` 骨架外字段落 extra，铁律 27）⇒ 两处都看。
    """
    path = os.path.join(narr_dir, "%s%s%s" % (NARR_PREFIX, day, NARR_SUFFIX))
    if not os.path.isfile(path):
        others = sorted(glob.glob(os.path.join(narr_dir, "%s*%s" % (NARR_PREFIX, NARR_SUFFIX))))
        return _result("1 叙事落盘", False, {"path": path, "files": len(others)},
                       "当日文件不存在（同目录共 %d 个叙事文件）" % len(others))
    rows, bad = [], 0
    for ln in _read_text(path).split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError as _e:
            # 坏行是**预期内**的（历史行可能被截断），但要留痕：cw2 门禁不接受裸赋值
            silent_exc(_e, where="m415_deferred_verify.check_narrative_persist:bad_line",
                       level="debug")
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    if not rows:
        return _result("1 叙事落盘", False, {"rows": 0, "bad": bad}, "文件在但无有效行")

    ok_head = ok_intent = ok_delta = 0
    for r in rows:
        extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
        head = str(r.get("headline") or extra.get("headline") or "").strip()
        intent = str(r.get("intent") or extra.get("intent") or "").strip()
        # ★顶层 delta 先看有没有「有数的格」；没有（历史行常是空 dict/全 None）
        #   再退回 extra.delta —— 只按 isinstance 判会被空 dict 挡住（单测抓到的）
        cands = [r.get("delta"), extra.get("delta")]
        has_cell = any(
            isinstance(d, dict) and any(isinstance(v, dict) and v.get("cur") is not None
                                        for v in d.values())
            for d in cands)
        ok_head += bool(head)
        ok_intent += bool(intent)
        ok_delta += bool(has_cell)
    ok = ok_head and ok_intent and ok_delta
    return _result("1 叙事落盘", ok,
                   {"rows": len(rows), "bad": bad, "headline": ok_head,
                    "intent": ok_intent, "delta_with_cur": ok_delta},
                   "三字段非空：headline %d/%d、intent %d/%d、delta 有数 %d/%d"
                   % (ok_head, len(rows), ok_intent, len(rows), ok_delta, len(rows)))


def check_experience_template(pool_path: str) -> dict:
    """② 经验池模板标记：is_template 为真的条数 > 0。"""
    if not os.path.isfile(pool_path):
        return _result("2 经验池模板标记", False, {"path": pool_path}, "池文件不存在")
    try:
        with io.open(pool_path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="m415_deferred_verify.check_experience_template", level="warning")
        return _result("2 经验池模板标记", False, {"path": pool_path}, "池文件读不出 JSON")
    rows = obj.get("experiences") if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        return _result("2 经验池模板标记", False, {"path": pool_path}, "池结构不是 list")
    n_tpl = sum(1 for r in rows if isinstance(r, dict) and r.get("is_template"))
    n_flag = sum(1 for r in rows if isinstance(r, dict) and "is_template" in r)
    return _result("2 经验池模板标记", n_tpl > 0,
                   {"total": len(rows), "is_template": n_tpl, "has_field": n_flag},
                   "共 %d 条，is_template=True %d 条，带该字段 %d 条" % (len(rows), n_tpl, n_flag))


def check_o3_in_digest(digest_path: str) -> dict:
    """③ O3 dry-run 进夜摘要：第八段在 + eligible 数字能抓到。"""
    if not os.path.isfile(digest_path):
        return _result("3 O3 进夜摘要", False, {"path": digest_path}, "摘要文件不存在")
    text = _read_text(digest_path)
    has_head = O3_HEAD in text
    m = _RE_ELIGIBLE.search(text)
    elig = int(m.group(1)) if m else None
    sections = len(re.findall(r"^## ", text, re.M))
    return _result("3 O3 进夜摘要", has_head and elig is not None,
                   {"has_section": has_head, "eligible": elig, "sections": sections},
                   "第八段=%s，eligible=%s，摘要共 %d 段"
                   % ("在" if has_head else "缺", elig, sections))


def check_perf_delta(log_paths: Sequence[str], effect_verify_path: str = "") -> dict:
    """④ perf_delta 触发：生产日志里出现 `[412刀3] perf_delta before=` 实跑行。

    ★只认**生产日志**：`pytest*.log` 里的命中是单测产物（11-01 实测 6 条全在
      pytest.log），拿它当 PASS 就是假绿 ⇒ 分开计数，PASS 要求生产侧 >0。
    ★辅助证据：`data/evolution/effect_verify.json` 的 `verdicts[*].dims.perf_ok`
      非 None 的条数（第三维真有数）。只作参考，不改判 PASS —— 那 6 条里 3 条是
      `tp1~tp3` 测试桩、3 条是 `p3d/pperf/pab` 性能单测桩，都不是生产补丁。
    """
    hit, prod, scanned = 0, 0, 0
    first, first_prod = "", ""
    for p in log_paths:
        if not os.path.isfile(p):
            continue
        scanned += 1
        base = os.path.basename(p).lower()
        is_test = base.startswith("pytest") or "regression" in base
        for ln in _read_text(p).split("\n"):
            if PERF_MARK in ln:
                hit += 1
                if not first:
                    first = "%s: %s" % (os.path.basename(p), ln.strip()[:150])
                if not is_test:
                    prod += 1
                    if not first_prod:
                        first_prod = "%s: %s" % (os.path.basename(p), ln.strip()[:150])

    n_perf = -1
    if effect_verify_path and os.path.isfile(effect_verify_path):
        try:
            with io.open(effect_verify_path, encoding="utf-8") as fh:
                _ev = json.load(fh)
            _v = _ev.get("verdicts") if isinstance(_ev, dict) else None
            if isinstance(_v, dict):
                n_perf = sum(1 for r in _v.values() if isinstance(r, dict)
                             and isinstance(r.get("dims"), dict)
                             and r["dims"].get("perf_ok") is not None)
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="m415_deferred_verify.check_perf_delta", level="debug")

    detail = first_prod or (("生产日志 0 条；测试日志 %d 条（不算触发）" % hit) if hit
                            else "无实跑痕迹（%d 个日志里 0 条）" % scanned)
    return _result("4 perf_delta 触发", prod > 0,
                   {"hits": hit, "prod_hits": prod, "logs_scanned": scanned,
                    "verdicts_with_perf": n_perf},
                   detail)


def check_narrative_replay(loader: Callable[[], dict] | None = None) -> dict:
    """⑤ 叙事回放：重启后 `load_recent()` 能回放出条目。

    `loader` 可注入（单测用），默认走真 `narrative_store`。
    """
    if loader is None:
        def loader():  # 局部默认实现 · ★421/R1-B8：F811 不覆盖遮蔽形参
            from nucleus.observability import narrative_store as _ns
            return _ns.summary()
    try:
        s = loader() or {}
    except Exception as _e:  # noqa: BLE001 — 外部器官/文件都可能抛，如实记 FAIL
        silent_exc(_e, where="m415_deferred_verify.check_narrative_replay", level="warning")
        return _result("5 叙事回放", False, {"error": type(_e).__name__}, "回放调用抛异常")
    rows = s.get("replayed_rows")
    rows = int(rows) if isinstance(rows, (int, float)) else -1
    ok = rows > 0
    return _result("5 叙事回放", ok,
                   {"state": s.get("state"), "replayed_rows": rows,
                    "days_with_file": s.get("days_with_file"),
                    "continuous_days": s.get("continuous_days")},
                   "state=%s 回放 %s 条（有记录天数 %s，连续 %s 天）"
                   % (s.get("state"), rows, s.get("days_with_file"), s.get("continuous_days")))


# ---------------------------------------------------------------- 编排
def run_all(root: str, day: str, loader: Callable[[], dict] | None = None,
            log_paths: Sequence[str] | None = None) -> list[dict]:
    data = os.path.join(root, "data")
    narr_dir = os.path.join(data, "narrative")
    pool = os.path.join(data, "experience", "experience_pool.json")
    digest = os.path.join(data, "reports", "digest_%s.md" % day)
    logs = list(log_paths) if log_paths is not None else _walk_logs(os.path.join(root, "logs"))
    out = [
        check_narrative_persist(narr_dir, day),
        check_experience_template(pool),
        check_o3_in_digest(digest),
        check_perf_delta(logs, os.path.join(data, "evolution", "effect_verify.json")),
        check_narrative_replay(loader),
    ]
    return out


def _print_human(results: Sequence[dict], day: str) -> None:
    n_ok = sum(1 for r in results if r["ok"])
    print("=" * 78)
    print("415 刀3 · deferred 五项验证   day=%s   %d/%d PASS" % (day, n_ok, len(results)))
    print("=" * 78)
    for r in results:
        print("[%s] %s" % ("PASS" if r["ok"] else "FAIL", r["check"]))
        print("       实测：%s" % json.dumps(r["value"], ensure_ascii=False))
        if r["detail"]:
            print("       说明：%s" % r["detail"])
    print("-" * 78)
    print("提示：叙事/经验池/摘要在 04:00 由夜间编排产出；04:00 之前跑必然 FAIL 1/3/5。")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="415 deferred 五项验证（只读）")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--day", default=time.strftime("%Y%m%d"))
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    try:
        results = run_all(args.root, args.day)
    except Exception as _e:  # noqa: BLE001 — 顶层兜底，如实报 2
        silent_exc(_e, where="m415_deferred_verify.main", level="error")
        print("脚本自身异常：%s: %s" % (type(_e).__name__, _e))
        return 2
    if args.json:
        print(json.dumps({"day": args.day, "results": results,
                          "passed": sum(1 for r in results if r["ok"])},
                         ensure_ascii=False, indent=1))
    else:
        _print_human(results, args.day)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
