#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第162批刀15 · 重锚分类授权助手（非阻断，仅供星轨核签参考）。

触发：commit-msg hook 在检测到 [ci-gate-change] 且本次提交含 CI 基线文件时调用。

职责：
1. 扫描暂存区中的基线 JSON（tools/ci/baselines/*.json、red_baseline_*.json 等）；
2. 区分 A 类（用例增减型，collect 节点变化可由本批新增/删除测试文件解释）与
   B 类（known_fail / pollution_set 内容变化，红线不自动化放行）；
3. 输出三字段归因桶：新增用例数 / 口径调整 / 真回归。

退出码：恒为 0（不阻断提交，仅信息输出到 stderr）。
"""
import json
import os
import subprocess
import sys

PROJECT_ROOT = subprocess.check_output(
    ["git", "rev-parse", "--show-toplevel"], text=True
).strip()


def _staged_files():
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"], text=True
    )
    return [l.strip() for l in out.splitlines() if l.strip()]


def _baseline_files(staged):
    hits = []
    for f in staged:
        if f.startswith("tools/ci/") and f.endswith(".json"):
            hits.append(f)
    return hits


def _diff_json(path):
    """返回 (old_dict, new_dict) 或 (None, None) 若无法解析。"""
    try:
        new_raw = subprocess.check_output(
            ["git", "show", f":{path}"], cwd=PROJECT_ROOT, text=True
        )
        new = json.loads(new_raw)
    except Exception as e:
        print(f"[刀15-分类] 解析异常（不影响分类，exit 0）：{e}", file=sys.stderr)
        new = None
    # 工作树版本（暂存版本 = 工作树，因已 add）
    try:
        with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as fh:
            cur = json.load(fh)
    except Exception as e:
        print(f"[刀15-分类] 解析异常（不影响分类，exit 0）：{e}", file=sys.stderr)
        cur = None
    old = new  # git 暂存前版本（HEAD 或 index 父）
    return old, cur


def _test_changes(staged):
    added = [f for f in staged if f.startswith("tests/") and f.endswith(".py")]
    deleted = []
    try:
        d = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=D", "--", "tests/"],
            text=True,
        )
        deleted = [l.strip() for l in d.splitlines() if l.strip()]
    except Exception as e:
        print(f"[刀15-分类] 解析异常（不影响分类，exit 0）：{e}", file=sys.stderr)
        pass
    return added, deleted


def classify():
    staged = _staged_files()
    bases = _baseline_files(staged)
    if not bases:
        print("[刀15-分类] 本次提交未含 CI 基线文件，非重锚提交，跳过分类。", file=sys.stderr)
        return

    collect_delta = None
    b_class_signal = False  # known_fail / pollution 变化
    caliber_adjust = False
    for b in bases:
        old, cur = _diff_json(b)
        if old is None or cur is None:
            continue
        # collect 节点数漂移
        oc = old.get("collect_baseline")
        cc = cur.get("collect_baseline")
        if isinstance(oc, int) and isinstance(cc, int) and oc != cc:
            collect_delta = (oc, cc)
        # B 类信号：known_fail / pollution_set 集合变化
        for key in ("known_fail", "pollution_set"):
            if set(old.get(key, [])) != set(cur.get(key, [])):
                b_class_signal = True
        # 其余字段（env_fingerprint / notes / red_baseline_notes 等）变化 → 口径调整
        for key in old:
            if key in ("collect_baseline", "known_fail", "pollution_set"):
                continue
            if old.get(key) != cur.get(key):
                caliber_adjust = True

    added, deleted = _test_changes(staged)
    new_test_n = len(added)
    del_test_n = len(deleted)

    # 归因判定
    explained = new_test_n > 0 or del_test_n > 0
    if b_class_signal:
        cls = "B"
        note = "红面/污染集合变化 → 维持人工裁决 + 独立 [ci-gate-change] 提交（红线不自动化放行）"
    elif collect_delta is not None and explained:
        cls = "A"
        note = "collect 节点变化可由本批新增/删除测试文件解释 → 批末由星轨核签后一次性重锚"
    elif collect_delta is not None and not explained:
        cls = "A?"
        note = "collect 变化但本批无测试文件增减 → 疑似口径调整或真回归，需星轨复核"
    else:
        cls = "A"
        note = "仅口径/备注调整，无 collect 漂移"

    real_regression = "否"
    if collect_delta is not None and not explained and not caliber_adjust:
        real_regression = "需复核"

    print("=" * 60, file=sys.stderr)
    print(f"[刀15-分类] 重锚分类 = {cls} 类", file=sys.stderr)
    print(f"  基线文件: {', '.join(bases)}", file=sys.stderr)
    if collect_delta is not None:
        print(f"  collect_baseline: {collect_delta[0]} → {collect_delta[1]} "
              f"(Δ={collect_delta[1]-collect_delta[0]})", file=sys.stderr)
    print(f"  三字段归因桶: 新增用例数={new_test_n} / 口径调整={'是' if caliber_adjust else '否'} "
          f"/ 真回归={real_regression}", file=sys.stderr)
    print(f"  说明: {note}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    try:
        classify()
    except Exception as e:
        print(f"[刀15-分类] 分类助手异常（不影响提交）：{e}", file=sys.stderr)
    sys.exit(0)
