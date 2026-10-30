#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""405 刀5 判据：hook 模板入库一致性（install_precommit_hook.py --check）。

## 这一刀要防的是什么（别改成"只测 happy path"）

404 刀3 往**本机** `.git/hooks/pre-commit` 加了 30 行「七层自检」，却没同步回
`tools/ci/hooks/pre-commit`。旧版安装脚本只会「模板 → 实装」单向覆盖，而且没人会
无故重跑它 ⇒ 那一步**五天没进版本库**（模板 9939 B vs 实装 11434 B）。
更糟的是那 30 行里写着「本 hook 未纳入版本管理」这句错话，把缺口伪装成"已知限制"。

⇒ 单靠单向安装永远发现不了「实装比模板新」。必须有一个只读比对入口把这件事判红。
   `--check` 就是它，本文件守的就是它。

## 判据设计原则

- 不依赖真实仓库状态（干净 clone 里 `.git/hooks/` 可能为空/是 sample）⇒
  一律用临时目录伪造 TEMPLATE_DIR / hooks 目录；
- 断言钉**语义**不钉措辞（不锁具体字节数，只锁"退出码 / 状态词 / 是否写盘"）；
- 每条用例的清理都走 `_purge_dir`（内部 `silent_exc` 留痕），不写 `except OSError: pass`
  —— `tests/` 同样受 cw2 静默 except 门监管。
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

_SPEC = os.path.join(ROOT, "tools", "ci", "install_precommit_hook.py")


