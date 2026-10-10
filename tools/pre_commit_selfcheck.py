# -*- coding: utf-8 -*-
"""提交前三检自检（★本地工具，入库为 tools/ 辅助脚本、不参与 CI 自动执行、不改门禁链）。

背景（2026-10-05复盘）：今日 70 笔 commit 中 21 笔是 [ci-gate-change] 重锚笔，
返工 21 次**全部可预判**：
  · collect 漂移被拦 11 次（每刀新增 7~15 节点 > 容差 ±5，必然触发）
  · cw2 静默 except 被拦 6 次
  · cw3-A 被拦 2 次（cw3-A「仅 IW 变更触发」早已写在记忆库，仍被拦）
  · 门禁隔离守卫被拦 2 次（CI 件与业务同批暂存）
根因：纪律只写在记忆库里，**不会自动执行** ⇒ 本脚本把纪律变成可执行的一步。

用法（在提交前跑）：
    "D:/Program Files/Python312/python.exe" tools/pre_commit_selfcheck.py

退出码：0=全部通过可提交；1=有必须先修的项。
"""
import io
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

BASELINE = 'tools/ci/baselines/red_baseline_156.json'
COLLECT_TOLERANCE = 5

# ★子进程干净环境：清掉 IDE/CLI 的 sitecustomize shim，否则会产生稳定假红
ENV = os.environ.copy()
for _k in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
    ENV.pop(_k, None)
ENV['PYTHONNOUSERSITE'] = '1'


def _run(args, timeout=1800):
    return subprocess.run([PY] + args, capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          env=ENV, timeout=timeout, cwd=ROOT)

def _run_git(args, timeout=60):
    """运行 git 命令（不预置 PY，避免把 git 当 python 脚本执行）。"""
    return subprocess.run(['git'] + args, capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          env=ENV, timeout=timeout, cwd=ROOT)

def _run_timed(args, timeout=1800, label=''):
    """带超时与耗时自报的 _run 包装（耗时输出到自检日志）。"""
    t0 = time.time()
    r = _run(args, timeout=timeout)
    elapsed = time.time() - t0
    print('    [耗时自报][%s] 超时=%ds 实测=%.1fs' % (label, timeout, elapsed))
    return r


def _staged():
    r = _run_git(['diff', '--cached', '--name-only'], timeout=60)
    return [x for x in (r.stdout or '').split('\n') if x.strip()]


def _unstaged():
    r = _run_git(['status', '--porcelain'], timeout=60)
    out = []
    for line in (r.stdout or '').split('\n'):
        if not line.strip():
            continue
        st = line[:2]
        if st.strip() == 'M' or st.strip() == '??' or st.strip() == 'A':
            out.append(line[3:].strip())
    return out


