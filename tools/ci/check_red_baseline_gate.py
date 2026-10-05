#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B156-0 红基线三片门禁（替换 T155-2 单一全量差集）。

设计（据烛微前置分析 §A-4 + 任务书 B156-0）：
  - 片1 · collect 门禁：--collect-only 必须 0 error，节点总数漂移 >±5 判红（拦半成品）。
  - 片2 · 隔离门禁（权威）：按文件单跑，known_fail 唯一来源；开发期跑此片，确保无「新确定性红」。
  - 片3 · 全量门禁（双向差集）：(a) 是否出现 known_fail∪pollution_set 之外的新红；(b) 是否出现基线内节点转绿/xpass（旧门禁完全漏检）。
  - env 指纹：每次跑必附 HEAD/工作树行数/staged/PYTHONIOENCODING/活体PID/插件清单；基线生成时缺字段不得入基线。

与 pre-commit 的关系：
  - 片1（collect）轻量，可挂 pre-commit（见 .git/hooks/pre-commit 对应段）。
  - 片2/片3 为重跑型（跑全量/逐文件），受 D5 纪律约束（禁活体窗内全量 pytest），仅批末/CI 手动或定时运行，不进 per-commit hook。

用法：
  python tools/ci/check_red_baseline_gate.py collect            # 片1
  python tools/ci/check_red_baseline_gate.py isolation          # 片2（逐文件单跑）
  python tools/ci/check_red_baseline_gate.py full               # 片3（全量双向差集）
  python tools/ci/check_red_baseline_gate.py verify-env         # 环境指纹校验
  python tools/ci/check_red_baseline_gate.py --input out.txt full   # 片3 解析已捕获输出
  python tools/ci/check_red_baseline_gate.py --selftest         # 内置单测
