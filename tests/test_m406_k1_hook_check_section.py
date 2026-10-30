# -*- coding: utf-8 -*-
"""406 刀1 判据：pre-commit 第 6 段「hook 模板 ≡ 实装」自检。

守的是什么：
  405 刀5 给 install_precommit_hook.py 加了 --check，但**没人会记得手跑**它。
  404 批的事故形态就是「改了本机忘了改模板」潜伏五天 —— 只有把 --check 挂进
  提交链路，这个缺口才不会再静默复发。

三层断言（缺一层就会变成"写了但没生效"）：
  A 结构层：第 6 段真的在模板里、只出现一次、位置在第 5 段之后、模板仍是纯 LF；
  B 行为层：把第 6 段**原样抽出**用 sh 跑桩脚本 —— 一致零输出 / 不一致 WARNING 不阻断 /
            block 才阻断 / 仅文档提交整段不跑；
  C 集成层：--check 在当前仓库真的 PASS（hooks 目录不存在时跳过，避免干净克隆假红）。

★为什么 B 层要"抽出来跑"而不是只做文本断言：
  第 4 段隔离守卫的教训是「写了 `if` 但条件恒假」这种代码文本上完全看不出来。
  本段的核心语义是"退出码 → 是否阻断"，只有真跑一次 shell 才能钉住。
"""
import io
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(_ROOT, "tools", "ci", "hooks", "pre-commit")
_INSTALLER = os.path.join(_ROOT, "tools", "ci", "install_precommit_hook.py")
_SANDBOX = os.path.join(_ROOT, "tmp", "_m406_k1_unit")

SEC6_HEAD = "# --- 6) 406 刀1 hook 模板 ≡ 实装"
SEC5_HEAD = "# --- 5) 404 刀3"


def _src():
    with io.open(_TPL, encoding="utf-8") as f:
        return f.read()


def _section6(src=None):
    """从模板里原样抽出第 6 段（含首尾），供行为测试真跑。"""
    src = _src() if src is None else src
    i = src.index(SEC6_HEAD)
    j = src.index('if [ "$SKIP_CODE_GATES" -ne 1 ]; then', i)
    depth = 0
    out = []
    for ln in src[j:].split("\n"):
        out.append(ln)
        s = ln.strip()
        if s.startswith("if ") and s.endswith("then"):
            depth += 1
        elif s == "fi":
            depth -= 1
            if depth == 0:
                return "\n".join(out)
    raise AssertionError("第6段 fi 未配对 —— 抽段失败")


_STUB = 'import os, sys\nsys.exit(int(os.environ.get("STUB_RC", "0")))\n'

_HARNESS = """fail() {
  echo "[pre-commit] $1" >&2
  overall=1
}
overall=0
PY="%(py)s"
ROOT="%(root)s"
SKIP_CODE_GATES=%(skip)s
%(sec)s
echo "__OVERALL=$overall"
"""


