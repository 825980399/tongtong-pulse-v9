# -*- coding: utf-8 -*-
"""★479 全面核查发现：语义桥 sidecar 成为孤儿（父进程死后不退出）。

## 实测现象

    主进程 pid=39696（23:44:09）
    sidecar pid=38772 PPID=39696  RSS=423MB  ← 正常
    sidecar pid=19600 PPID=40900  RSS=189MB  ← 父进程已死，孤儿
    sidecar pid=28324 PPID=17824  RSS= 87MB  ← 测试残留
    合计 **699MB**

每次重启框架都留一个 200~400MB 的 sidecar，长期累积吃光内存。

## 根因

`Popen(..., stdin=subprocess.DEVNULL)` ⇒ 父子无任何存活性关联；
HTTP 模式的 sidecar 只在 `serve_forever()` 里，**没有父进程检测**。

## 改法与两个 Windows 坑

1. client spawn 时通过 `PULSE_SIDECAR_PPID` 传本进程 PID；
2. sidecar 起 daemon 线程每 `_PPID_WATCH_SEC`（默认 30s，可用
   `PULSE_SIDECAR_PPID_WATCH_SEC` 调小）探一次父进程；不在 ⇒ `os._exit(0)`；
3. **坑一**：Windows 上 `OpenProcess` 对「已退出但句柄未释放」的僵尸进程
   仍能打开 ⇒ 只看句柄会把死进程判活的。必须再查 `GetExitCodeProcess`
   是否 `STILL_ACTIVE(259)`；
4. **坑二**：验证时用 `Popen.poll()` 判进程状态，别用自己写的 OpenProcess 包装
   （同一坑会污染测试结论 —— 第一版就因此误判成「看护没生效」，
   实际 stderr 已经打出「父进程已退出 ⇒ 自行结束」）。

## 端到端实测

临时父进程 spawn sidecar 后立刻退出，看护间隔 1s：
    ✓ sidecar 在 2 秒后自行结束（rc=0）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, "tools", "semantic"))

import encoder_sidecar as ES


class TestPidAlive(unittest.TestCase):
    """探活判据本身必须两个方向都对（否则看护形同虚设或误杀）。"""

    def test_01_current_process_alive(self):
        self.assertTrue(ES._pid_alive(os.getpid()))

    def test_02_missing_pid_dead(self):
        # 一个几乎不可能存在的 PID
        self.assertFalse(ES._pid_alive(999999))

    def test_03_zero_treated_alive(self):
        """pid<=0 视为「判不了」⇒ 返回 True（安全侧，绝不误杀）。"""
        self.assertTrue(ES._pid_alive(0))
        self.assertTrue(ES._pid_alive(-1))

    def test_04_zombie_detected_dead(self):
        """★核心：已退出但句柄未释放的子进程必须判「死」。

        这是第一版失败的原因：只查 OpenProcess 句柄，僵尸进程返回非 NULL
        ⇒ 被判活 ⇒ 看护永不触发。
        """
        import subprocess
        _p = subprocess.Popen([sys.executable, "-c", "pass"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
        try:
            _p.wait(timeout=30)      # 回收，但 Popen 仍持有句柄
            self.assertEqual(_p.returncode, 0)
            self.assertFalse(ES._pid_alive(_p.pid),
                             "僵尸进程被判活 ⇒ 看护永不触发")
        finally:
            if _p.poll() is None:
                _p.kill()


class TestWatchParentSource(unittest.TestCase):
    """结构防回潮。"""

    def test_10_watch_enabled_before_serve(self):
        _p = os.path.join(ROOT, "tools", "semantic", "encoder_sidecar.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _i = _src.index("def _serve_http")
        _seg = _src[_i:_i + 1600]
        self.assertIn("_watch_parent()", _seg, "看护没接进 _serve_http")

    def test_11_uses_exit_code_not_just_handle(self):
        _p = os.path.join(ROOT, "tools", "semantic", "encoder_sidecar.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        self.assertIn("GetExitCodeProcess", _src, "只看句柄 ⇒ 僵尸会被判活")
        self.assertIn("259", _src, "缺 STILL_ACTIVE 判据")

    def test_12_client_passes_ppid(self):
        _p = os.path.join(ROOT, "nucleus", "semantic", "bridge_encoder.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        self.assertIn("PULSE_SIDECAR_PPID", _src, "client 没把 PID 传下去")
        self.assertIn("env=_env", _src)

    def test_13_no_ppid_env_means_disabled(self):
        """没设 PULSE_SIDECAR_PPID（手工调试）⇒ 不启用看护，行为与改前一致。"""
        _old = os.environ.pop("PULSE_SIDECAR_PPID", None)
        try:
            # 不抛即通过；关键是不能因为缺变量而报错
            ES._watch_parent()
        finally:
            if _old is not None:
                os.environ["PULSE_SIDECAR_PPID"] = _old


if __name__ == "__main__":
    unittest.main()