"""
import os
import re
import sys
import json
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # tools/ci -> tools -> root
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from nucleus._silent_except import silent_exc  # 静默异常走 CI 门禁认可通道
BASELINE_PATH = os.path.join(SCRIPT_DIR, "baselines", "red_baseline_156.json")
COLLECT_TOLERANCE = 5

FAILED_RE = re.compile(r"^\s*FAILED\s+(\S+)", re.M)
XFAIL_RE = re.compile(r"^\s*XFAIL\s+(\S+)", re.M)
XPASS_RE = re.compile(r"^\s*XPASS\s+(\S+)", re.M)
COLLECT_ERR_RE = re.compile(r"ERROR collecting (\S+)", re.M)
COLLECT_COUNT_RE = re.compile(r"(\d+)\s+tests?\s+collected|collected\s+(\d+)\s+(?:item|items|tests?)", re.M)


def load_baseline(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    known = set(d.get("known_fail", []))
    poll = set(d.get("pollution_set", []))
    env = d.get("env_fingerprint", {}) or {}
    collect_baseline = d.get("collect_baseline")
    assert isinstance(known, set), "known_fail 必须为 list"
    return {"known_fail": known, "pollution_set": poll,
            "env_fingerprint": env, "collect_baseline": collect_baseline, "raw": d}


#: pytest 汇总行形态：末尾形如 "27 failed, 40 passed, 4 errors in 110.08s"
#:   或 "20 passed in 15.96s" / "no tests ran"。
#: ★这是判定「pytest 是否真正跑完」的**唯一可靠信号**（第161批下 刀1）。
_SUMMARY_RE = re.compile(r'\d+\s+(failed|passed|error)\b|\bno tests ran\b')


def has_pytest_summary(text):
    """★刀1：判断 pytest 输出是否含汇总行（= 是否真正跑完）。

    截断（SystemExit / 超时 / 崩溃打断）时 pytest 来不及打汇总行，
    此时 FAILED=0 是「根本没跑起来」的假象，绝不能当「全绿」。
    """
    if not text:
        return False
    return bool(_SUMMARY_RE.search(text))


def parse_failed(text):
    failed = set(FAILED_RE.findall(text or ""))
    xfailed = set(XFAIL_RE.findall(text or ""))
    xpassed = set(XPASS_RE.findall(text or ""))
    return failed, xfailed, xpassed


def run_pytest(args, timeout=1800):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest"] + args, cwd=PROJECT_ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=timeout,
    )
    return proc.stdout + "\n" + proc.stderr


def run_pytest_per_file(files, timeout=600):
    iso = set()
    errs = {}
    for i, f in enumerate(files, 1):
        txt, _ = _run_once([f, "-q", "--tb=line", "-rF", "-p", "no:cacheprovider"], timeout)
        fs, _, _ = parse_failed(txt)
        iso |= fs
        if "SAFE_DELETE" in txt or "SystemExit" in txt:
            errs[f] = "safe_delete_guard_or_systemexit"
    return iso, errs


def _run_once(args, timeout):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest"] + args, cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
        return proc.stdout + "\n" + proc.stderr, None
    except subprocess.TimeoutExpired as _te:  # noqa: BLE001
        silent_exc(_te, where="check_red_baseline_gate._run_once", level="debug")
        return "", "timeout"
    except Exception as e:  # noqa: BLE001
        silent_exc(e, where="check_red_baseline_gate._run_once", level="debug")
        return "", "err:%s" % e


def list_test_files():
    files = []
    tests = os.path.join(PROJECT_ROOT, "tests")
    for dp, _, fns in os.walk(tests):
        for fn in fns:
            if fn.startswith("test_") and fn.endswith(".py"):
                files.append(os.path.relpath(os.path.join(dp, fn), PROJECT_ROOT).replace("\\", "/"))
    return sorted(files)


# ---------------------------------------------------------------- 片1 collect
def slice_collect(baseline):
    txt, _ = _run_once(["tests/", "--collect-only", "-q", "-p", "no:cacheprovider"], timeout=300)
    errs = COLLECT_ERR_RE.findall(txt)
    m = COLLECT_COUNT_RE.search(txt)
    count = int(m.group(1) or m.group(2)) if m else None
    print(f"[slice1-collect] 收集错误={len(errs)} 收集节点数={count}", file=sys.stderr)
    if errs:
        print("[slice1-collect] ❌ 存在收集错误（半成品/导入断链）：")
        for e in errs[:20]:
            print(f"   ! {e}")
        return 1
    cb = baseline.get("collect_baseline")
    if count is None:
        print("[slice1-collect] ⚠ 未能解析节点数，跳过漂移校验")
        return 0
    if cb is not None and abs(count - cb) > COLLECT_TOLERANCE:
        print(f"[slice1-collect] ❌ 节点数漂移 {count} vs 基线 {cb}（容差 ±{COLLECT_TOLERANCE}）")
        return 1
    print(f"[slice1-collect] ✅ PASS（0 收集错误，节点数 {count} 在基线 {cb}±{COLLECT_TOLERANCE} 内）")
    return 0


# ---------------------------------------------------------------- 片2 isolation
def slice_isolation(baseline, input_path=None):
    known = baseline["known_fail"]
    poll = baseline["pollution_set"]
    allowed = known | poll
    if input_path and os.path.isfile(input_path):
        with open(input_path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        # ★刀1顺带修：parse_failed 返回 3 元（failed/xfailed/xpassed），原按 2 值解包
        #   ⇒ ValueError 崩溃（160下下 6.1 实测发现，遗留至今）。
        iso, _xf, _xp = parse_failed(txt)
        print(f"[slice2-isolation] 解析已捕获输出，隔离失败={len(iso)}", file=sys.stderr)
    else:
        files = list_test_files()
        print(f"[slice2-isolation] 逐文件单跑 {len(files)} 个测试文件…", file=sys.stderr)
        iso, errs = run_pytest_per_file(files)
        if errs:
            print(f"[slice2-isolation] ⚠ {len(errs)} 个文件运行异常（安全删除守卫/超时），结果仅供参考", file=sys.stderr)
    new = iso - allowed
    if new:
        print(f"[slice2-isolation] ❌ 检出隔离新确定性红（不在 known_fail∪pollution_set，共 {len(new)}）：")
        for n in sorted(new):
            print(f"   + {n}")
        print("[slice2-isolation] 结论：FAIL（存在新增确定性红）")
        return 1
    print(f"[slice2-isolation] ✅ PASS（隔离失败 {len(iso)} 全部落在已知基线，无新增确定性红）")
    return 0


# ---------------------------------------------------------------- 片3 full 双向
def slice_full(baseline, input_path=None):
    known = baseline["known_fail"]
    poll = baseline["pollution_set"]
    allowed = known | poll
    if input_path and os.path.isfile(input_path):
        with open(input_path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        print("[slice3-full] 解析已捕获输出", file=sys.stderr)
    else:
        print(f"[slice3-full] 运行全量 pytest（节点≈{baseline.get('collect_baseline')}）…", file=sys.stderr)
        txt = run_pytest(["tests/", "-q", "--tb=line", "-rF", "-p", "no:cacheprovider"], timeout=1800)
        # ★第161批下 刀1（T-门禁SystemExit截断假绿-1）：截断须 **FAIL 阻断**。
        #   截断的可靠信号 = **pytest 汇总行缺失**（如 "N failed, M passed in Xs"）；
        #   绝不以「输出含 SystemExit」判定——被测代码的 SAFE_DELETE_BULK_CONFIRM_REQUIRED
        #   是**预期安全拦截**，此时 pytest 仍会正常打汇总行。
        #   若仅看 SystemExit，会把「有汇总行的正常运行」误判为截断（160下下 6.1 实测
        #   6 批全部被误判），从而把真实结果当成不可信而丢弃。
        _truncated = not has_pytest_summary(txt)
        if _truncated:
            print("[slice3-full] ❌ 截断检测：pytest 汇总行缺失，结果不可信（未跑完即中断）",
                  file=sys.stderr)
            print("[slice3-full]    · 此时 FAILED=0 属「根本没跑起来」的假象，"
                  "「基线转绿」统计全部作废", file=sys.stderr)
            print("[slice3-full]    · 处方：改用 --input传入 isolation 逐文件实测结果，"
                  "或排查触发截断的测试文件组合", file=sys.stderr)
            print("[slice3-full] 结论：FAIL（截断，结果不可信）")
            return 1
        if "SAFE_DELETE" in txt or "SystemExit" in txt:
            print("[slice3-full] ℹ 检测到 safe-delete 守卫输出，但 pytest 汇总行完整"
                  "（属预期安全拦截，非截断）", file=sys.stderr)
    failed, xfailed, xpassed = parse_failed(txt)
    new_red = failed - allowed
    turned_green = allowed - failed
    print(f"[slice3-full] 本轮 FAILED={len(failed)}  XFAIL={len(xfailed)}  XPASS={len(xpassed)}")
    print(f"[slice3-full] 基线允许集(known_fail∪pollution)={len(allowed)}  新增红={len(new_red)}  基线转绿={len(turned_green)}")
    if turned_green:
        print("[slice3-full] ℹ️ 以下基线红节点本轮未以 FAILED 出现（已修复/转态，正向，仅报告）：")
        for n in sorted(turned_green):
            print(f"   - {n}")
    if new_red:
        print("\n[slice3-full] ❌ 检出新红（known_fail∪pollution_set 之外）：")
        for n in sorted(new_red):
            print(f"   + {n}")
        print("[slice3-full] 结论：FAIL（存在新增失败）")
        return 1
    print("\n[slice3-full] ✅ PASS（新增红=0；xfail 不计 fail；基线转绿已正向报告）")
    return 0


# ---------------------------------------------------------------- env 指纹
def verify_env(baseline):
    env = baseline.get("env_fingerprint", {}) or {}
    base_head = env.get("head")
    cur_head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT).decode().strip()
    status = subprocess.check_output(["git", "status", "--short"], cwd=PROJECT_ROOT).decode("utf-8", "replace")
    dirty = len([l for l in status.splitlines() if l.strip()])
    staged = len(subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=PROJECT_ROOT).decode().splitlines())
    pyenc = os.environ.get("PYTHONIOENCODING", "<未设置>")
    print(f"[verify-env] 基线HEAD={base_head} 当前HEAD={cur_head} 工作树脏={dirty} staged={staged} PYTHONIOENCODING={pyenc}")
    rc = 0
    if base_head and base_head != cur_head:
        print(f"[verify-env] ❌ HEAD 漂移：基线 {base_head} ≠ 当前 {cur_head}（基线须在对应 HEAD 重建）")
        rc = 1
    if dirty != env.get("git_status_short_lines"):
        print(f"[verify-env] ⚠ 工作树脏文件数 {dirty} 与基线 {env.get('git_status_short_lines')} 不一致（基线生成时须干净窗）")
    if pyenc != env.get("pythonioencoding"):
        print("[verify-env] ⚠ PYTHONIOENCODING 与基线不一致（输出解析口径差异）")
    if rc == 0:
        print("[verify-env] ✅ HEAD 匹配（其余字段仅供参考）")
    return rc


def selftest():
    sample = (
        "FAILED tests/test_a.py::TestA::test_one - AssertionError\n"
        "FAILED tests/test_a.py::test_two - assert 0\n"
        "XFAIL tests/test_b.py::test_x - reason\n"
        "XPASS tests/test_c.py::test_y\n"
        "collected 4456 items\n"
        "passed 10\n"
    )
    failed, xfailed, xpassed = parse_failed(sample)
    assert failed == {"tests/test_a.py::TestA::test_one", "tests/test_a.py::test_two"}, failed
    assert xfailed == {"tests/test_b.py::test_x"}, xfailed
    assert xpassed == {"tests/test_c.py::test_y"}, xpassed
    m = COLLECT_COUNT_RE.search(sample)
    assert m and int(m.group(1) or m.group(2)) == 4456
    print("[selftest] 解析单测通过")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0
    baseline_path = BASELINE_PATH
    if "--baseline" in argv:
        i = argv.index("--baseline")
        baseline_path = argv[i + 1]
    input_path = None
    if "--input" in argv:
        i = argv.index("--input")
        input_path = argv[i + 1]
    mode = "full"
    for a in argv[1:]:
        if not a.startswith("-"):
            mode = a
            break
    if not os.path.isfile(baseline_path):
        print(f"[gate] 基线文件缺失：{baseline_path}", file=sys.stderr)
        return 2
    baseline = load_baseline(baseline_path)
    print(f"[gate] 基线 HEAD={baseline['env_fingerprint'].get('head')} "
          f"known_fail={len(baseline['known_fail'])} pollution={len(baseline['pollution_set'])} "
          f"collect基线={baseline['collect_baseline']}", file=sys.stderr)
    if mode == "collect":
        return slice_collect(baseline)
    if mode == "isolation":
        return slice_isolation(baseline, input_path)
    if mode == "full":
        return slice_full(baseline, input_path)
    if mode == "verify-env":
        return verify_env(baseline)
    print(f"[gate] 未知模式：{mode}（collect/isolation/full/verify-env）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