def _run_section6(rc, gate="warn", skip=0):
    os.makedirs(os.path.join(_SANDBOX, "tools", "ci"), exist_ok=True)
    with io.open(os.path.join(_SANDBOX, "tools", "ci", "install_precommit_hook.py"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write(_STUB)
    env = dict(os.environ)
    env["STUB_RC"] = str(rc)
    env["HOOK_SYNC_GATE"] = gate
    script = _HARNESS % {
        "py": sys.executable.replace("\\", "/"),
        "root": _SANDBOX.replace("\\", "/"),
        "skip": skip,
        "sec": _section6(),
    }
    return subprocess.run(["sh", "-s"], input=script, capture_output=True,
                          text=True, env=env)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_TPL):
            raise unittest.SkipTest("hook 模板不存在：%s" % _TPL)
        cls.src = _src()


class TestA_Structure(_Base):
    def test_01_section6_present(self):
        self.assertIn(SEC6_HEAD, self.src, "第6段标题缺失")

    def test_02_section6_exactly_once(self):
        self.assertEqual(self.src.count(SEC6_HEAD), 1, "第6段重复（补丁重跑过？）")

    def test_03_after_section5(self):
        self.assertGreater(self.src.index(SEC6_HEAD), self.src.index(SEC5_HEAD),
                           "第6段必须在第5段（七层自检）之后")

    def test_04_before_exit_tail(self):
        """第6段必须在 `if [ "$overall" -ne 0 ]` 收尾块之前 —— 否则 fail() 改不动 overall。"""
        self.assertLess(self.src.index(SEC6_HEAD),
                        self.src.index('if [ "$overall" -ne 0 ]; then'))

    def test_05_header_mentions_section6(self):
        head = self.src.split("\n")[:20]
        self.assertTrue(any("406" in h and "--check" in h for h in head),
                        "头部门禁清单未登记第6段")

    def test_06_template_is_pure_lf(self):
        raw = open(_TPL, "rb").read()
        self.assertEqual(raw.count(b"\r\n"), 0, "hook 模板必须纯 LF（CRLF 会让 sh 报错）")

    def test_07_calls_installer_check(self):
        """必须真的调用 `install_precommit_hook.py --check`，不能只写注释。"""
        body = _section6()
        self.assertIn("install_precommit_hook.py", body)
        self.assertIn("--check", body)

    def test_08_default_is_warn_not_block(self):
        """默认值必须是 warn —— 写成 block 会让干净克隆一律提交不了。"""
        body = _section6()
        self.assertIn('${HOOK_SYNC_GATE:-warn}', body)

    def test_09_gate_kill_switch_respected(self):
        body = _section6()
        self.assertIn('if [ "$SKIP_CODE_GATES" -ne 1 ]; then', body,
                      "第6段必须受 GATE_PATH_SPLIT 的文档捷径保护")


class TestB_Behaviour(_Base):
    """把第6段原样抽出来真跑 —— 退出码 → 是否阻断，只有真跑才钉得住。"""

    def test_10_same_is_silent(self):
        p = _run_section6(0)
        self.assertEqual(p.stdout.strip(), "__OVERALL=0")
        self.assertEqual(p.stderr.strip(), "", "一致时必须零输出")

    def test_11_mismatch_warns_but_passes(self):
        p = _run_section6(1)
        self.assertTrue(p.stdout.strip().endswith("__OVERALL=0"), "默认 warn 不得阻断")
        self.assertIn("hook 模板和实装不同步", p.stderr)

    def test_12_mismatch_message_has_fix_hint(self):
        p = _run_section6(1)
        self.assertIn("install_precommit_hook.py", p.stderr, "WARNING 必须给出修复指引")

    def test_13_block_gate_blocks(self):
        p = _run_section6(1, gate="block")
        self.assertTrue(p.stdout.strip().endswith("__OVERALL=1"),
                        "HOOK_SYNC_GATE=block 时必须置 overall=1")
        self.assertIn("HOOK_SYNC_GATE=block", p.stderr)

    def test_14_block_gate_still_passes_when_same(self):
        """开关开 + 一致 → 仍然过（block 只影响不一致分支）。"""
        p = _run_section6(0, gate="block")
        self.assertTrue(p.stdout.strip().endswith("__OVERALL=0"))
        self.assertEqual(p.stderr.strip(), "")

    def test_15_docs_only_skips_entirely(self):
        p = _run_section6(1, skip=1)
        self.assertEqual(p.stdout.strip(), "__OVERALL=0")
        self.assertEqual(p.stderr.strip(), "", "仅文档提交时第6段整段不跑")

    def test_16_mismatch_detail_forwarded(self):
        """--check 的明细必须转发到 stderr（否则只见"不同步"不知差在哪）。"""
        os.makedirs(os.path.join(_SANDBOX, "tools", "ci"), exist_ok=True)
        noise = os.path.join(_SANDBOX, "tools", "ci", "install_precommit_hook.py")
        with io.open(noise, "w", encoding="utf-8", newline="\n") as f:
            f.write('import sys\nprint("[check] pre-commit STALE 模板 1 行 vs 实装 2 行")\n'
                    'sys.exit(1)\n')
        try:
            env = dict(os.environ)
            env["STUB_RC"] = "1"
            env["HOOK_SYNC_GATE"] = "warn"
            script = _HARNESS % {
                "py": sys.executable.replace("\\", "/"),
                "root": _SANDBOX.replace("\\", "/"),
                "skip": 0,
                "sec": _section6(),
            }
            p = subprocess.run(["sh", "-s"], input=script, capture_output=True,
                               text=True, env=env)
            self.assertIn("STALE", p.stderr, "--check 明细未转发")
            self.assertTrue(p.stdout.strip().endswith("__OVERALL=0"))
        finally:
            with io.open(noise, "w", encoding="utf-8", newline="\n") as f:
                f.write(_STUB)


class TestC_Integration(_Base):
    def _hooks_dir(self):
        """定位 hooks 目录；worktree 下 `.git` 是文件，回退 `git rev-parse --git-path hooks`。

        ★刻意不用 try/except：cw2 门禁把「except 里只 return」判为静默 handler，
        而这里失败就是"没有 hooks 目录"这一个确定语义，用返回码判断更直白。
        """
        d = os.path.join(_ROOT, ".git", "hooks")
        if os.path.isdir(d):
            return d
        p = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                           cwd=_ROOT, text=True, capture_output=True)
        if p.returncode != 0:
            return ""
        out = (p.stdout or "").strip()
        if out and not os.path.isabs(out):
            out = os.path.join(_ROOT, out)
        return out if os.path.isdir(out) else ""

    def test_20_check_passes_on_this_repo(self):
        if not os.path.isfile(_INSTALLER):
            self.skipTest("安装脚本不存在")
        if not self._hooks_dir():
            self.skipTest("无 .git/hooks（干净克隆/CI，按设计跳过）")
        p = subprocess.run([sys.executable, _INSTALLER, "--check"],
                           capture_output=True, text=True, cwd=_ROOT)
        self.assertEqual(p.returncode, 0,
                         "--check 未通过（模板与实装不同步）：\n%s\n%s"
                         % (p.stdout, p.stderr))

    def test_21_installed_hook_contains_section6(self):
        d = self._hooks_dir()
        if not d:
            self.skipTest("无 .git/hooks（干净克隆/CI，按设计跳过）")
        inst = os.path.join(d, "pre-commit")
        if not os.path.isfile(inst):
            self.skipTest("实装 hook 缺失（未安装，非本批回归）")
        body = io.open(inst, encoding="utf-8", errors="replace").read()
        self.assertIn(SEC6_HEAD, body, "实装 hook 里没有第6段 —— 忘了跑 install")


if __name__ == "__main__":
    unittest.main(verbosity=2)
