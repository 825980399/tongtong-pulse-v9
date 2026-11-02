# -*- coding: utf-8 -*-
"""408 刀2：清理「``example_question`` 不是问题」的低质推理经验记录。

背景（408 T0 实测，不是推测）
------------------------------
`data/context/reasoning_experience.json` 共 128 条，其中
**98 条已被 407 批标为污染**（`polluted=True` / `is_cleaned=False`），
**30 条干净记录参与打分**。这 30 条里有一批的 `example_question` 存的
根本不是问题：

* ``answer_leak``     —— 字段里混进了**回答正文**（`[自主推导参考]` / `[归纳升华]`）
* ``serp_query``      —— 搜索引擎串（``site:baike.baidu.com … -拼音 -部首 -笔顺 -怎么读``）
* ``template_frag``   —— 框架自己拼的内化指令（``内化"向善"本能——反思温柔``）
* ``self_meta``       —— 模型自述（``我需要更多的一手资料…``）

★**为什么不用任务书原来的判据**（"不以疑问词开头 / 不含问号 / 是陈述句"）：
  该判据用的是**缺席证据**。408 T0 实测它会把 20 条判成"陈述句"，其中
  ``你是否包含身份相关的知识``、``1+1 等于几``、``你是谁``、``现象：…请你分析``
  都是**真问题** —— 只是没写问号。按"没问号"一刀切 = 误删真经验。
  所以本工具只用**正面证据**（上面四类），并另设 ``--tier2`` 弱判据
  （陈述句/主题片段）**默认关闭、只报告**。

★**标记，不删除**：写入的字段全部是既有的
（``polluted`` / ``is_cleaned`` / ``pollution_reason`` / ``pollution_risk`` /
``cleanup_reason`` / ``cleanup_batch`` / ``cleanup_at``），
``--restore`` 可按批次整体回滚。写盘前强制备份 + sha256 双向校验。

用法
----
::

    # 只读体检（默认 dry-run）
    python tools/m408_clean_low_quality_experience.py

    # 连带看清 tier2 弱判据会多标哪些（仍然不写）
    python tools/m408_clean_low_quality_experience.py --tier2

    # 执行（自动备份 → 标记 → 写盘 → sha256 校验 → 落审计）
    python tools/m408_clean_low_quality_experience.py --apply

    # 回滚本批次
    python tools/m408_clean_low_quality_experience.py --restore

    # 顺带测 best_score 前后变化（要 import 推理经验类，慢一点）
    python tools/m408_clean_low_quality_experience.py --measure
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc                      # noqa: E402
from nucleus.data.experience_cleanup import (                      # noqa: E402
    CLASS_LOW_QUALITY_STATEMENT, LQ_ANSWER_LEAK, LQ_SELF_META, LQ_SERP_QUERY,
    LQ_STATEMENT_ONLY, LQ_TEMPLATE_FRAG, is_retrievable, mark_low_quality,
    restore_low_quality, scan_low_quality)

BATCH = 408
DEFAULT_POOL = os.path.join(_ROOT, "data", "context", "reasoning_experience.json")
DEFAULT_BACKUP = os.path.join(_ROOT, "data", "context",
                              "reasoning_experience.pre408.json")
AUDIT_DIR = os.path.join(_ROOT, "data", "monitor")

#: 子类的人话说明（报告里用；未知子类原样显示）
SUB_LABEL = {
    LQ_ANSWER_LEAK: "存的是回答正文",
    LQ_SERP_QUERY: "搜索引擎串",
    LQ_TEMPLATE_FRAG: "框架模板片段",
    LQ_SELF_META: "模型自述",
    LQ_STATEMENT_ONLY: "陈述句/主题片段（弱判据）",
}

#: best_score 探针问题 —— 覆盖库里出现过的几类真问题形态
PROBE_QUESTIONS = (
    "请归纳知识压缩和知识融合的共同特点",
    "请推导规则1→A→B",
    "你是否包含身份相关的知识",
    "你是谁",
    "动机循环有哪 4 个压力指标",
    "1+1 等于几",
    "请把这套架构类比到现实世界中的一家公司",
    "请列举三种可能的性能瓶颈",
    "你能解释一下什么是自我理解吗",
)


# ------------------------------------------------------------------ 工具函数
def _rel(path: str) -> str:
    """显示用相对路径（★跨盘符时 os.path.relpath 会抛 ValueError，退回绝对路径）。"""
    try:
        return os.path.relpath(path, _ROOT)
    except ValueError as e:
        silent_exc(e, where="tools.m408_clean_low_quality_experience::_rel", level="debug")
        return path


def sha256(path: str) -> str:
    """文件 sha256（不存在返回空串）。"""
    if not os.path.isfile(path):
        return ""
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def load_pool(path: str) -> dict:
    """读经验池；兼容 ``{"experiences": [...]}`` 与裸列表。"""
    with open(path, encoding="utf-8") as f:
        _d = json.load(f)
    return _d if isinstance(_d, dict) else {"experiences": _d}


def records_of(doc: dict) -> list:
    """取记录列表（不拷贝 —— 标记要写回同一批对象）。"""
    _r = doc.get("experiences")
    return _r if isinstance(_r, list) else []


def _eol(path: str) -> str:
    """判原文件主导行尾（★408 T0 实测本池 3571 CRLF / 0 裸 LF）。

    ★不能无脑写 ``newline="\\n"``：那会把 156KB 的经验池整体翻成 LF，
      文件在 git 里是 data/（已 ignore）看不见这个差异，但下一次框架
      自写会翻回 CRLF，于是 sha256 反复跳 —— 排查时会误判成"有人在改我数据"。
    """
    if not os.path.isfile(path):
        return "\n"
    _b = open(path, "rb").read()
    return "\r\n" if _b.count(b"\r\n") else "\n"


def save_pool(path: str, doc: dict) -> None:
    """原子写盘（``.m408tmp`` → ``os.replace``），行尾随原文件。"""
    _tmp = path + ".m408tmp"
    with open(_tmp, "w", encoding="utf-8", newline=_eol(path)) as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(_tmp, path)


def measure(path: str) -> dict:
    """用真实的 ``ReasoningExperience.search()`` 量 best_score（不可用则返回 skipped）。"""
    try:
        from nucleus.mnemosyne.ReasoningExperience import ReasoningExperience
    except Exception as e:
        silent_exc(e, where="tools.m408_clean_low_quality_experience::measure import", level="debug")
        return {"skipped": "import_failed", "error": type(e).__name__}
    _dir = os.path.dirname(path)
    if os.path.basename(path) != "reasoning_experience.json":
        return {"skipped": "not_default_name"}
    try:
        _re = ReasoningExperience(data_dir=_dir)
    except Exception as e:
        silent_exc(e, where="tools.m408_clean_low_quality_experience::measure ctor", level="debug")
        return {"skipped": "ctor_failed", "error": type(e).__name__}
    _rows = []
    for _q in PROBE_QUESTIONS:
        try:
            _re.search(_q)
            _diag = getattr(_re, "last_search_diagnostics", {}) or {}
        except Exception as e:
            silent_exc(e, where="tools.m408_clean_low_quality_experience::measure search", level="debug")
            _diag = {"best_score": -1.0, "hit": False, "error": type(e).__name__}
        _rows.append({"q": _q,
                      "best_score": _diag.get("best_score"),
                      "hit": bool(_diag.get("hit")),
                      "n_polluted": _diag.get("n_excluded_polluted")})
    _hit = [r for r in _rows if r["hit"]]
    return {"rows": _rows,
            "n_probe": len(_rows),
            "n_hit": len(_hit),
            "max_best_score": max([r["best_score"] or 0.0 for r in _rows] or [0.0]),
            "min_best_score": min([r["best_score"] or 0.0 for r in _rows] or [0.0])}


def _stats(recs: list) -> dict:
    _retr = [e for e in recs if is_retrievable(e)]
    _lq = [e for e in recs
           if e.get("cleanup_reason") == CLASS_LOW_QUALITY_STATEMENT]
    _by_sub = {}
    for e in _lq:
        _s = str(e.get("pollution_reason") or "unknown")
        _by_sub[_s] = _by_sub.get(_s, 0) + 1
    return {"total": len(recs), "retrievable": len(_retr),
            "marked_low_quality": len(_lq), "marked_by_sub": _by_sub}


def fmt_measure(m: dict, tag: str) -> str:
    """把 ``measure()`` 的结果压成一行（跳过/失败也要说清楚）。"""
    if not m:
        return "best_score（%s）：未测" % tag
    if m.get("skipped"):
        return "best_score（%s）：跳过（%s）" % (tag, m["skipped"])
    return ("best_score（%s）：%s 条探针，上限 %.3f，区间 %.3f~%.3f，命中 %s 条"
            % (tag, m.get("n_probe"), float(m.get("max_best_score") or 0.0),
               float(m.get("min_best_score") or 0.0),
               float(m.get("min_best_score") or 0.0), m.get("n_hit")))


def _print_scan(rows: list) -> None:
    if not rows:
        print("  （无）")
        return
    for _r in rows:
        print("  [%-14s] #%-3d %r" % (_r["sub"], _r["index"], _r["question"]))


def _write_audit(report: dict) -> str:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    _p = os.path.join(AUDIT_DIR, "m408_experience_cleanup_%s.json"
                      % time.strftime("%Y%m%d"))
    with open(_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return _p


# ------------------------------------------------------------------ 主流程
def main(argv=None) -> int:                                       # noqa: C901
    _ap = argparse.ArgumentParser(description="408 刀2 低质经验记录清理")
    _ap.add_argument("--file", default=DEFAULT_POOL, help="经验池路径")
    _ap.add_argument("--backup", default=DEFAULT_BACKUP, help="备份路径")
    _ap.add_argument("--batch", type=int, default=BATCH, help="批次号（默认 408）")
    _ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run）")
    _ap.add_argument("--restore", action="store_true", help="回滚本批次标记")
    _ap.add_argument("--tier2", action="store_true",
                     help="启用弱判据（陈述句/主题片段），默认只做正面证据档")
    _ap.add_argument("--measure", action="store_true",
                     help="顺带量 best_score（import 推理经验类）")
    _ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    _a = _ap.parse_args(argv)

    if not os.path.isfile(_a.file):
        print("✗ 经验池不存在：%s" % _a.file)
        return 2

    _doc = load_pool(_a.file)
    _recs = records_of(_doc)
    _before = _stats(_recs)
    _sha_before = sha256(_a.file)
    _report: dict = {"batch": _a.batch, "file": _a.file, "tier2": bool(_a.tier2),
                     "dry_run": not _a.apply, "sha256_before": _sha_before,
                     "stats_before": _before,
                     "measure_before": measure(_a.file) if _a.measure else None}

    # ---------------- 回滚
    if _a.restore:
        _r = restore_low_quality(_recs, batch_no=_a.batch)
        save_pool(_a.file, _doc)
        _after = _stats(records_of(load_pool(_a.file)))
        _report.update({"restore": _r, "stats_after": _after,
                        "sha256_after": sha256(_a.file)})
        print("回滚批次 %d：%d 条" % (_a.batch, _r["restored"]))
        print("可检索 %d → %d ／ 低质标 %d → %d" % (
            _before["retrievable"], _after["retrievable"],
            _before["marked_low_quality"], _after["marked_low_quality"]))
        if _a.measure:
            print("best_score 复测：%s" % json.dumps(
                measure(_a.file), ensure_ascii=False)[:400])
        if _a.json:
            print(json.dumps(_report, ensure_ascii=False, indent=1))
        _write_audit(_report)
        return 0

    # ---------------- 扫描
    _rows = scan_low_quality(_recs, tier2=_a.tier2)
    _by_sub: dict = {}
    for _r in _rows:
        _by_sub[_r["sub"]] = _by_sub.get(_r["sub"], 0) + 1
    print("=" * 70)
    print("408 刀2 低质经验记录扫描  文件=%s" % _rel(_a.file))
    print("  总 %d 条 / 可检索 %d 条 / 已标低质 %d 条 / 本档命中 %d 条" % (
        _before["total"], _before["retrievable"],
        _before["marked_low_quality"], len(_rows)))
    for _s, _n in sorted(_by_sub.items(), key=lambda x: -x[1]):
        print("    - %-14s %2d 条  %s" % (_s, _n, SUB_LABEL.get(_s, "")))
    if not _a.tier2:
        print("  （弱判据档 --tier2 未启用；正面证据档只有上面这几类）")
    print("-" * 70)
    _print_scan(_rows)
    print("-" * 70)

    # ---------------- 标记（dry-run 时只统计）
    _mk = mark_low_quality(_recs, batch_no=_a.batch, dry_run=not _a.apply,
                           tier2=_a.tier2)
    _report["scan"] = {"n": len(_rows), "by_sub": _by_sub, "rows": _rows}
    _report["mark"] = _mk
    print("标记：%s" % json.dumps(_mk, ensure_ascii=False))

    if not _a.apply:
        _report["stats_after"] = _before
        if _a.measure:
            print(fmt_measure(_report.get("measure_before") or {}, "清理前"))
        print("★dry-run —— 未写盘。确认无误后加 --apply。")
        if _a.json:
            print(json.dumps(_report, ensure_ascii=False, indent=1))
        return 0

    # ---------------- 备份 → 写盘 → 校验
    if not os.path.isfile(_a.backup):
        shutil.copy2(_a.file, _a.backup)
        print("备份已建：%s" % _rel(_a.backup))
    else:
        print("备份已存在（不覆盖）：%s" % _rel(_a.backup))
    _sha_bk = sha256(_a.backup)
    _report["backup"] = _a.backup
    _report["sha256_backup"] = _sha_bk
    _report["backup_matches_source"] = (_sha_bk == _sha_before)
    if _sha_bk != _sha_before:
        print("✗ 备份与源不一致（备份是旧的？）—— 拒绝写盘。"
              "请先人工确认后再用 --backup 指向新路径。")
        return 3

    save_pool(_a.file, _doc)
    _sha_after = sha256(_a.file)
    _doc2 = load_pool(_a.file)
    _after = _stats(records_of(_doc2))
    _report["stats_after"] = _after
    _report["sha256_after"] = _sha_after
    if _a.measure:
        _report["measure_after"] = measure(_a.file)

    print("-" * 70)
    print("写盘完成：sha256 %s → %s" % (_sha_before[:12], _sha_after[:12]))
    print("可检索 %d → %d 条；低质标 %d → %d 条" % (
        _before["retrievable"], _after["retrievable"],
        _before["marked_low_quality"], _after["marked_low_quality"]))
    print("落盘复核（重新读回）：%s" % json.dumps(_after, ensure_ascii=False))
    if _a.measure:
        print(fmt_measure(_report.get("measure_before") or {}, "清理前"))
        print(fmt_measure(_report.get("measure_after") or {}, "清理后"))
    _ap2 = _write_audit(_report)
    print("审计：%s" % _rel(_ap2))
    if _a.json:
        print(json.dumps(_report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