def main():
    print('=' * 70)
    print('提交前三检自检（collect 增量 / cw3-A / cw2 / 隔离守卫）')
    print('=' * 70)

    base = json.load(io.open(BASELINE, encoding='utf-8'))
    cur_collect = base.get('collect_baseline', 0)
    kf = len(base.get('known_fail', []))
    staged = _staged()
    touched = sorted(set(staged) | set(_unstaged()))
    print('当前基线：collect=%s known_fail=%d pollution_set=%d'
          % (cur_collect, kf, len(base.get('pollution_set', []))))
    print('本次涉及文件 %d 个' % len(touched))
    print()

    must_fix = []

    # ---- ① collect 增量 ----
    print('[1/4] collect 节点数（门禁容差 ±%d）' % COLLECT_TOLERANCE)
    has_py = any(f.endswith('.py') for f in touched)
    if not has_py:
        # ★docs-only / 纯配置 短路：无 .py 变更则不跑全库 collect 收集，
        #   显著缩短门禁耗时（collect 收集是全库 pytest 收集，最重一段）。
        print('    - 纯文档/纯配置提交（无 .py 变更），跳过 collect 全库收集判定（docs-only 短路）')
        print('    [耗时自报][collect] 短路：跳过（0 全库收集）')
        print('    ✓ 短路通过（无需全库收集，门禁耗时显著下降）')
    else:
        r = _run_timed(['tools/ci/check_red_baseline_gate.py', 'collect'],
                       timeout=1800, label='collect')
        # ★门禁的「收集节点数」写在 **stderr**（stdout 只有结论行）—— 两都要读
        out = (r.stdout or '') + '\n' + (r.stderr or '')
        m = re.search(r'收集节点数=(\d+)', out)
        if not m:
            if r.returncode == 0:
                # 增量门跳过全库收集（无受影响测试文件）→ 节点数不可能漂移，视为通过
                print('    ✓ 增量门跳过全库收集（无受影响测试文件），节点数无漂移可能，视为通过')
            else:
                print('    ⚠ 未取到节点数且门禁非零退出（门禁输出异常），请手工跑一次确认')
                print('      门禁 stdout: %s' % (r.stdout or '').strip()[:120])
                print('      门禁 stderr: %s' % (r.stderr or '').strip()[:120])
                must_fix.append('collect 节点数未取到，需手工确认')
        else:
            actual = int(m.group(1))
            delta = actual - cur_collect
            print('    实测 collect=%d  基线=%d  增量=%+d' % (actual, cur_collect, delta))
            if abs(delta) > COLLECT_TOLERANCE:
                print('    ✗ 超出容差 ⇒ 必须先重锚 %s，否则提交必被拦'
                      % BASELINE)
                print('      处方：把 collect_baseline 改为 %d（同批或独立'
                      ' [ci-gate-change] commit）' % actual)
                must_fix.append('collect 需重锚 %d → %d' % (cur_collect, actual))
            else:
                print('    ✓ 在容差内，无需重锚')

    # ---- ② cw3-A（仅 IW 变更触发）----
    print()
    print('[2/4] cw3-A 语义守恒（仅改 PulseInnerWorld.py 时触发）')
    iw_hit = [f for f in touched if f.endswith('PulseInnerWorld.py')]
    if iw_hit:
        r = _run_timed(['tools/ci/cw3_consistency_gate.py'], timeout=600, label='cw3-A')
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode == 0:
            print('    ✓ PASS')
        else:
            print('    ✗ FAIL ⇒ 需按门禁处方处理：')
            for line in out.split('\n'):
                if '指标' in line or 'delta_reason' in line or 'FAIL' in line:
                    print('      %s' % line.strip())
            print('      处方：① 误伤 ⇒ 在 iw_consistency_baseline.json填'
                  ' delta_reason + expected_delta；')
            print('            ② 真漂移 ⇒ 回退该改动')
            must_fix.append('cw3-A 未过（已改 IW）')
    else:
        print('    – 本次未改 PulseInnerWorld.py，跳过')

    # ---- ③ cw2 静默 except ----
    print()
    print('[3/4] cw2 静默 except 指纹（须非沙箱跑）')
    r = _run_timed(['tools/ci/cw2_t2e_ci_gate_silent_except.py'], timeout=1800, label='cw2')
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode == 0:
        print('    ✓ PASS')
    else:
        bad = [ln.strip() for ln in out.split('\n')
               if ('静默' in ln or 'FAIL' in ln) and ln.strip()][:8]
        print('    ✗ FAIL ⇒ 新写的 except 需补日志：')
        for ln in bad:
            print('      %s' % ln)
        print('      合规形态：except Exception as e: + silent_exc(e, where="模块.函数")')
        print('                或 with contextlib.suppress(SomeError):')
        must_fix.append('cw2 有新增静默 except')

    # ---- ④ 门禁隔离守卫 ----
    print()
    print('[4/4] 门禁隔离守卫（tools/ci/ 不得与业务代码同批）')
    ci_files = [f for f in staged if f.startswith('tools/ci/')]
    biz_files = [f for f in staged
                 if not f.startswith('tools/ci/') and not f.startswith('docs/')]
    if ci_files and biz_files:
        print('    ✗ 暂存区同时含 CI 门禁件与业务代码：')
        for f in ci_files:
            print('       CI  : %s' % f)
        for f in biz_files[:6]:
            print('       业务: %s' % f)
        print('      处方：git reset HEAD -- <CI 件路径>，先单独提 CI 笔')
        must_fix.append('CI 件与业务同批暂存')
    else:
        print('    ✓ 无混批（或无 CI 件）')

    # ---- 结论 ----
    print()
    print('=' * 70)
    if must_fix:
        print('结论：✗ 有 %d 项必须先处理，提交必被拦：' % len(must_fix))
        for i, x in enumerate(must_fix, 1):
            print('   %d) %s' % (i, x))
        print()
        print('★处理完再提交，可省一次 15-18 min 的门禁等待 + 一次重提。')
        return 1
    print('结论：✓ 三检全过，可提交。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
