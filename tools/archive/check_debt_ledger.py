#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_debt_ledger.py — 技术债务台账双轨计数与归一校验（只读 + 退出码）。

用途：复现「池票 33 清 / 152 开 / 0 销」「现场票 5 清 / 7 开 / 1 销」「infra 1 清 / 12 开 / 0 销」三轨口径（第121批 T-121a 刷新），
      与 docs/完整进化路线与技术债务清单_v1.0.md §1.2 三轨表交叉验证。
红线：工具不做自动写；写回由人。仅读 + 打印 + 退出码。

计数规则（第119批刷新口径：T-119b① 7 票注销 + T-119c D186-D207 补录后基线；
  T-119b②(17占位裁决)/③(20 R4组) 因任务书未给票号、dz_p0p1_verify.json 路灯未拿到，本批未做，
  待星轨补清单后基线再降）：
  - 轨道列：空 或 含 'pool' → pool；含 'site' → site（site 票终身 site）；含 'infra' → infra（账本/工具/测试/勘误四类，不计入完成率，SOP §5）。
  - 状态判定（清优先于销）：
      清：实查/处置含 已清 / 已闭环 / 关闭 / 码级已清 / 已修复
      销：含 注销 / 已销号
      开：其余（含『待录入』占位 → 计开放，诚实口径）
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "docs", "分析报告", "技术债务台账_代码实查_20260919.csv")

TARGET = {
    "pool": {"清": 33, "开": 152, "销": 0},
    "site": {"清": 5, "开": 7, "销": 1},
    "infra": {"清": 1, "开": 12, "销": 0},
}


def classify(text: str) -> str:
    s = text or ""
    if "注销" in s or "已销号" in s:
        return "销"
    # 第120批 T-120c：码级已清 必须叠加 runtime已验(✅) 才算清，修复"已清"子串误匹配陷阱
    if "码级已清" in s:
        if "runtime已验" in s or "✅" in s:
            return "清"
        return "开"
    if any(k in s for k in ("已清", "已闭环", "关闭", "已修复")):
        return "清"
    if "runtime已验" in s and "✅" in s:
        return "清"
    return "开"


def track_of(value: str) -> str:
    v = value or ""
    if "site" in v:
        return "site"
    if "infra" in v:
        return "infra"
    return "pool"  # 空 或 pool（含 'pool·117在途' 等）均归 pool


def main() -> int:
    if not os.path.isfile(CSV):
        print(f"[ERR] 台账缺失: {CSV}")
        return 2
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    stat = {tr: {"清": 0, "开": 0, "销": 0} for tr in ("pool", "site", "infra")}
    for r in rows:
        tr = track_of(r.get("轨道", ""))
        c = classify((r.get("实查状态", "") or "") + " " + (r.get("处置建议", "") or ""))
        stat[tr][c] += 1

    print("=" * 60)
    print("技术债务台账 · 三轨计数（tools/check_debt_ledger.py，第121批 T-121a）")
    print("=" * 60)
    print(f"{'轨道':<8}{'已清':>6}{'开放':>6}{'销号':>6}{'合计':>6}   目标(清/开/销)")
    ok = True
    for tr in ("pool", "site", "infra"):
        s = stat[tr]
        tot = s["清"] + s["开"] + s["销"]
        tgt = TARGET[tr]
        flag = "OK" if (s["清"] == tgt["清"] and s["开"] == tgt["开"] and s["销"] == tgt["销"]) else "DIFF"
        if flag == "DIFF":
            ok = False
        print(f"{tr:<8}{s['清']:>6}{s['开']:>6}{s['销']:>6}{tot:>6}   {tgt['清']}/{tgt['开']}/{tgt['销']} [{flag}]")
    pool_tot = sum(stat["pool"].values())
    site_tot = sum(stat["site"].values())
    infra_tot = sum(stat["infra"].values())
    grand = pool_tot + site_tot + infra_tot
    print("-" * 60)
    print(f"池票={pool_tot} | 现场票={site_tot} | infra={infra_tot} | 总={grand} (目标211)")
    if grand != 211:
        ok = False
        print("⚠ 总数未对齐目标211：D208-D211补录后共211票，若再变需重算基线。")
    # 完成率（不含 infra 轨，per SOP §5：infra 不计入完成率分母）
    prod = pool_tot + site_tot
    prod_clear = stat["pool"]["清"] + stat["site"]["清"]
    rate = (prod_clear / prod * 100) if prod else 0.0
    print(f"生产完成率(不含infra)= {prod_clear}/{prod} = {rate:.1f}%")
    print("=" * 60)
    if ok:
        print("✅ 三轨计数与 TARGET 一致（infra 不计入完成率）")
        return 0
    print("❌ 三轨计数与目标不一致（见上）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
