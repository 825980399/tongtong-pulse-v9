#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-脉冲纪律回收-1（169批 C10）只读审计：PulseController sleep 纪律核验。

背景（T0 实测，169批）：
  PulseController 共 12 处 time.sleep（任务书称 11 处）。逐处核实其语义后：
  - 仅 `_keepalive_loop` 的保活节拍 sleep 属「脉冲纪律」范畴，
    且已在 163 批刀8 完成心跳化（`wait_heartbeat_pulse` + 灰度
    `MAIN_LOOP_PULSE_ENABLED`，默认 OFF ⇒ 零行为变化）；
  - 其余 10 处均为**浏览器自动化业务延时**（拟人思考/点击间隔/重试退避/
    终止信号轮询/窗口清理消化）。把它们改成心跳等待会破坏功能：
    延时被心跳提前唤醒 ⇒ 拟人节奏失效、页面未消化即关窗。
    故这 10 处已打 `# [pulse-sleep-exempt]` 分类标记（豁免白名单）。

本工具（**只读，绝不改文件**）核验两件事：
  1) 主循环保活：灰度开启时代码走 `wait_heartbeat_pulse` 而非裸 sleep；
  2) 业务延时豁免：`time.sleep` 调用点全部带 `[pulse-sleep-exempt]` 标记，
     即「新增未分类 sleep」会被判 FAIL —— 防止未来把业务延时误当纪律改掉，
     也防止真正的纪律问题被随手标记掩盖。

用法：
  python tools/pulse_sleep_audit.py            # 人类可读
  python tools/pulse_sleep_audit.py --json     # 机读
退出码：0=PASS，1=FAIL。
"""
import argparse
import ast
import io
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join("organs", "motor", "PulseController.py")
EXEMPT_TAG = "[pulse-sleep-exempt]"

#: 保活 sleep 允许存在的唯一形态：灰度开关分流（脉冲优先 / OFF 退化裸 sleep）。
#:   灰度 ON  → wait_heartbeat_pulse(...)（事件驱动）
#:   灰度 OFF → time.sleep(60)          （零行为变化退路）
KEEPALIVE_DEF = "_keepalive_loop"


def _read_lines():
    p = os.path.join(PROJECT_ROOT, TARGET)
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n").split("\n")


def _enclosing_funcs(tree):
    """返回 {lineno(1-based): func_name}（取**最内层**函数名）。

    ★嵌套函数须取最内层：`_start_keepalive_timer` 内定义 `_keepalive_loop`，
    保活 sleep 归属 `_keepalive_loop`，若取最外层会被误判成业务延时。
    """
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            if ln not in out or node.lineno > out[ln][0]:
                out[ln] = (node.lineno, node.name)
    return {ln: name for ln, (_start, name) in out.items()}


def audit():
    lines = _read_lines()
    src = "\n".join(lines)
    tree = ast.parse(src)
    owner = _enclosing_funcs(tree)

    sleeps = []
    for i, line in enumerate(lines):
        if "time.sleep(" not in line:
            continue
        # 注释行里的说明性提及不算调用点
        st = line.strip()
        if st.startswith("#"):
            continue
        ln = i + 1
        # 豁免标记须**紧邻上一行**（标记就写在 sleep 正上方）。
        # ★不可上溯多行：否则同一区段内后续新增的 sleep 会被前一行的标记
        #   误判为已豁免 ⇒ 漏标记回潮（169批 C10 实测踩到）。
        sleeps.append({
            "line": ln,
            "func": owner.get(ln, "<module>"),
            "text": st[:90],
            "exempt": (i > 0 and EXEMPT_TAG in lines[i - 1]),
        })

    keepalive = [s for s in sleeps if s["func"] == KEEPALIVE_DEF]
    business = [s for s in sleeps if s["func"] != KEEPALIVE_DEF]

    unclassified = [s for s in business if not s["exempt"]]
    exempt_ok = [s for s in business if s["exempt"]]

    # 保活必须走网关（灰度分流），否则纪律未落地
    gw_ok = ("wait_heartbeat_pulse" in src)
    # ★在保活 sleep 上下 8 行内找网关调用（分流写在其上方，非同一行）
    keepalive_has_gateway = any(
        any("wait_heartbeat_pulse" in lines[j]
            for j in range(max(0, s["line"] - 1 - 8), min(len(lines), s["line"] + 8)))
        for s in keepalive)

    violations = []
    for s in unclassified:
        violations.append(
            "L%d %s: 未分类的 time.sleep（若为业务延时请加 %s 标记；"
            "若为真纪律问题请改 wait_heartbeat_pulse）"
            % (s["line"], s["func"], EXEMPT_TAG))
    if not gw_ok:
        violations.append("未找到 wait_heartbeat_pulse 网关调用（脉冲纪律未落地）")
    if keepalive and not keepalive_has_gateway:
        violations.append(
            "{} 的保活 sleep 未与 wait_heartbeat_pulse 分流（纪律未落地）".format(KEEPALIVE_DEF))

    return {
        "target": TARGET,
        "total_sleep": len(sleeps),
        "keepalive_sleep": len(keepalive),
        "business_exempt": len(exempt_ok),
        "business_unclassified": len(unclassified),
        "gateway_present": gw_ok,
        "keepalive_gateway_split": keepalive_has_gateway,
        "sleeps": sleeps,
        "violations": violations,
        "pass": not violations,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = audit()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("[pulse-sleep-audit] 目标: {}".format(r["target"]))
        print("  time.sleep 总数        : %d" % r["total_sleep"])
        print("  保活节拍 sleep         : %d（脉冲纪律范畴）" % r["keepalive_sleep"])
        print("  业务延时（已豁免标记） : %d" % r["business_exempt"])
        print("  业务延时（未分类）     : %d" % r["business_unclassified"])
        print("  心跳网关 wait_heartbeat_pulse : %s" % ("在位" if r["gateway_present"] else "缺失"))
        print("  保活与网关分流         : %s" % ("是" if r["keepalive_gateway_split"] else "否"))
        if r["violations"]:
            print("[pulse-sleep-audit] [FAIL] 违规 %d 项：" % len(r["violations"]))
            for v in r["violations"]:
                print("   ! {}".format(v))
        else:
            print("[pulse-sleep-audit] [PASS] 主循环保活走心跳网关；业务延时全部已分类豁免。")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())