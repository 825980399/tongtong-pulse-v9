# -*- coding: utf-8 -*-
"""第161批下 刀1 门控单测（T-门禁SystemExit截断假绿-1）。

核心断言（任务书验收）：
  ① 截断场景（无 pytest 汇总行）⇒ slice_full 返回 1（阻断），不得 PASS
  ② 正常运行（含汇总行，即便含 safe-delete/SystemExit 文本）⇒ 不得误判为截断
  ③ has_pytest_summary 判据 6 种形态全对
  ④ 片2 isolation 的 --input 分支不再因3 值解包崩溃
"""
# -*- coding: utf-8 -*-
# export-scan-skip-file  （本文件 PII 均为构造的假夹具，非真实身份）

import importlib.util
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPO = os.path.dirname(ROOT) if ROOT.endswith('tests') else ROOT
GATE_REL = os.path.join('tools', 'ci', 'check_red_baseline_gate.py')

ENV = os.environ.copy()
for _k in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
    ENV.pop(_k, None)
ENV['PYTHONNOUSERSITE'] = '1'

# 截断文本：★故意不含 "N failed, M passed" 汇总行
TRUNC = '\n'.join([
    'F.F....F....F...F                    [ 63%]',
    '...F................FFFF.E           [100%]',
    '=================================== ERRORS ====================================',
    'E   SystemExit: 1',
    '[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":60,"threshold":50,"scope":"turn"}',
    '================================== FAILURES ===================================',
    'E   SystemExit: 1',
    'cleanup warn: SystemExit: 1',
])

# 正常文本：含完整汇总行，即便含 safe-delete/SystemExit 字样
NORMAL = '\n'.join([
    'F.F....F....F...F                                            [100%]',
    '[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50,"scope":"turn"}',
    'E   SystemExit: 1',
    '27 failed, 40 passed, 4 errors in 110.08s (0:01:50)',
])


def _load_gate():
    path = os.path.join(REPO, GATE_REL)
    spec = importlib.util.spec_from_file_location('gate_k1', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run_slice_full(sample_text):
    """注入 run_pytest 后调 slice_full，返回 (rc, 合并输出)。"""
    # ★先写样本文件，再用 driver 里的 repr 传路径（避免 %r 占位符与 % 格式化打架）
    sample_path = os.path.join(REPO, 'tmp', '_k1_sample.txt')
    with open(sample_path, 'w', encoding='utf-8') as f:
        f.write(sample_text)

    drv_path = os.path.join(REPO, 'tmp', '_k1_driver.py')
    with open(drv_path, 'w', encoding='utf-8') as f:
        f.write(
            'import sys, json, importlib.util\n'
            'sys.path.insert(0, {repo!r})\n'
            'spec = importlib.util.spec_from_file_location("gate", {gate!r})\n'
            'g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)\n'
            'sample = open({sample!r}, encoding="utf-8").read()\n'
            'g.run_pytest = lambda args, timeout=None: sample\n'
            'base = json.load(open(g.BASELINE_PATH, encoding="utf-8"))\n'
            'base["known_fail"] = set(base["known_fail"])\n'
            'base["pollution_set"] = set(base["pollution_set"])\n'
            'sys.exit(g.slice_full(base))\n'.format(
                repo=REPO, gate=os.path.join(REPO, GATE_REL), sample=sample_path))

    r = subprocess.run([sys.executable, drv_path], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=ENV, cwd=REPO,
                       timeout=300)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


class TestKnife1SummaryJudge:
    """判据函数 has_pytest_summary —— 截断检测的唯一可靠信号。"""

    def test_01_real_summary_detected(self):
        g = _load_gate()
        assert g.has_pytest_summary('27 failed, 40 passed, 4 errors in 110.08s')
        assert g.has_pytest_summary('20 passed in 15.96s')

    def test_02_truncated_output_not_detected(self):
        g = _load_gate()
        assert not g.has_pytest_summary(TRUNC)
        assert not g.has_pytest_summary('')

    def test_03_safedelete_with_summary_is_not_truncation(self):
        """★关键区分：safe-delete 守卫 + 完整汇总行 = 正常运行，非截断。"""
        g = _load_gate()
        assert g.has_pytest_summary(NORMAL)

    def test_04_no_tests_ran_counts_as_summary(self):
        g = _load_gate()
        assert g.has_pytest_summary('no tests ran in 0.5s')


class TestKnife1TruncationBlocks:
    """★任务书核心验收：截断场景 GATE_EXIT=1（阻断），不再假绿PASS。"""

    def test_05_truncation_returns_fail(self):
        rc, out = _run_slice_full(TRUNC)
        assert rc == 1, '截断场景必须 return 1，实际 {}\n{}'.format(rc, out[:400])
        assert '截断检测' in out, '应打印截断检测提示'
        assert 'FAIL' in out

    def test_06_normal_run_not_flagged_as_truncation(self):
        rc, out = _run_slice_full(NORMAL)
        assert '截断检测' not in out, '★正常运行被误判为截断：{}'.format(out[:300])
        assert '汇总行完整' in out or 'PASS' in out

    def test_07_truncation_message_explains_false_green(self):
        """★截断提示必须点明「FAILED=0 是没跑起来的假象」，否则后人会再次误采信。"""
        rc, out = _run_slice_full(TRUNC)
        assert rc == 1
        assert '根本没跑起来' in out or '假象' in out, '须说明 FAILED=0 的假象性质'


class TestKnife1IsolationUnpackFix:
    """顺带修：片2 isolation 的 --input 分支 3 值解包（原会 ValueError 崩溃）。"""

    def test_08_isolation_input_branch_no_crash(self):
        """★原缺陷：parse_failed 返回 3 元，:145 按 2 值解包 ⇒ ValueError。

        ★走门禁真实入口（load_baseline → slice_isolation），不用手搓
          baseline dict（手搓会漏掉 list→set 归一，那是main() 的职责）。
        """
        import tempfile
        g = _load_gate()
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False,
                                         encoding='utf-8') as f:
            f.write('FAILED tests/test_x.py::test_a - AssertionError\n'
                    '1 failed, 2 passed in 1.0s\n')
            sample = f.name
        try:
            base = g.load_baseline(g.BASELINE_PATH)
            rc = g.slice_isolation(base, input_path=sample)
            assert rc in (0, 1), '★不得因 ValueError 崩溃，实际 rc={}'.format(rc)
        finally:
            os.unlink(sample)

    def test_09_parse_failed_returns_three_values(self):
        g = _load_gate()
        res = g.parse_failed('FAILED tests/test_x.py::test_a\n1 failed in 1s\n')
        assert len(res) == 3, 'parse_failed 应返回 3 元组'


class TestKnife1Regression:
    def test_10_selftest_still_passes(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, GATE_REL), '--selftest'],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=ENV, cwd=REPO, timeout=300)
        assert r.returncode == 0, '内置 selftest 须通过：{}'.format((r.stdout or '')[:300])