def _load():
    spec = importlib.util.spec_from_file_location("m405_k5_install", _SPEC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load()


def _purge_dir(path):
    """删掉临时目录；失败只留痕不上抛（收尾清理不该让用例变红）。"""
    if not path or not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
    except OSError as e:
        silent_exc(e, where="test_m405_k5._purge_dir", level="debug")


class _Base(unittest.TestCase):
    """伪造一份「模板目录 + 实装目录」，把模块级常量指向临时区。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m405k5_")
        self.tpl = os.path.join(self._tmp, "hooks")
        self.dst = os.path.join(self._tmp, "git_hooks")
        os.makedirs(self.tpl)
        os.makedirs(self.dst)
        self._patch = mock.patch.multiple(
            H, TEMPLATE_DIR=self.tpl, HOOKS_DIR=self.dst)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        _purge_dir(self._tmp)

    # --- 造样本 ---
    def _write(self, path, content):
        with open(path, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))

    def _tpl(self, name, content):
        self._write(os.path.join(self.tpl, name), content)

    def _dst(self, name, content):
        self._write(os.path.join(self.dst, name), content)

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()


# ---------------------------------------------------------------------------
# 1) 纯函数：_shape / _compare 的三态判定
# ---------------------------------------------------------------------------
class TestCompareStates(_Base):

    def test_01_same_when_bytes_equal(self):
        self._tpl("pre-commit", "#!/bin/sh\nexit 0\n")
        self._dst("pre-commit", "#!/bin/sh\nexit 0\n")
        state, detail, diff = H._compare("pre-commit", self.dst)
        self.assertEqual(state, "same")
        self.assertEqual(diff, [])
        # detail 必须给出可读体量（人排查时要看得到多大）
        self.assertTrue(any("行" in d and "B" in d for d in detail))

    def test_02_stale_when_installed_is_newer(self):
        """★本刀要防的那个真实形态：实装比模板多内容。"""
        self._tpl("pre-commit", "#!/bin/sh\nexit 0\n")
        self._dst("pre-commit", "#!/bin/sh\necho extra\nexit 0\n")
        state, detail, _ = H._compare("pre-commit", self.dst)
        self.assertEqual(state, "stale")
        self.assertIn("实装", "".join(detail))

    def test_03_stale_when_template_is_newer(self):
        self._tpl("pre-commit", "#!/bin/sh\necho new\nexit 0\n")
        self._dst("pre-commit", "#!/bin/sh\nexit 0\n")
        state, _, _ = H._compare("pre-commit", self.dst)
        self.assertEqual(state, "stale")

    def test_04_missing_when_not_installed(self):
        self._tpl("pre-commit", "#!/bin/sh\n")
        state, detail, _ = H._compare("pre-commit", self.dst)
        self.assertEqual(state, "missing")
        self.assertIn("安装", "".join(detail))

    def test_05_no_template_is_distinct_from_missing(self):
        """模板缺失与实装缺失必须可分辨 —— 否则会误报"跑一遍装一下就好"。"""
        self._dst("pre-commit", "#!/bin/sh\n")
        state, _, _ = H._compare("pre-commit", self.dst)
        self.assertEqual(state, "no_template")

    def test_06_diff_is_unified_and_names_both_sides(self):
        self._tpl("pre-commit", "#!/bin/sh\nAAA\nexit 0\n")
        self._dst("pre-commit", "#!/bin/sh\nBBB\nexit 0\n")
        _, _, diff = H._compare("pre-commit", self.dst)
        text = "".join(diff)
        self.assertIn("---", text)
        self.assertIn("+++", text)
        self.assertIn("-AAA", text)
        self.assertIn("+BBB", text)
        # 两侧身份要写清（模板 / 实装），不然看 diff 不知道谁是谁
        self.assertIn("模板", text)
        self.assertIn("实装", text)

    def test_07_crlf_in_template_is_flagged(self):
        """hook 模板含 CRLF 要在 detail 里点名 —— git 以 sh 调用，CR 会让解释器名变 'sh\\r'。"""
        self._tpl("pre-commit", "#!/bin/sh\r\nexit 0\r\n")
        self._dst("pre-commit", "#!/bin/sh\nexit 0\n")
        _, detail, _ = H._compare("pre-commit", self.dst)
        self.assertTrue(any("CRLF" in d for d in detail))


# ---------------------------------------------------------------------------
# 2) CLI 退出码契约（能挂进任何例行检查的关键）
# ---------------------------------------------------------------------------
class TestCliExitCodes(_Base):

    def _main(self, argv):
        with mock.patch.object(H, "_root", lambda: self._tmp), \
                mock.patch.object(H, "_hooks_dir", lambda root: self.dst):
            return H.main(argv)

    def test_10_check_returns_zero_when_aligned(self):
        for n in H.HOOKS:
            self._tpl(n, "#!/bin/sh\nexit 0\n")
            self._dst(n, "#!/bin/sh\nexit 0\n")
        self.assertEqual(self._main(["--check"]), 0)

    def test_11_check_returns_one_when_stale(self):
        self._tpl("pre-commit", "#!/bin/sh\nexit 0\n")
        self._dst("pre-commit", "#!/bin/sh\necho extra\nexit 0\n")
        self._tpl("commit-msg", "#!/bin/sh\n")
        self._dst("commit-msg", "#!/bin/sh\n")
        self.assertEqual(self._main(["--check"]), 1)

    def test_12_check_does_not_write_anything(self):
        """--check 必须只读 —— 写盘就把"校验"变成"安装"了。"""
        self._tpl("pre-commit", "#!/bin/sh\nTEMPLATE\n")
        self._dst("pre-commit", "#!/bin/sh\nINSTALLED\n")
        before_tpl = self._read(os.path.join(self.tpl, "pre-commit"))
        before_dst = self._read(os.path.join(self.dst, "pre-commit"))
        self._main(["--check"])
        self.assertEqual(self._read(os.path.join(self.tpl, "pre-commit")), before_tpl)
        self.assertEqual(self._read(os.path.join(self.dst, "pre-commit")), before_dst)

    def test_13_diff_alone_is_rejected(self):
        """--diff 只在 --check 下有意义，单独用应报错而不是静默忽略。"""
        with self.assertRaises(SystemExit) as cm:
            self._main(["--diff"])
        self.assertEqual(cm.exception.code, 2)

    def test_14_unknown_hook_name_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self._main(["--check", "post-merge"])
        self.assertEqual(cm.exception.code, 2)

    def test_15_unknown_option_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self._main(["--force"])
        self.assertEqual(cm.exception.code, 2)

    def test_16_hooks_dir_absent_returns_two(self):
        """hooks 目录不存在属环境错误（2），不能报成"不一致"（1）—— 两者处置方式不同。"""
        with mock.patch.object(H, "_root", lambda: self._tmp), \
                mock.patch.object(H, "_hooks_dir",
                                  lambda root: os.path.join(self._tmp, "nope")):
            self.assertEqual(H.main(["--check"]), 2)

    def test_17_check_single_hook_by_name(self):
        self._tpl("pre-commit", "#!/bin/sh\nA\n")
        self._dst("pre-commit", "#!/bin/sh\nA\n")
        # commit-msg 压根不存在，但只查 pre-commit ⇒ 应为 PASS
        self.assertEqual(self._main(["--check", "pre-commit"]), 0)


# ---------------------------------------------------------------------------
# 3) 安装：幂等 + 补可执行位 + 不碰无关 hook
# ---------------------------------------------------------------------------
class TestInstall(_Base):

    def _main(self, argv):
        with mock.patch.object(H, "_root", lambda: self._tmp), \
                mock.patch.object(H, "_hooks_dir", lambda root: self.dst):
            return H.main(argv)

    def test_20_install_copies_template(self):
        self._tpl("pre-commit", "#!/bin/sh\nBODY\n")
        self._tpl("commit-msg", "#!/bin/sh\nMSG\n")
        self.assertEqual(self._main([]), 0)
        self.assertEqual(self._read(os.path.join(self.dst, "pre-commit")),
                         self._read(os.path.join(self.tpl, "pre-commit")))

    def test_21_install_is_idempotent(self):
        self._tpl("pre-commit", "#!/bin/sh\nBODY\n")
        self._tpl("commit-msg", "#!/bin/sh\nMSG\n")
        self.assertEqual(self._main([]), 0)
        first = self._read(os.path.join(self.dst, "pre-commit"))
        mtime_first = os.stat(os.path.join(self.dst, "pre-commit")).st_mtime_ns
        self.assertEqual(self._main([]), 0)
        self.assertEqual(self._read(os.path.join(self.dst, "pre-commit")), first)
        # 跳过写入 ⇒ mtime 不该变（"已是最新"必须真的不写盘）
        self.assertEqual(
            os.stat(os.path.join(self.dst, "pre-commit")).st_mtime_ns, mtime_first)

    def test_22_install_survives_chmod_failure(self):
        """chmod 失败不许让安装失败。

        ★为什么这样测而不是断言 st_mode 含执行位：Windows 上 `os.chmod` 只认只读位，
          执行位是 no-op（Python 文档明写"All other bits are ignored"）⇒ 锁执行位等于
          锁平台策略，在 Windows 上必然红。这里改用「chmod 抛异常时安装仍成功」——
          那才是这段 try/except 存在的真正语义（权限受限/文件系统不支持时别把整次安装搞挂）。
        """
        self._tpl("pre-commit", "#!/bin/sh\n")
        self._tpl("commit-msg", "#!/bin/sh\n")
        with mock.patch.object(H.os, "chmod", side_effect=OSError("no perm")):
            self.assertEqual(self._main([]), 0)
        # 内容仍然落盘（chmod 失败不该回滚复制）
        self.assertEqual(self._read(os.path.join(self.dst, "pre-commit")),
                         self._read(os.path.join(self.tpl, "pre-commit")))

    @unittest.skipIf(os.name == "nt", "Windows 的 chmod 不认执行位（no-op）")
    def test_22b_install_sets_executable_bit_posix(self):
        self._tpl("pre-commit", "#!/bin/sh\n")
        self._tpl("commit-msg", "#!/bin/sh\n")
        self._main([])
        mode = os.stat(os.path.join(self.dst, "pre-commit")).st_mode
        self.assertTrue(mode & 0o100, "属主可执行位未置上")

    def test_23_install_fails_when_template_missing(self):
        self._tpl("commit-msg", "#!/bin/sh\n")
        # pre-commit 模板缺失 ⇒ 安装失败
        self.assertEqual(self._main([]), 1)

    def test_24_install_leaves_unrelated_hooks_alone(self):
        """只装 HOOKS 清单里的，不许顺手改动 pre-push 之类的既有 hook。"""
        self._tpl("pre-commit", "#!/bin/sh\n")
        self._tpl("commit-msg", "#!/bin/sh\n")
        other = os.path.join(self.dst, "pre-push")
        self._write(other, "#!/bin/sh\nKEEP ME\n")
        self._main([])
        self.assertEqual(self._read(other), b"#!/bin/sh\nKEEP ME\n")


# ---------------------------------------------------------------------------
# 4) 仓库内模板本身的硬约束（防「改模板时不小心把 CRLF 带进去」）
# ---------------------------------------------------------------------------
class TestRepoTemplateHygiene(unittest.TestCase):

    def test_30_real_templates_are_lf_only(self):
        for n in H.HOOKS:
            p = os.path.join(ROOT, "tools", "ci", "hooks", n)
            self.assertTrue(os.path.isfile(p), "模板缺失：%s" % p)
            with open(p, "rb") as f:
                b = f.read()
            self.assertNotIn(b"\r\n", b, "%s 含 CRLF，hook 模板必须纯 LF" % n)
            self.assertTrue(b.startswith(b"#!"), "%s 缺 shebang" % n)

    def test_31_repo_has_no_install_hooks_py_duplicate(self):
        """任务书刀5 原名是 install_hooks.py，但仓里已有 install_precommit_hook.py。

        不许出现两个平行安装脚本 —— 两份"安装源"迟早各自演化，
        又一次变成"改了这边没改那边"。若要改名，必须删旧的、不留并存。
        """
        dup = os.path.join(ROOT, "tools", "ci", "install_hooks.py")
        self.assertFalse(os.path.exists(dup),
                         "出现平行安装脚本 tools/ci/install_hooks.py，"
                         "请合并到 install_precommit_hook.py 而不是并存")


if __name__ == "__main__":
    unittest.main()
