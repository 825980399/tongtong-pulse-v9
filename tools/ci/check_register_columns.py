#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""176批段7-2 登记册列完整性门禁（路灯只交脚本；数据修复由星轨应用，红线④）。

校验 docs/台账/待裁决登记册.csv 每行：
  - 列数 = 9（ID, 提出方, 提出批次, 摘要, P级, 状态, 到期批次, 裁决批次, 裁决内容）
  - P级 ∈ {P0, P1, P2, P3}
  - 到期批次 为正整数（批次号）

状态集合不做硬阻断（仅软提示），避免误伤历史值；列完整性与 P级/到期批次
为硬门禁，任一违规即退出码 2（供挂门禁时阻断提交）。

★与 172 刀0 thread_spawn 排除清单同族：纯结构校验，零业务逻辑。
数据修复（8+ 错位行 P级/到期列被摘要文本占据）由星轨配合应用（红线④：
登记册 CSV 星轨写），路灯只交本脚本；挂门禁须待数据修复后，避免误阻。

退出码：0=通过，2=存在阻断性列完整性违规。
"""
import argparse
import csv
import os
import sys

EXPECTED_COLS = 9
P_LEVELS = {"P0", "P1", "P2", "P3"}
DEFAULT_CSV = os.path.join("docs", "台账", "待裁决登记册.csv")


def _main():
    ap = argparse.ArgumentParser(description="登记册列完整性门禁")
    ap.add_argument("--csv", default=None, help="登记册 CSV 路径（默认 仓库根/docs/台账/待裁决登记册.csv）")
    ap.add_argument("--root", default=".", help="仓库根目录")
    args = ap.parse_args()
    path = args.csv or os.path.join(args.root, DEFAULT_CSV)
    if not os.path.isfile(path):
        print("[register-cols] 未找到登记册：%s" % path)
        return 2
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        print("[register-cols] 空文件：%s" % path)
        return 2
    header = rows[0]
    if len(header) != EXPECTED_COLS:
        print("[register-cols] 表头列数=%d（期望 %d）：%s" % (len(header), EXPECTED_COLS, header))
        return 2
    pidx = header.index("P级")
    eidx = header.index("到期批次")
    sidx = header.index("状态")
    fails = []
    for ln, r in enumerate(rows[1:], start=2):
        if len(r) != EXPECTED_COLS:
            fails.append((ln, "列数=%d（期望 %d）" % (len(r), EXPECTED_COLS)))
            continue
        p = (r[pidx] or "").strip()
        if p not in P_LEVELS:
            fails.append((ln, "P级='%s' 不在 {P0..P3}" % p))
        # 已撤销票无排期语义：到期批次为空属合法，豁免检查（其余状态必须正整数）
        if (r[sidx] or "").strip() != "已撤销":
            exp = (r[eidx] or "").strip()
            if not exp.isdigit() or int(exp) <= 0:
                fails.append((ln, "到期批次='%s' 非正整数" % exp))
    if fails:
        print("[register-cols] 发现 %d 处列完整性违规（数据修复后须归零）：" % len(fails))
        for ln, msg in fails:
            print("  行 %d: %s" % (ln, msg))
        return 2
    print("[register-cols] OK：%d 行全部列完整（9列 / P级∈P0..P3 / 到期批次正整数）" % (len(rows) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
