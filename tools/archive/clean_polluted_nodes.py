#!/usr/bin/env python
"""clean_polluted_nodes —— clean_polluted_nodes.py — 知识库错误节点标记工具（不删除，仅标记）

版本: v10 PulseNet · 工具
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

import argparse
import json
import os
import shutil
import sys
from nucleus.data.DataAccessLayer import safe_read_json

# 项目根目录（脚本位于 tools/ 下，向上一级即项目根）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SNAPSHOTS = [
    os.path.join(_PROJECT_ROOT, "data", "knowledge", "pulse_knowledge_snapshot.json"),
    os.path.join(_PROJECT_ROOT, "data", "knowledge", "pulse_l1_snapshot.json"),
]

MARK_PREFIX = "[已标记错误-待清理] "


def _get(n: dict, key: str) -> str:
    return str(n.get(key, "") or "")


def match_r1(n: dict):
    """特斯拉物理共振错误定义（排除 SEED 否定句）。"""
    v = _get(n, "value")
    path = _get(n, "space_path")
    if "特斯拉" in v and "共振" in path and "不是" not in v:
        return "R1:特斯拉物理共振错误定义"
    return None


def match_r2(n: dict):
    """自言自语 / 梦境推演噪声节点。"""
    v = _get(n, "value")
    if v.startswith("我了解到"):
        return "R2:自言自语节点(以'我了解到'开头)"
    if "【种子记忆】[自我状态]" in v:
        return "R2:种子记忆+自我状态噪声"
    return None


def match_r3(n: dict):
    """把用户问题误存为知识节点的污染（共振引擎路径下的问题形式文本）。"""
    v = _get(n, "value")
    path = _get(n, "space_path")
    if "共振引擎" not in path:
        return None
    if (v.startswith(("什么是", "什么是")) or v.endswith(("是什么", "是什么？", "？", "?"))):
        return "R3:问题被误存为知识节点(共振引擎路径)"
    return None


def match_r4(n: dict):
    """仅扫描候选，不标记。"""
    v = _get(n, "value")
    if v.startswith("我了解到"):
        return "R4候选:自言自语节点"
    if "【种子记忆】" in v and len(v) > 500:
        return "R4候选:过长的种子记忆节点"
    if "此刻的我——" in v:
        return "R4候选:此刻的我独白"
    return None


def process(snapshot: str, dry_run: bool) -> dict:
    """处理单个快照，返回统计字典。"""
    if not os.path.exists(snapshot):
        print(f"\n[跳过] 文件不存在: {snapshot}")
        return {"total": 0, "marked": 0, "r4": 0}

    data = safe_read_json(snapshot, default={})

    nodes = data.get("nodes", [])
    marked = []
    r4_candidates = []

    for n in nodes:
        v = _get(n, "value")
        if v.startswith(MARK_PREFIX):
            continue  # 已标记过，跳过避免重复
        r = match_r1(n) or match_r2(n) or match_r3(n)
        if r:
            marked.append((n.get("node_id"), _get(n, "space_path"), v[:50], r))
            if not dry_run:
                n["value"] = MARK_PREFIX + v
        r4 = match_r4(n)
        if r4:
            r4_candidates.append((n.get("node_id"), _get(n, "space_path"), v[:50], r4))

    if marked and not dry_run:
        bak = snapshot + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(snapshot, bak)
            print(f"[备份] 已生成 {bak}")
        else:
            print(f"[备份] 已存在 {bak}，跳过覆盖")
        with open(snapshot, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n=== {os.path.basename(snapshot)} ===")
    print(f"总节点: {len(nodes)} | 匹配待标记: {len(marked)} | R4候选: {len(r4_candidates)}")
    for nid, path, val, reason in marked:
        print(f"  [标记] {reason}")
        print(f"      id  = {nid}")
        print(f"      path= {path}")
        print(f"      val = {val}")
    for nid, path, val, reason in r4_candidates:
        print(f"  [R4候选-不标记] {reason}")
        print(f"      id  = {nid}")
        print(f"      path= {path}")
        print(f"      val = {val}")

    return {"total": len(nodes), "marked": len(marked), "r4": len(r4_candidates)}


def main():
    p = argparse.ArgumentParser(description="标记知识库错误节点（不删除）")
    p.add_argument("--apply", action="store_true",
                   help="实际写入标记（默认 --dry-run 仅扫描打印候选列表）")
    p.add_argument("--snapshots", nargs="*", default=DEFAULT_SNAPSHOTS,
                   help="指定快照文件路径（默认扫描两个官方快照）")
    args = p.parse_args()
    dry = not args.apply

    print("=" * 64)
    print(f"[模式] {'DRY-RUN（仅扫描打印，不写文件）' if dry else 'APPLY（实际标记并自动备份）'}")
    print("=" * 64)

    total_marked = 0
    for s in args.snapshots:
        stats = process(s, dry)
        total_marked += stats["marked"]

    print("\n" + "=" * 64)
    print(f"汇总：待标记节点总数 = {total_marked}")
    if dry and total_marked > 0:
        print("以上为预演结果。确认无误后加 --apply 实际标记（会自动备份 .bak）。")
    elif not dry and total_marked > 0:
        print("已标记完成，并已备份原文件为 .bak。重启框架后本地推理将不再返回这些节点。")
    print("=" * 64)


if __name__ == "__main__":
    sys.exit(main())
