#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""legacy 装配段防回潮门禁（第158批 _create_organ 退役★ 配套门禁）。

背景（据第158批 _create_organ 退役实测结论）：
  main.py 的 _init_organs_legacy()（区间以本脚本自报为准）内含 37 处硬编码 self._create_organ(...)
  调用。在 FEATURE['use_declarative_assembly']=True（config.py:1839）下，该段
  已不执行（入口 _init_organs_with_feature() 直接分流至 _init_organs_declarative），
  属「已停用但保留的一键回退通道」。

  本门禁的作用不是断言它该被删，而是**防止已停用路径被重新扩张**：
  若有人在 legacy 段内新增 _create_organ 调用，说明声明式装配未真正承接，
  却在硬编码路径上加固——这会让「声明式为唯一装配面」的收敛方向倒退。

校验项：
  1. legacy 段内 self._create_organ( 调用行数 <= 基线（默认 37）；
  2. legacy 段必须仍带「已停用」标注（docstring 退役说明未被删）；
  3. use_declarative_assembly 仍为 True（若被置 False，则 legacy 复活执行，
     本门禁的前提不再成立，应改走「真退役」流程并更新基线）。

红线：任一项 FAIL 即 rc=1。

用法：
  python tools/ci/check_legacy_assembly_gate.py            # 默认校验（pre-commit 调用）
  python tools/ci/check_legacy_assembly_gate.py --selftest # 自检（构造违规样本，验证能拦）
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 门禁件位于 tools/ci/，属 cw2「未跟踪盲区」范围：
# 任何 except 块体若无 silent_exc / 日志调用即 FAIL。统一走 _silent_except。
from nucleus._silent_except import silent_exc  # noqa: E402

MAIN_PY = os.path.join(PROJECT_ROOT, "main.py")
CONFIG_PY = os.path.join(PROJECT_ROOT, "config.py")

# ★基线：实测 legacy 段内 self._create_organ( 调用行数 = 37（代码面口径）
#   （全仓 .py 字面 75 = main 47 + gate 16 + organ_assembler 6 + config 2
#    + PulseCodeLearner 2 + organ_loader 1 + metrics_spec 1；计数判据
#    `git grep -c '_create_organ' -- main.py` = 47）。
BASELINE_LEGACY_CALLS = 37

# legacy 段停用标注的识别锚点（docstring 中的退役说明）
# ★锚点措辞须与 main.py 内实际写入的 docstring 一致，否则门禁恒 FAIL。
RETIRE_MARKER = "★第158批 _create_organ 退役★"

# declarative 段内允许出现的 _create_organ 调用数（QICA 准器官，Phase 0 单独实例化）
DECLARATIVE_ALLOWED = 1


def _read_lines(path):
    """读文件并归一为 LF 行列表（★判行尾/计数必须归一，否则 CRLF 影响匹配）。"""
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8").replace("\r\n", "\n").split("\n")


def locate_legacy_span(lines):
    """定位 legacy 段行号区间 [start, end)（1-based 行号，闭区间端点返回 0-based index）。

    定位策略：从 def _init_organs_legacy 之后的下一个顶层 def（即同缩进的
    「    def xxx」或类结束）为止。legacy 是文件内最后一个装配方法，
    故 end 取「下一个 4 空格缩进 def」或文件末尾。
    """
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("def _init_organs_legacy"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln.startswith("    def ") and not ln.startswith("     "):
            end = j
            break
    return start, end


def collect_legacy_calls(lines):
    """返回 legacy 段内 self._create_organ( 的调用行号列表。"""
    span = locate_legacy_span(lines)
    if span is None:
        return None, None
    start, end = span
    hits = [i + 1 for i in range(start, end) if "self._create_organ(" in lines[i]]
    return hits, span


def check_declarative_qica(lines):
    """校验 declarative 段内 _create_organ 调用数 == 1（QICA 准器官 Phase 0）。"""
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("def _init_organs_declarative"):
            start = i
            break
    if start is None:
        return None, 0
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("    def "):
            end = j
            break
    hits = [i + 1 for i in range(start, end) if "self._create_organ(" in lines[i]]
    return [h for h in hits], len(hits)


def check_switch_on():
    """读 config.FEATURE['use_declarative_assembly']；返回 (值 或 None, 错误信息)。"""
    try:
        import config  # noqa: PLC0415
        val = (getattr(config, "FEATURE", {}) or {}).get("use_declarative_assembly", None)
        return val, None
    except Exception as e:  # noqa: BLE001
        silent_exc(e, where="check_legacy_assembly_gate::check_switch_on", level="warning")
        return None, f"读取 config.FEATURE 失败: {type(e).__name__}"


def run_gate():
    """执行门禁校验，返回 (是否通过, 报告行列表)。"""
    report = []
    errors = []

    if not os.path.isfile(MAIN_PY):
        return False, [f"[FAIL] main.py 不存在: {MAIN_PY}"]

    lines = _read_lines(MAIN_PY)
    hits, span = collect_legacy_calls(lines)
    if hits is None:
        return False, ["[FAIL] 未定位到 _init_organs_legacy（门禁前提失效）"]

    start, end = span
    report.append(f"[INFO] legacy 段行号区间 = [{start + 1}, {end}]")
    report.append(f"[INFO] legacy 段 _create_organ 调用数 = {len(hits)}（基线 {BASELINE_LEGACY_CALLS}）")

    # 1) 调用数不得上升
    if len(hits) > BASELINE_LEGACY_CALLS:
        errors.append(
            f"legacy 段 _create_organ 调用数 {len(hits)} > 基线 {BASELINE_LEGACY_CALLS}"
            f"（新增行号: {hits[BASELINE_LEGACY_CALLS:]}）——已停用路径不得扩张"
        )
    elif len(hits) < BASELINE_LEGACY_CALLS:
        report.append(
            f"[INFO] legacy 段调用数 {len(hits)} < 基线 {BASELINE_LEGACY_CALLS}"
            f"（已缩减，真退役推进中；如已删除整段请更新基线）"
        )

    # 2) 停用标注必须仍在
    seg = "\n".join(lines[start:end])
    if RETIRE_MARKER not in seg:
        errors.append(f"legacy 段缺少停用标注（锚点 {RETIRE_MARKER!r}）——退役说明被删？")
    else:
        report.append("[OK] legacy 段停用标注在位")

    # 3) declarative 段 QICA 例外
    qica_hits, qica_n = check_declarative_qica(lines)
    report.append(f"[INFO] declarative 段 _create_organ 调用数 = {qica_n}（QICA 准器官，预期 {DECLARATIVE_ALLOWED}）")
    if qica_n is not None and qica_n > DECLARATIVE_ALLOWED:
        errors.append(
            f"declarative 段 _create_organ 调用数 {qica_n} > {DECLARATIVE_ALLOWED}"
            f"（行号 {qica_hits}）——准器官例外只应保留 QICA 一处"
        )

    # 4) 装配开关必须为 True（否则 legacy 复活，门禁前提失效）
    val, err = check_switch_on()
    if err:
        errors.append(err)
    elif val is not True:
        errors.append(
            f"use_declarative_assembly={val!r}（非 True）——legacy 已复活执行，"
            f"本门禁前提失效，应改走真退役流程并重锚基线"
        )
    else:
        report.append("[OK] use_declarative_assembly=True（legacy 确为停用路径）")

    for r in report:
        print(r)
    if errors:
        for e in errors:
            print(f"[FAIL] {e}")
        return False, report
    print("[PASS] legacy 装配段防回潮门禁通过")
    return True, report


def selftest():
    """自检：构造违规样本，验证门禁能拦住回潮。"""
    global BASELINE_LEGACY_CALLS
    ok = True

    # 样本1：legacy 段新增一处调用 -> 应 FAIL
    orig = BASELINE_LEGACY_CALLS
    try:
        BASELINE_LEGACY_CALLS = 36  # 模拟基线 36、实际 37
        passed, _ = run_gate()
        if passed:
            print("[SELFTEST-FAIL] 违规样本1 未被拦截")
            ok = False
        else:
            print("[SELFTEST-OK] 违规样本1（调用数超基线）被拦截")
    finally:
        BASELINE_LEGACY_CALLS = orig

    # 样本2：真实仓库当前状态 -> 应 PASS
    passed, _ = run_gate()
    if not passed:
        print("[SELFTEST-FAIL] 真实仓库状态未通过")
        ok = False
    else:
        print("[SELFTEST-OK] 真实仓库状态通过")

    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    passed, _ = run_gate()
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
